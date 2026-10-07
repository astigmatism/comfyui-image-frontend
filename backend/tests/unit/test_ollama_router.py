"""Prompt Assistant model selection and router error handling (docs/llm-router-contract.md).

The adapter chooses the model for every request from the router's capabilities document: the
most capable NSFW model first, then the most capable model (CIF_OLLAMA_NSFW=prefer), or a named
service with fallbacks (CIF_OLLAMA_SELECTION=named). These tests drive it through httpx's
MockTransport with documents shaped like the router's.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from app.config import Settings
from app.errors import AppError
from app.services.llm_router import CLIENT_NAME
from app.services.ollama import OllamaAdapter
from tests.router_fixtures import (
    CAPABILITIES_PATH,
    capabilities_response,
    paired_document,
    router_document,
    router_error,
    router_model,
    solo_document,
)

PROMPT = '{"prompt":"A quiet lake illuminated by moonlight."}'


def _ok(model: str = "canonical-model", content: str = PROMPT) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "message": {"content": content, "thinking": "considered it"},
            "done": True,
            "done_reason": "stop",
        },
    )


class Router:
    """A scripted router: a mutable document and a queue of chat responses."""

    def __init__(self, document: dict[str, Any] | None = None) -> None:
        self.document = document if document is not None else paired_document()
        self.chat: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.paths: list[str] = []
        self.responses: list[httpx.Response | Callable[[dict[str, Any]], httpx.Response]] = []
        self.capabilities_failure: Exception | None = None
        self.on_capabilities: Callable[[], None] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        self.headers.append(request.headers)
        if request.url.path == CAPABILITIES_PATH:
            if self.capabilities_failure is not None:
                raise self.capabilities_failure
            if self.on_capabilities is not None:
                self.on_capabilities()
            return capabilities_response(request, self.document)
        assert request.url.path == "/api/chat", f"unexpected request {request.url.path}"
        payload = json.loads(request.content)
        self.chat.append(payload)
        if self.responses:
            response = self.responses.pop(0)
            return response(payload) if callable(response) else response
        return _ok()

    @property
    def models(self) -> list[str]:
        return [payload["model"] for payload in self.chat]


async def _no_wait(_: float) -> None:
    return None


def _adapter(router: Router, **settings: Any) -> tuple[OllamaAdapter, list[float], list[float]]:
    retry_waits: list[float] = []
    router_waits: list[float] = []

    async def retry_sleeper(seconds: float) -> None:
        retry_waits.append(seconds)

    async def router_waiter(seconds: float) -> None:
        router_waits.append(seconds)

    adapter = OllamaAdapter(
        Settings(test_mode=True, ollama_base_url="http://router.test", **settings),
        transport=httpx.MockTransport(router.handler),
        retry_sleeper=retry_sleeper,
        router_waiter=router_waiter,
        seed_resolver=lambda minimum, maximum: 7,
    )
    return adapter, retry_waits, router_waits


async def _compose(adapter: OllamaAdapter, **options: Any):
    return await adapter.compose(
        mode=options.pop("mode", "create"),
        prompt=options.pop("prompt", ""),
        direction=options.pop("direction", "a moonlit lake"),
        **options,
    )


# ---------- selection ----------


async def test_paired_configuration_uses_nighttime() -> None:
    router = Router(paired_document())
    adapter, _, _ = _adapter(router)
    try:
        result = await _compose(adapter)
        available, message = await adapter.status()
    finally:
        await adapter.close()
    assert router.models == ["nighttime"]
    assert result.service == "nighttime" and result.fallback is False and result.nsfw is True
    assert result.raw_response["router"]["reason"] == "most_capable_nsfw"
    assert (available, message) == (True, None)
    # No per-call /api/tags probe: only the capabilities document and the chat request.
    assert "/api/tags" not in router.paths


async def test_solo_configuration_uses_daytime_and_reports_the_fallback() -> None:
    router = Router(solo_document())
    adapter, _, _ = _adapter(router)
    try:
        result = await _compose(adapter)
        capabilities = await adapter.capabilities()
    finally:
        await adapter.close()
    assert router.models == ["daytime"]
    assert result.service == "daytime" and result.fallback is True and result.nsfw is False
    status = capabilities["router"]
    assert status["service"] == "daytime"
    assert status["nsfw"] is False
    assert status["fallback"] is True
    assert status["reason"] == "no_nsfw_model_available"
    assert status["configuration_id"] == "fake-solo"
    assert status["notice"].startswith("No NSFW model is available")


async def test_unhealthy_nsfw_model_falls_back_to_daytime() -> None:
    router = Router(paired_document(unavailable={"nighttime"}))
    adapter, _, _ = _adapter(router)
    try:
        result = await _compose(adapter)
    finally:
        await adapter.close()
    assert router.models == ["daytime"]
    assert result.fallback is True


async def test_two_nsfw_models_use_the_higher_score() -> None:
    router = Router(
        router_document(
            [
                router_model("daytime", nsfw=False, score=90.0),
                router_model("nighttime", nsfw=True, score=64.9),
                router_model("midnight", nsfw=True, score=72.0),
            ]
        )
    )
    adapter, _, _ = _adapter(router)
    try:
        await _compose(adapter)
    finally:
        await adapter.close()
    assert router.models == ["midnight"]


async def test_require_nsfw_with_none_available_is_unavailable_without_sending() -> None:
    router = Router(solo_document())
    adapter, _, _ = _adapter(router, ollama_nsfw="require")
    try:
        with pytest.raises(AppError) as error:
            await _compose(adapter)
        available, message = await adapter.status()
    finally:
        await adapter.close()
    assert error.value.code == "ollama_unavailable"
    assert error.value.status_code == 503
    assert "requires an NSFW model" in error.value.message
    assert error.value.message.endswith("Manual prompting still works.")
    assert router.chat == []
    assert available is False and message and "NSFW" in message


@pytest.mark.parametrize(
    ("preference", "expected"), [("avoid", "daytime"), ("any", "daytime"), ("prefer", "nighttime")]
)
async def test_nsfw_preference_settings(preference: str, expected: str) -> None:
    router = Router(paired_document())
    adapter, _, _ = _adapter(router, ollama_nsfw=preference)
    try:
        result = await _compose(adapter)
    finally:
        await adapter.close()
    assert router.models == [expected]
    assert result.fallback is False


async def test_named_mode_sends_the_preferred_service_then_its_fallbacks() -> None:
    router = Router(paired_document())
    adapter, _, _ = _adapter(router, ollama_selection="named")
    try:
        assert (await _compose(adapter)).service == "nighttime"
        router.document = solo_document()
        result = await _compose(adapter)
        capabilities = await adapter.capabilities()
    finally:
        await adapter.close()
    assert router.models == ["nighttime", "daytime"]
    assert result.fallback is True
    assert capabilities["router"]["reason"] == "named_fallback"
    assert capabilities["router"]["notice"].startswith("Nighttime is unavailable")


async def test_named_mode_with_fallback_disabled_reports_unavailable() -> None:
    router = Router(solo_document())
    adapter, _, _ = _adapter(router, ollama_selection="named", ollama_fallback_models="")
    try:
        with pytest.raises(AppError) as error:
            await _compose(adapter)
    finally:
        await adapter.close()
    assert error.value.code == "ollama_unavailable"
    assert "(nighttime)" in error.value.message
    assert router.chat == []


def test_named_mode_requires_a_service_name() -> None:
    with pytest.raises(ValueError, match="CIF_OLLAMA_SELECTION=named"):
        Settings(
            test_mode=True,
            ollama_base_url="http://router.test",
            ollama_selection="named",
            ollama_model="",
            ollama_fallback_models="",
        )
    settings = Settings(
        test_mode=True,
        ollama_base_url="http://router.test",
        ollama_selection=" NAMED ",
        ollama_nsfw="Avoid",
        ollama_fallback_models="daytime, nighttime, daytime",
    )
    assert settings.ollama_selection == "named"
    assert settings.ollama_nsfw == "avoid"
    assert settings.ollama_named_models == ("nighttime", "daytime")
    with pytest.raises(ValueError):
        Settings(test_mode=True, ollama_router_wait_seconds=60)


async def test_draining_waits_without_switching_then_chooses_again() -> None:
    router = Router(paired_document(draining=True))
    waits: list[float] = []

    async def waiter(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 3:
            # The switch finishes in a solo configuration.
            router.document = solo_document()

    adapter, _, _ = _adapter(router)
    adapter.router_waiter = waiter
    try:
        result = await _compose(adapter)
    finally:
        await adapter.close()
    assert waits == [2.0, 4.0, 8.0]
    # Nothing was sent while draining, and no fallback was chosen during it.
    assert router.models == ["daytime"]
    assert result.service == "daytime"


async def test_draining_status_reports_waiting() -> None:
    router = Router(paired_document(draining=True))
    adapter, _, _ = _adapter(router)
    try:
        available, message = await adapter.status()
        capabilities = await adapter.capabilities()
    finally:
        await adapter.close()
    assert available is False
    assert message and "switching configuration" in message
    assert capabilities["router"]["state"] == "waiting"
    assert capabilities["router"]["service"] is None


async def test_the_nsfw_model_is_used_again_as_soon_as_it_returns() -> None:
    router = Router(solo_document())
    adapter, _, _ = _adapter(router)
    try:
        assert (await _compose(adapter)).service == "daytime"
        router.document = paired_document()
        assert (await _compose(adapter)).service == "nighttime"
        assert (await adapter.capabilities())["router"]["fallback"] is False
    finally:
        await adapter.close()
    assert router.models == ["daytime", "nighttime"]


async def test_selection_changes_are_logged_with_their_reason(caplog) -> None:
    router = Router(paired_document())
    adapter, _, _ = _adapter(router)
    try:
        with caplog.at_level(logging.INFO, logger="app.services.ollama"):
            await _compose(adapter)
            await _compose(adapter)
            router.document = solo_document()
            await _compose(adapter)
    finally:
        await adapter.close()
    changes = [
        record
        for record in caplog.records
        if record.message == "llm_router_model_changed" and record.router_purpose == "text"  # type: ignore[attr-defined]
    ]
    assert [(item.router_service, item.router_selection_reason) for item in changes] == [  # type: ignore[attr-defined]
        ("nighttime", "most_capable_nsfw"),
        ("daytime", "no_nsfw_model_available"),
    ]
    assert changes[1].levelno == logging.WARNING
    assert changes[1].router_previous_service == "nighttime"  # type: ignore[attr-defined]


# ---------- errors (contract section 10) ----------


async def test_service_offline_falls_back_at_once_and_costs_no_retries() -> None:
    # The document lags: it still lists Nighttime, but the configuration already stopped it.
    router = Router(paired_document())
    router.responses = [
        router_error("SERVICE_OFFLINE", 503),
        httpx.Response(503, json={"error": "busy"}),
        httpx.Response(503, json={"error": "busy"}),
        _ok(),
    ]

    def current_after_the_first_request() -> None:
        if router.chat:
            router.document = solo_document()

    router.on_capabilities = current_after_the_first_request
    adapter, retry_waits, router_waits = _adapter(router)
    try:
        result = await _compose(adapter, think=False)
    finally:
        await adapter.close()
    assert router.models == ["nighttime", "daytime", "daytime", "daytime"]
    # Two transient retries on Daytime; SERVICE_OFFLINE consumed none of the three attempts.
    assert retry_waits == [0.25, 0.5]
    assert router_waits == []
    assert result.service == "daytime"


@pytest.mark.parametrize("code", ["BACKEND_UNAVAILABLE", "MODEL_NOT_FOUND"])
async def test_unusable_services_fall_back_to_the_next_candidate(code: str, caplog) -> None:
    router = Router(paired_document())
    router.responses = [router_error(code, 404 if code == "MODEL_NOT_FOUND" else 503)]
    adapter, retry_waits, _ = _adapter(router)
    try:
        with caplog.at_level(logging.WARNING):
            result = await _compose(adapter)
    finally:
        await adapter.close()
    assert router.models == ["nighttime", "daytime"]
    assert retry_waits == []
    assert result.service == "daytime" and result.fallback is True
    if code == "MODEL_NOT_FOUND":
        assert any(record.message == "llm_router_model_not_found" for record in caplog.records)


async def test_backend_unavailable_without_a_fallback_retries_with_backoff() -> None:
    router = Router(solo_document())
    router.responses = [router_error("BACKEND_UNAVAILABLE", 503), _ok()]
    adapter, retry_waits, _ = _adapter(router)
    try:
        await _compose(adapter)
    finally:
        await adapter.close()
    assert router.models == ["daytime", "daytime"]
    assert retry_waits == [0.25]


async def test_service_offline_with_nothing_left_is_unavailable() -> None:
    router = Router(solo_document())
    router.responses = [router_error("SERVICE_OFFLINE", 503)]
    adapter, retry_waits, _ = _adapter(router)
    try:
        with pytest.raises(AppError) as error:
            await _compose(adapter)
    finally:
        await adapter.close()
    assert error.value.code == "ollama_unavailable"
    assert retry_waits == []
    assert router.models == ["daytime"]


@pytest.mark.parametrize("code", ["BACKEND_DRAINING", "MAINTENANCE_MODE"])
async def test_drain_and_maintenance_codes_wait_then_choose_again(code: str) -> None:
    router = Router(paired_document())
    router.responses = [router_error(code, 503), router_error(code, 503)]
    waits: list[float] = []

    async def waiter(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            router.document = solo_document()

    adapter, retry_waits, _ = _adapter(router)
    adapter.router_waiter = waiter
    try:
        result = await _compose(adapter)
    finally:
        await adapter.close()
    assert waits == [2.0, 4.0]
    assert retry_waits == []
    # The configuration changed while waiting: the model is chosen again.
    assert router.models == ["nighttime", "nighttime", "daytime"]
    assert result.service == "daytime"


async def test_waiting_lasts_at_least_ten_minutes_with_backoff_up_to_30_seconds() -> None:
    router = Router(paired_document())
    router.responses = [router_error("BACKEND_DRAINING", 503) for _ in range(40)]
    adapter, _, router_waits = _adapter(router)
    try:
        with pytest.raises(AppError) as error:
            await _compose(adapter)
    finally:
        await adapter.close()
    assert error.value.code == "ollama_unavailable"
    assert "still switching configuration" in error.value.message
    assert router_waits[:6] == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert max(router_waits) == 30.0
    assert sum(router_waits) >= 600.0
    assert sum(router_waits[:-1]) < 600.0


async def test_other_server_errors_retry_and_other_client_errors_fail() -> None:
    router = Router(paired_document())
    router.responses = [router_error("UPSTREAM_TIMEOUT", 502), _ok()]
    adapter, retry_waits, _ = _adapter(router)
    try:
        await _compose(adapter)
        assert retry_waits == [0.25]
        router.responses = [router_error("INVALID_SAMPLING_OPTION", 400)]
        with pytest.raises(AppError) as rejected:
            await _compose(adapter)
        router.responses = [router_error("context_length_exceeded", 400)]
        with pytest.raises(AppError) as too_long:
            await _compose(adapter)
    finally:
        await adapter.close()
    assert rejected.value.code == "ollama_generate_rejected"
    assert rejected.value.details["router_code"] == "INVALID_SAMPLING_OPTION"
    assert too_long.value.code == "ollama_context_exceeded"
    assert too_long.value.status_code == 422
    # Neither request error was retried or sent to another model.
    assert router.models == ["nighttime", "nighttime", "nighttime", "nighttime"]


async def test_an_incomplete_answer_is_never_treated_as_complete() -> None:
    incomplete = httpx.Response(
        200,
        json={
            "model": "m",
            "message": {"content": PROMPT},
            "done": True,
            "done_reason": "error",
            "error": "backend stopped",
            "x_router": {"status": "incomplete", "stop_reason": "UPSTREAM_TIMEOUT"},
        },
    )
    router = Router(paired_document())
    router.responses = [incomplete, incomplete, incomplete]
    adapter, retry_waits, _ = _adapter(router)
    try:
        with pytest.raises(AppError) as error:
            await _compose(adapter)
    finally:
        await adapter.close()
    assert error.value.code == "ollama_generate_incomplete"
    assert error.value.details["router_code"] == "UPSTREAM_TIMEOUT"
    assert retry_waits == [0.25, 0.5]


async def test_a_length_limited_router_answer_escalates_instead_of_being_accepted() -> None:
    limited = httpx.Response(
        200,
        json={
            "model": "m",
            # Even text that parses is not a completed answer when the router says incomplete.
            "message": {"content": PROMPT, "thinking": "partial"},
            "done": True,
            "done_reason": "length",
            "x_router": {"status": "incomplete", "stop_reason": "max_output_tokens"},
        },
    )
    router = Router(paired_document())
    router.responses = [limited, _ok()]
    adapter, _, _ = _adapter(router)
    try:
        result = await _compose(adapter)
    finally:
        await adapter.close()
    assert [payload["options"]["num_predict"] for payload in router.chat] == [2048, 4096]
    first = result.raw_response["output_budget_attempt_diagnostics"][0]
    assert first["router_status"] == "incomplete"
    assert first["router_stop_reason"] == "max_output_tokens"


# ---------- refusals from a non-NSFW model ----------


async def test_a_fallback_refusal_is_reported_plainly_and_never_retried() -> None:
    router = Router(solo_document())
    router.responses = [_ok(content='{"prompt":"I\'m sorry, but I can\'t help with that."}')]
    adapter, retry_waits, _ = _adapter(router)
    try:
        with pytest.raises(AppError) as error:
            await _compose(adapter)
    finally:
        await adapter.close()
    assert error.value.code == "ollama_model_declined"
    assert error.value.status_code == 422
    assert error.value.message.startswith(
        "No NSFW model is available; Daytime declined this request."
    )
    assert error.value.details["router"]["service"] == "daytime"
    assert error.value.details["router"]["fallback"] is True
    assert len(router.chat) == 1 and retry_waits == []


@pytest.mark.parametrize("failure", ["schema", "MALFORMED_STRUCTURED_OUTPUT"])
async def test_fallback_output_that_fails_the_schema_is_a_decline(failure: str) -> None:
    router = Router(solo_document())
    router.responses = [
        _ok(content="I will not write that.")
        if failure == "schema"
        else router_error("MALFORMED_STRUCTURED_OUTPUT", 502)
    ]
    adapter, retry_waits, _ = _adapter(router)
    try:
        with pytest.raises(AppError) as error:
            await _compose(adapter)
    finally:
        await adapter.close()
    assert error.value.code == "ollama_model_declined"
    assert len(router.chat) == 1 and retry_waits == []


async def test_named_fallback_refusal_names_the_unavailable_preference() -> None:
    router = Router(solo_document())
    router.responses = [_ok(content='{"prompt":"I cannot help with this request."}')]
    adapter, _, _ = _adapter(router, ollama_selection="named")
    try:
        with pytest.raises(AppError) as error:
            await _compose(adapter)
    finally:
        await adapter.close()
    assert error.value.message.startswith("Nighttime is unavailable; Daytime declined")


async def test_the_nsfw_model_is_not_second_guessed_for_refusal_wording() -> None:
    router = Router(paired_document())
    router.responses = [_ok(content='{"prompt":"I can\'t stop smiling, portrait, warm light"}')]
    adapter, _, _ = _adapter(router)
    try:
        result = await _compose(adapter)
    finally:
        await adapter.close()
    assert result.prompt.startswith("I can't stop smiling")


# ---------- request settings ----------


@pytest.mark.parametrize(
    ("efforts", "think", "expected"),
    [
        (None, True, "xhigh"),
        ({"default": "default", "low": "low", "medium": "medium"}, True, "medium"),
        ({"default": "default", "off": "none"}, True, True),
        (None, False, False),
    ],
)
async def test_thinking_effort_is_checked_against_the_chosen_model(
    efforts: dict[str, str] | None, think: bool, expected: object
) -> None:
    router = Router(router_document([router_model("nighttime", nsfw=True, efforts=efforts)]))
    adapter, _, _ = _adapter(router)
    try:
        result = await _compose(adapter, think=think)
    finally:
        await adapter.close()
    assert router.chat[0]["think"] == expected
    assert result.raw_response["router"]["thinking_effort"] == expected


async def test_a_model_without_thinking_receives_think_false() -> None:
    router = Router(router_document([router_model("nighttime", nsfw=True, thinking=False)]))
    adapter, _, _ = _adapter(router)
    try:
        await _compose(adapter)
    finally:
        await adapter.close()
    assert router.chat[0]["think"] is False


async def test_every_router_request_identifies_the_client_and_sends_service_ids_only() -> None:
    router = Router(paired_document())
    adapter, _, _ = _adapter(router, ollama_api_key="local-only")
    try:
        await _compose(adapter)
        await adapter.status()
    finally:
        await adapter.close()
    assert router.headers
    assert all(headers["x-client-name"] == CLIENT_NAME for headers in router.headers)
    assert CLIENT_NAME == "comfyui-image-frontend"
    assert all(payload["model"] in {"daytime", "nighttime"} for payload in router.chat)
    # stream is always explicit; the seed and schema are unchanged.
    assert router.chat[0]["stream"] is False
    assert router.chat[0]["format"]["required"] == ["prompt"]


async def test_router_unreachable_reports_unavailable_without_sending() -> None:
    router = Router(paired_document())
    router.capabilities_failure = httpx.ConnectError("router down")
    adapter, _, _ = _adapter(router)
    try:
        available, message = await adapter.status()
        with pytest.raises(AppError) as error:
            await _compose(adapter)
        router.capabilities_failure = None
        assert (await adapter.status())[0] is True
    finally:
        await adapter.close()
    assert available is False
    assert message and "capabilities could not be read" in message
    assert error.value.code == "ollama_unavailable"
    assert router.chat == []


@pytest.mark.parametrize("slots", [1, 2])
async def test_requests_to_one_service_never_exceed_its_slots(slots: int) -> None:
    document = router_document([router_model("nighttime", nsfw=True, slots=slots)])
    sent: list[dict[str, Any]] = []
    in_flight = 0
    peak = 0

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            if request.url.path == CAPABILITIES_PATH:
                return capabilities_response(request, document)
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            sent.append(json.loads(request.content))
            return _ok()

    adapter = OllamaAdapter(
        Settings(test_mode=True, ollama_base_url="http://router.test"),
        transport=SlowTransport(),
    )
    try:
        await asyncio.gather(*(_compose(adapter, direction=f"lake {n}") for n in range(3)))
    finally:
        await adapter.close()
    assert peak == slots
    assert len(sent) == 3


async def test_the_output_limit_fits_the_serving_models_context_window() -> None:
    # A small window: the reserve and the request's input leave less than the 2048 allowance.
    small = router_document([router_model("nighttime", nsfw=True, context_window=2_400)])
    router = Router(small)
    adapter, _, _ = _adapter(router)
    try:
        await _compose(adapter)
        router.document = paired_document()  # a normal window: the allowance is unchanged
        await _compose(adapter, direction="another lake")
    finally:
        await adapter.close()
    limited, normal = (payload["options"]["num_predict"] for payload in router.chat)
    assert 1 <= limited < 2048
    assert limited <= 2_400 - 1024
    assert normal == 2048


def test_the_read_timeout_allows_for_queueing_behind_long_generations() -> None:
    # The router has no queue deadline; a request may wait behind long Daytime generations.
    adapter = OllamaAdapter(Settings(test_mode=True, ollama_base_url="http://router.test"))
    assert adapter._client is not None
    assert adapter._client.timeout.read == 900.0
    assert adapter._client.headers["X-Client-Name"] == CLIENT_NAME


def test_the_contract_is_vendored_verbatim_with_a_conformance_map_and_agent_rule() -> None:
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    text = (root / "docs" / "llm-router-contract.md").read_text(encoding="utf-8")
    header, _, rest = text.partition("\n\n")
    assert header.startswith(
        "> **Vendored copy — do not edit.** LLM Router client contract, version 1"
    )
    assert "d5edba88089070e82bb72600e860fd204df88481" in header
    vendored, marker, conformance = rest.partition(
        "\n## How ComfyUI Image Frontend upholds this contract\n"
    )
    assert marker, "the conformance map must follow the vendored text"
    # The contract text is byte-for-byte the router maintainer's version 1 at d5edba8.
    assert hashlib.sha256(vendored.encode("utf-8")).hexdigest() == (
        "5314c4ac4ec09e3bbe51816883077c28cff2c4538a09a64fa71961cbb432121f"
    )
    for item in range(1, 12):
        assert f"| {item} |" in conformance
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "must uphold [`docs/llm-router-contract.md`]" in agents
    assert "Never edit the vendored contract text" in agents
