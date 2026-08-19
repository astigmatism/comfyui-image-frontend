from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from app.config import Settings
from app.errors import AppError
from app.services.ollama import (
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
    assert payload["options"] == {"temperature": 0.7, "seed": 90210, "num_predict": 512}
    assert payload["think"] is True


def test_refine_sampling_remains_deterministic() -> None:
    payload = _generate_payload(mode="refine", instruction="refine this", attempt=2)

    assert payload["options"] == {"temperature": 0.1, "seed": 0, "num_predict": 512}


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
