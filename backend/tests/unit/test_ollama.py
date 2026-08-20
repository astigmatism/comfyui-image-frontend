from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
from app.config import Settings
from app.errors import AppError
from app.services.ollama import (
    OUTPUT_TOKEN_BUDGETS,
    OllamaAdapter,
    _extract_prompt,
    _generate_payload,
    _has_thinking_output,
    _instruction,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        session_secret="test-session-secret-material-0123456789",
        ollama_base_url="http://ollama.test",
        test_mode=True,
    )


def test_refine_instruction_only_defines_the_outcome_and_inputs() -> None:
    current = (
        "Two scarlet macaws on a rain-dark branch, one facing left, 85mm lens, "
        "shallow depth of field, no flowers"
    )
    direction = "Change the rain to snow."

    instruction = _instruction(mode="refine", prompt=current, direction=direction)

    assert instruction == (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. "
        "Refine the current prompt according to the creative direction.\n\n"
        f"Current prompt:\n{current}\n\nCreative direction:\n{direction}"
    )


def test_create_instruction_only_defines_the_outcome_and_direction() -> None:
    instruction = _instruction(
        mode="create",
        prompt="this existing prompt is deliberately irrelevant",
        direction="a ceramic robot",
    )

    assert instruction == (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. "
        "Create one complete, polished, directly usable image prompt from this creative "
        "direction:\n\na ceramic robot"
    )


def test_extract_prompt_supports_structured_and_plain_text_responses() -> None:
    assert _extract_prompt(json.dumps({"prompt": "  a detailed scene  "})) == "a detailed scene"
    assert _extract_prompt('```json\n{"prompt": "a fenced response"}\n```') == ("a fenced response")
    assert _extract_prompt("  a plain response  ") == "a plain response"


def test_thinking_output_must_be_a_nonempty_string() -> None:
    assert _has_thinking_output({"thinking": "considered the constraints"}) is True
    assert _has_thinking_output({"thinking": "  "}) is False
    assert _has_thinking_output({"response": '{"prompt":"portrait"}'}) is False


def test_duplicate_create_retry_changes_sampling_without_adding_instructions() -> None:
    instruction = _instruction(mode="create", prompt="", direction="a ceramic robot")
    payload = _generate_payload(mode="create", instruction=instruction, attempt=1, seed=90210)

    assert payload["prompt"] == instruction
    assert payload["options"] == {
        "temperature": 0.7,
        "seed": 90210,
        "num_predict": OUTPUT_TOKEN_BUDGETS[0],
    }
    assert payload["think"] is True


def test_refine_sampling_remains_deterministic() -> None:
    payload = _generate_payload(mode="refine", instruction="refine this", attempt=2)

    assert payload["options"] == {
        "temperature": 0.1,
        "seed": 0,
        "num_predict": OUTPUT_TOKEN_BUDGETS[0],
    }


def test_thinking_can_be_disabled_per_request() -> None:
    payload = _generate_payload(
        mode="refine",
        instruction="refine this",
        think=False,
    )

    assert payload["think"] is False


