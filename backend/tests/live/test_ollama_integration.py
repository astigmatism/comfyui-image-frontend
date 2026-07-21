from __future__ import annotations

import os

import pytest
from app.config import Settings
from app.services.ollama import ComposeResult, OllamaAdapter

_BASE_URL = os.getenv("CIF_OLLAMA_BASE_URL")
_RUN_LIVE = os.getenv("CIF_RUN_LIVE_OLLAMA_TESTS") == "1"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (_RUN_LIVE and _BASE_URL),
        reason="set CIF_RUN_LIVE_OLLAMA_TESTS=1 and CIF_OLLAMA_BASE_URL to run",
    ),
]


def _adapter() -> OllamaAdapter:
    return OllamaAdapter(Settings(test_mode=True, ollama_base_url=_BASE_URL))


def _assert_thinking_used(result: ComposeResult) -> None:
    responses = result.raw_response.get("attempts", [result.raw_response])
    assert isinstance(responses, list)
    assert responses
    for response in responses:
        assert isinstance(response, dict)
        thinking = response.get("thinking")
        assert isinstance(thinking, str)
        assert thinking.strip()


async def test_live_create_returns_an_expanded_new_prompt() -> None:
    adapter = _adapter()
    try:
        result = await adapter.compose(
            mode="create",
            prompt="A plain studio portrait of a ceramic vase.",
            direction="an astronaut tending a greenhouse on Mars",
        )
    finally:
        await adapter.close()

    assert "an astronaut tending a greenhouse on mars" in result.prompt.casefold()
    assert result.prompt.startswith("an astronaut tending a greenhouse on Mars")
    assert len(result.prompt.split()) >= 70
    assert "ceramic vase" not in result.prompt.casefold()
    _assert_thinking_used(result)


async def test_live_refine_applies_the_requested_change() -> None:
    adapter = _adapter()
    try:
        result = await adapter.compose(
            mode="refine",
            prompt=(
                "A woman holding a red umbrella beside a quiet canal, eye-level photograph, "
                "soft overcast light."
            ),
            direction="Change only the red umbrella to a blue umbrella.",
        )
    finally:
        await adapter.close()

    normalized = result.prompt.casefold()
    assert "blue umbrella" in normalized
    assert "red umbrella" not in normalized
    assert "quiet canal" in normalized
    assert "soft overcast light" in normalized
    _assert_thinking_used(result)


async def test_live_repeated_create_returns_four_distinct_prompts() -> None:
    adapter = _adapter()
    direction = "a red fox beneath moonlit pines"
    current = "an unrelated starting prompt"
    prompts: list[str] = []
    try:
        for _ in range(4):
            result = await adapter.compose(
                mode="create",
                prompt=current,
                direction=direction,
                excluded_prompts=prompts,
            )
            prompts.append(result.prompt)
            current = result.prompt
            _assert_thinking_used(result)
    finally:
        await adapter.close()

    assert len(set(prompts)) == 4
    for prompt in prompts:
        normalized = prompt.casefold()
        assert "red fox" in normalized
        assert prompt.startswith(direction)
        assert "pine" in normalized
        assert len(prompt.split()) >= 70
