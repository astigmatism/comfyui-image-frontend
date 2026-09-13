from __future__ import annotations

import pytest
from app.domain.prompt_instructions import DEFAULT_PROMPT_INSTRUCTIONS
from app.models import PromptAssistantRun
from tests.conftest import csrf
from tests.helpers import generation_payload, provision_user


@pytest.mark.parametrize("mode", ["create", "refine"])
def test_custom_instructions_reach_router_and_survive_generation_recall(
    app_client, fake_state, mode
) -> None:
    provision_user(app_client)
    defaults = app_client.get("/api/prompt-assistant/status").json()["default_instructions"]
    assert defaults == DEFAULT_PROMPT_INSTRUCTIONS
    custom = "Write one concise image prompt in French. Preserve the requested subject."
    fake_state.ollama_response_prompt = "Un phare sur une falaise, dans la lumière du matin."
    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": mode,
            "prompt": "An existing lighthouse scene",
            "creative_direction": "soft morning light",
            "instructions": custom,
        },
    )
    assert response.status_code == 200, response.text
    composed = response.json()
    instruction = fake_state.ollama_calls[-1]["messages"][0]["content"]
    assert instruction.startswith(custom + "\n\n")
    assert "expert prompt writer" not in instruction
    assert "soft morning light" in instruction
    assert ("An existing lighthouse scene" in instruction) is (mode == "refine")
    with app_client.app.state.container.db.session_factory() as session:
        run = session.get(PromptAssistantRun, composed["composition_id"])
        assert run.instructions == custom
        assert custom not in str(run.raw_response_json)

    payload = generation_payload(app_client, composed["prompt"], seed=123)
    payload["prompt_assistant_run_id"] = composed["composition_id"]
    accepted = app_client.post(
        "/api/generations", headers={"X-CSRF-Token": csrf(app_client)}, json=payload
    )
    assert accepted.status_code == 201, accepted.text
    recalled = app_client.get(f"/api/generations/{accepted.json()['id']}/recall").json()
    assert recalled["prompt_assistant"]["instructions"] == custom


@pytest.mark.parametrize("instructions", ["", "   ", "x" * 8001])
def test_invalid_instructions_are_rejected_before_contacting_router(
    app_client, fake_state, instructions
) -> None:
    provision_user(app_client)
    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"mode": "create", "creative_direction": "a fox", "instructions": instructions},
    )
    assert response.status_code == 422
    assert fake_state.ollama_calls == []


def test_defaults_remain_available_when_router_is_offline(app_client, fake_state) -> None:
    provision_user(app_client)
    fake_state.ollama_available = False
    result = app_client.get("/api/prompt-assistant/status")
    assert result.json()["default_instructions"] == DEFAULT_PROMPT_INSTRUCTIONS