def test_response_only_structured_output_is_accepted_with_a_capability_warning(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(
                200,
                json={
                    "model": "response-only-model",
                    "response": json.dumps({"prompt": "a portrait in warm window light"}),
                    "done": True,
                    "done_reason": "stop",
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(_settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            result = await adapter.compose(
                mode="refine",
                prompt="a portrait",
                direction="use warm window light",
            )
        finally:
            await adapter.close()

        assert result.prompt == "a portrait in warm window light"
        assert result.model == "response-only-model"
        assert result.raw_response == {
            "model": "response-only-model",
            "status": 200,
            "field_presence": {"response": True, "thinking": False},
            "response_length": len('{"prompt": "a portrait in warm window light"}'),
            "thinking_length": 0,
            "done_reason": "stop",
            "validation_stage": "complete",
            "selected_field": "response",
            "warnings": ["thinking_output_missing"],
            "output_budget_attempts": 1,
            "output_budgets": [OUTPUT_TOKEN_BUDGETS[0]],
            "selected_output_budget_attempt": 1,
            "selected_output_budget": OUTPUT_TOKEN_BUDGETS[0],
        }
        assert "warm window light" not in json.dumps(result.raw_response)

    asyncio.run(scenario())


def test_thinking_only_structured_output_remains_supported(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(
                200,
                json={
                    "model": "thinking-model",
                    "response": "",
                    "thinking": json.dumps({"prompt": "a fox beneath moonlit pines"}),
                    "done": True,
                    "done_reason": "stop",
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(
            _settings(tmp_path),
            transport=httpx.MockTransport(handler),
            seed_resolver=lambda minimum, maximum: 42,
        )
        try:
            result = await adapter.compose(
                mode="create",
                prompt="",
                direction="a fox beneath moonlit pines",
            )
        finally:
            await adapter.close()

        assert result.prompt == "a fox beneath moonlit pines"
        assert result.raw_response["selected_field"] == "thinking"
        assert "warnings" not in result.raw_response

    asyncio.run(scenario())


def test_thinking_create_retries_length_with_only_a_larger_output_budget(
    tmp_path: Path,
) -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            payloads.append(json.loads(request.content))
            if len(payloads) == 1:
                return httpx.Response(
                    200,
                    json={
                        "model": "thinking-model",
                        "response": "",
                        "thinking": "private partial reasoning that is not structured output",
                        "done": True,
                        "done_reason": "length",
                        "eval_count": OUTPUT_TOKEN_BUDGETS[0],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "model": "thinking-model",
                    "response": json.dumps({"prompt": "a fox beneath moonlit pines"}),
                    "thinking": "completed private reasoning",
                    "done": True,
                    "done_reason": "stop",
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(
            _settings(tmp_path),
            transport=httpx.MockTransport(handler),
            seed_resolver=lambda minimum, maximum: 90210,
        )
        try:
            result = await adapter.compose(
                mode="create",
                prompt="old prompt",
                direction="a fox beneath moonlit pines",
                think=True,
            )
        finally:
            await adapter.close()

        assert result.prompt == "a fox beneath moonlit pines"
        assert result.duration_ms >= 0
        assert result.raw_response["output_budget_attempts"] == 2
        assert result.raw_response["output_budgets"] == list(OUTPUT_TOKEN_BUDGETS[:2])
        assert result.raw_response["selected_output_budget_attempt"] == 2
        assert result.raw_response["selected_output_budget"] == OUTPUT_TOKEN_BUDGETS[1]
        assert "private partial reasoning" not in json.dumps(result.raw_response)

    asyncio.run(scenario())

    assert len(payloads) == 2
    first, second = payloads
    assert first["think"] is True
    assert second["think"] is True
    assert first["format"] == second["format"]
    assert first["prompt"] == second["prompt"]
    assert first["options"] == {
        "temperature": 0.5,
        "seed": 90210,
        "num_predict": OUTPUT_TOKEN_BUDGETS[0],
    }
    assert second["options"] == {
        "temperature": 0.5,
        "seed": 90210,
        "num_predict": OUTPUT_TOKEN_BUDGETS[1],
    }


def test_output_budget_retry_does_not_consume_create_distinctness_attempt(
    tmp_path: Path,
) -> None:
    payloads: list[dict[str, object]] = []
    responses = [
        {
            "model": "thinking-model",
            "response": "",
            "thinking": "partial reasoning",
            "done": True,
            "done_reason": "length",
        },
        {
            "model": "thinking-model",
            "response": json.dumps({"prompt": "old prompt"}),
            "thinking": "complete reasoning",
            "done": True,
            "done_reason": "stop",
        },
        {
            "model": "thinking-model",
            "response": json.dumps({"prompt": "a distinct fox portrait"}),
            "thinking": "complete reasoning",
            "done": True,
            "done_reason": "stop",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json=responses.pop(0))
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(
            _settings(tmp_path),
            transport=httpx.MockTransport(handler),
            seed_resolver=lambda minimum, maximum: 500,
        )
        try:
            result = await adapter.compose(
                mode="create",
                prompt="old prompt",
                direction="a fox portrait",
            )
        finally:
            await adapter.close()
        assert result.prompt == "a distinct fox portrait"

    asyncio.run(scenario())

    assert [payload["options"]["num_predict"] for payload in payloads] == [
        OUTPUT_TOKEN_BUDGETS[0],
        OUTPUT_TOKEN_BUDGETS[1],
        OUTPUT_TOKEN_BUDGETS[0],
    ]
    assert [payload["options"]["seed"] for payload in payloads] == [500, 500, 501]
    assert [payload["options"]["temperature"] for payload in payloads] == [0.5, 0.5, 0.7]
    assert len({payload["prompt"] for payload in payloads}) == 1


def test_refine_retries_length_with_deterministic_sampling(tmp_path: Path) -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            payloads.append(json.loads(request.content))
            if len(payloads) == 1:
                return httpx.Response(
                    200,
                    json={
                        "model": "thinking-model",
                        "response": "",
                        "thinking": "partial reasoning",
                        "done_reason": "length",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "model": "thinking-model",
                    "response": "",
                    "thinking": json.dumps({"prompt": "a warmer detailed portrait"}),
                    "done_reason": "stop",
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(_settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            result = await adapter.compose(
                mode="refine",
                prompt="a portrait",
                direction="make it warmer",
            )
        finally:
            await adapter.close()
        assert result.prompt == "a warmer detailed portrait"

    asyncio.run(scenario())

    assert [payload["options"] for payload in payloads] == [
        {"temperature": 0.1, "seed": 0, "num_predict": budget}
        for budget in OUTPUT_TOKEN_BUDGETS[:2]
    ]


def test_complete_structured_prompt_is_accepted_even_when_done_reason_is_length(
    tmp_path: Path,
) -> None:
    generate_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal generate_calls
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            generate_calls += 1
            return httpx.Response(
                200,
                json={
                    "model": "thinking-model",
                    "response": json.dumps({"prompt": "a complete warm portrait"}),
                    "thinking": "reasoning reached a valid answer",
                    "done_reason": "length",
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(_settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            result = await adapter.compose(
                mode="refine",
                prompt="a portrait",
                direction="make it warmer",
            )
        finally:
            await adapter.close()
        assert result.prompt == "a complete warm portrait"
        assert result.raw_response["done_reason"] == "length"
        assert result.raw_response["selected_output_budget_attempt"] == 1

    asyncio.run(scenario())
    assert generate_calls == 1


def test_repeated_length_exhaustion_is_bounded_and_privacy_safe(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    payloads: list[dict[str, object]] = []
    private_reasoning = "private direction and prompt fragments must not be retained"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            payloads.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "model": "thinking-model",
                    "response": "",
                    "thinking": private_reasoning,
                    "done": True,
                    "done_reason": "length",
                    "eval_count": payloads[-1]["options"]["num_predict"],
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(_settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            with (
                caplog.at_level(logging.INFO, logger="app.services.ollama"),
                pytest.raises(AppError) as raised,
            ):
                await adapter.compose(
                    mode="refine",
                    prompt="private original prompt",
                    direction="private creative direction",
                )
        finally:
            await adapter.close()

        error = raised.value
        assert error.code == "ollama_output_budget_exhausted"
        assert error.status_code == 503
        assert error.details["validation_stage"] == "output_budget_exhausted"
        assert error.details["done_reason"] == "length"
        assert error.details["output_budget_attempts"] == len(OUTPUT_TOKEN_BUDGETS)
        assert error.details["output_budgets"] == list(OUTPUT_TOKEN_BUDGETS)
        assert len(error.details["output_budget_attempt_diagnostics"]) == len(OUTPUT_TOKEN_BUDGETS)
        serialized = json.dumps(error.details)
        assert private_reasoning not in serialized
        assert "private original prompt" not in serialized
        assert "private creative direction" not in serialized
        retry_records = [
            record.__dict__
            for record in caplog.records
            if record.getMessage() == "ollama_output_budget_retry"
        ]
        assert len(retry_records) == len(OUTPUT_TOKEN_BUDGETS) - 1
        serialized_logs = json.dumps(retry_records, default=str)
        assert private_reasoning not in serialized_logs
        assert "private original prompt" not in serialized_logs
        assert "private creative direction" not in serialized_logs

    asyncio.run(scenario())
    assert [payload["options"]["num_predict"] for payload in payloads] == list(OUTPUT_TOKEN_BUDGETS)


@pytest.mark.parametrize(
    ("response_text", "thinking_text", "expected_message"),
    [
        ("{not-json", "", "Prompt Assistant returned malformed structured prompt output."),
        ("", "", "Prompt Assistant returned no usable prompt."),
    ],
)
def test_malformed_or_empty_structured_output_is_rejected_with_safe_diagnostics(
    tmp_path: Path,
    response_text: str,
    thinking_text: str,
    expected_message: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(
                200,
                json={
                    "model": "malformed-model",
                    "response": response_text,
                    "thinking": thinking_text,
                    "done": True,
                    "done_reason": "stop",
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(_settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(AppError) as raised:
                await adapter.compose(
                    mode="refine",
                    prompt="a portrait",
                    direction="warm light",
                )
        finally:
            await adapter.close()

        assert raised.value.code == "ollama_invalid_response"
        assert raised.value.message == expected_message
        assert raised.value.details == {
            "model": "malformed-model",
            "status": 200,
            "field_presence": {"response": True, "thinking": True},
            "response_length": len(response_text),
            "thinking_length": len(thinking_text),
            "done_reason": "stop",
            "validation_stage": "structured_prompt",
            "output_budget": OUTPUT_TOKEN_BUDGETS[0],
            "output_budget_attempt": 1,
            "output_budget_attempts": 1,
            "output_budgets": [OUTPUT_TOKEN_BUDGETS[0]],
        }
        if response_text:
            assert response_text not in json.dumps(raised.value.details)

    asyncio.run(scenario())


def test_refine_rejects_normalized_unchanged_output_with_actionable_error(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(
                200,
                json={
                    "model": "active-model",
                    "response": json.dumps({"prompt": "  A   PORTRAIT  "}),
                    "thinking": "considered the request",
                    "done": True,
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(_settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(AppError) as raised:
                await adapter.compose(
                    mode="refine",
                    prompt="a portrait",
                    direction="make it better",
                )
        finally:
            await adapter.close()

        assert raised.value.code == "prompt_refinement_unchanged"
        assert raised.value.status_code == 422
        assert "specific Creative Direction" in raised.value.message
        assert raised.value.details["validation_stage"] == "refinement_comparison"

    asyncio.run(scenario())


def test_read_timeout_is_classified_without_retrying_or_retaining_prompt_text(
    tmp_path: Path,
) -> None:
    generate_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal generate_calls
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            generate_calls += 1
            raise httpx.ReadTimeout("private prompt must not be retained", request=request)
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = OllamaAdapter(_settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(AppError) as raised:
                await adapter.compose(
                    mode="refine",
                    prompt="private prompt must not be retained",
                    direction="warm light",
                )
        finally:
            await adapter.close()

        assert raised.value.code == "ollama_generate_timeout"
        assert raised.value.status_code == 504
        assert raised.value.details["status"] is None
        assert raised.value.details["validation_stage"] == "timeout"
        assert "private prompt" not in json.dumps(raised.value.details)

    asyncio.run(scenario())
    assert generate_calls == 1


def test_malformed_generate_json_is_retried_and_classified(tmp_path: Path) -> None:
    generate_calls = 0
    retry_delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal generate_calls
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "active-model"}]})
        if request.url.path == "/api/generate":
            generate_calls += 1
            return httpx.Response(
                200,
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def record_retry_delay(delay: float) -> None:
        retry_delays.append(delay)

    async def scenario() -> None:
        adapter = OllamaAdapter(
            _settings(tmp_path),
            transport=httpx.MockTransport(handler),
            retry_sleeper=record_retry_delay,
        )
        try:
            with pytest.raises(AppError) as raised:
                await adapter.compose(
                    mode="refine",
                    prompt="a portrait",
                    direction="warm light",
                    think=False,
                )
            assert raised.value.code == "ollama_generate_invalid_json"
            assert raised.value.status_code == 502
            assert raised.value.details == {
                "operation": "generate",
                "failure_kind": "invalid_json",
                "attempts": 3,
                "thinking_enabled": False,
                "upstream_status": 200,
                "model": None,
                "status": 200,
                "field_presence": {"response": False, "thinking": False},
                "response_length": 0,
                "thinking_length": 0,
                "done_reason": None,
                "validation_stage": "invalid_json",
            }
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert generate_calls == 3
    assert retry_delays == [0.25, 0.5]
