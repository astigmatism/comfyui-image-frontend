from __future__ import annotations

import json
import os

import pytest
from app.config import Settings
from app.main import create_app
from app.models import PromptAssistantRun
from app.services.ollama import ComposeResult, OllamaAdapter
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.helpers import generation_payload, provision_user

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


def _assert_structured_output_selected(result: ComposeResult) -> None:
    responses = result.raw_response.get("attempts", [result.raw_response])
    assert isinstance(responses, list)
    assert responses
    for response in responses:
        assert isinstance(response, dict)
        assert response.get("selected_field") in {"response", "thinking"}
        assert response.get("validation_stage") == "complete"


@pytest.mark.parametrize("think", [False, True])
async def test_live_create_returns_a_new_prompt_from_the_direction(think: bool) -> None:
    adapter = _adapter()
    try:
        result = await adapter.compose(
            mode="create",
            prompt="A plain studio portrait of a ceramic vase.",
            direction="an astronaut tending a greenhouse on Mars",
            think=think,
        )
    finally:
        await adapter.close()

    assert "astronaut" in result.prompt.casefold()
    assert "greenhouse" in result.prompt.casefold()
    assert "mars" in result.prompt.casefold() or "martian" in result.prompt.casefold()
    assert "ceramic vase" not in result.prompt.casefold()
    _assert_structured_output_selected(result)


@pytest.mark.parametrize("think", [False, True])
async def test_live_refine_applies_the_requested_change(think: bool) -> None:
    adapter = _adapter()
    try:
        result = await adapter.compose(
            mode="refine",
            prompt=(
                "A woman holding a red umbrella beside a quiet canal, eye-level photograph, "
                "soft overcast light."
            ),
            direction="Change only the red umbrella to a blue umbrella.",
            think=think,
        )
    finally:
        await adapter.close()

    normalized = result.prompt.casefold()
    assert "blue umbrella" in normalized
    assert "red umbrella" not in normalized
    assert "quiet canal" in normalized
    assert "soft overcast light" in normalized
    _assert_structured_output_selected(result)


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
            _assert_structured_output_selected(result)
    finally:
        await adapter.close()

    assert len(set(prompts)) == 4
    for prompt in prompts:
        normalized = prompt.casefold()
        assert "red fox" in normalized
        assert "moonlit" in normalized or "moonlight" in normalized
        assert "pine" in normalized
        assert len(prompt.split()) >= 5


@pytest.mark.parametrize("mode", ["create", "refine"])
@pytest.mark.parametrize("think", [False, True])
def test_live_composition_api_persists_and_compiles_the_router_output(
    settings_factory, fake_state, mode: str, think: bool
) -> None:
    """Use the real router with isolated accounts/storage and a fake ComfyUI target."""
    settings = settings_factory(ollama_base_url=_BASE_URL)
    app = create_app(settings)
    with TestClient(app) as client:
        provision_user(client, username="live.router.audit")
        body = {
            "mode": mode,
            "prompt": "A woman holding a red umbrella beside a quiet canal.",
            "creative_direction": (
                "an astronaut tending a greenhouse on Mars"
                if mode == "create"
                else "Change only the red umbrella to a blue umbrella."
            ),
            "think": think,
        }
        outgoing: list[dict] = []

        async def record_request(request) -> None:
            if request.url.path.endswith("/api/chat"):
                outgoing.append(json.loads(request.content))

        adapter = app.state.container.ollama
        adapter._client.event_hooks["request"].append(record_request)
        response = client.post(
            "/api/prompt-assistant/compose", headers={"X-CSRF-Token": csrf(client)}, json=body
        )
        assert response.status_code == 200, response.text
        composed = response.json()
        assert outgoing
        for request in outgoing:
            assert request["model"] == settings.ollama_model
            assert request["think"] == ("xhigh" if think else False)
            assert body["creative_direction"] in request["messages"][0]["content"]
            assert (body["prompt"] in request["messages"][0]["content"]) is (mode == "refine")
        final = composed["prompt"].casefold()
        if mode == "refine":
            assert "blue umbrella" in final and "red umbrella" not in final
            assert "quiet canal" in final
        else:
            assert "astronaut" in final and "greenhouse" in final
            assert "umbrella" not in final

        payload = generation_payload(client, "stale browser prompt", seed=123)
        payload["prompt_assistant_run_id"] = composed["composition_id"]
        accepted = client.post(
            "/api/generations", headers={"X-CSRF-Token": csrf(client)}, json=payload
        )
        assert accepted.status_code == 201, accepted.text
        generation_id = accepted.json()["id"]
        recalled = client.get(f"/api/generations/{generation_id}/recall").json()
        assert recalled["parameters"]["prompt"] == composed["prompt"]
        with app.state.container.db.session_factory() as session:
            run = session.get(PromptAssistantRun, composed["composition_id"])
            assert run is not None
            assert run.model_name == composed["model"]
            assert run.prompt_before == body["prompt"]
            assert run.creative_direction == body["creative_direction"]
            assert run.thinking_enabled is think
            assert run.ollama_output == composed["prompt"]
            assert run.generation_id == generation_id
        assert fake_state.ollama_calls == []
