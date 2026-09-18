from __future__ import annotations

import pytest
from app.domain.prompt_instructions import DEFAULT_PROMPT_INSTRUCTIONS
from app.models import PromptAssistantRun
from tests.conftest import csrf
from tests.helpers import generation_payload, provision_user


@pytest.mark.parametrize("mode", ["create", "refine"])
@pytest.mark.parametrize("think", [True, False])
def test_custom_instructions_reach_router_and_survive_generation_recall(
    app_client, fake_state, mode, think
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
            "think": think,
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
        assert run.thinking_enabled is think
        assert custom not in str(run.raw_response_json)

    payload = generation_payload(app_client, composed["prompt"], seed=123)
    payload["prompt_assistant_run_id"] = composed["composition_id"]
    accepted = app_client.post(
        "/api/generations", headers={"X-CSRF-Token": csrf(app_client)}, json=payload
    )
    assert accepted.status_code == 201, accepted.text
    recalled = app_client.get(f"/api/generations/{accepted.json()['id']}/recall").json()
    assistant = recalled["prompt_assistant"]
    assert assistant["instructions"] == custom
    assert assistant["mode"] == mode
    assert assistant["creative_direction"] == "soft morning light"
    assert assistant["thinking_enabled"] is think


def test_manual_generation_snapshots_assistant_inputs_for_recall(app_client, fake_state) -> None:
    provision_user(app_client)
    payload = generation_payload(app_client, "a red fox sitting in snow", seed=321)
    payload["prompt_assistant"] = {
        "mode": "create",
        "creative_direction": "a red fox in winter light",
        "instructions": "Prefer quiet, natural scenes.",
        "thinking_enabled": False,
    }
    accepted = app_client.post(
        "/api/generations", headers={"X-CSRF-Token": csrf(app_client)}, json=payload
    )
    assert accepted.status_code == 201, accepted.text
    recalled = app_client.get(f"/api/generations/{accepted.json()['id']}/recall").json()
    assistant = recalled["prompt_assistant"]
    assert assistant["mode"] == "create"
    assert assistant["creative_direction"] == "a red fox in winter light"
    assert assistant["instructions"] == "Prefer quiet, natural scenes."
    assert assistant["thinking_enabled"] is False
    # No run was linked, so no composition provenance is reported.
    assert assistant["ollama_output"] is None
    assert assistant["model"] is None


def test_blank_assistant_instructions_are_stored_as_null(app_client, fake_state) -> None:
    provision_user(app_client)
    payload = generation_payload(app_client, "a quiet harbor at dawn", seed=322)
    payload["prompt_assistant"] = {
        "mode": "refine",
        "creative_direction": "",
        "instructions": "   ",
        "thinking_enabled": True,
    }
    accepted = app_client.post(
        "/api/generations", headers={"X-CSRF-Token": csrf(app_client)}, json=payload
    )
    assert accepted.status_code == 201, accepted.text
    recalled = app_client.get(f"/api/generations/{accepted.json()['id']}/recall").json()
    assistant = recalled["prompt_assistant"]
    assert assistant["mode"] == "refine"
    assert assistant["creative_direction"] == ""
    assert assistant["instructions"] is None
    assert assistant["thinking_enabled"] is True


def test_batch_items_each_retrieve_assistant_snapshot_even_without_linked_run(
    app_client, fake_state
) -> None:
    provision_user(app_client)
    fake_state.ollama_response_prompt = "A lighthouse in fog, first light on the water."
    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "refine",
            "prompt": "An existing lighthouse scene",
            "creative_direction": "foggy dawn on the coast",
            "think": False,
            "instructions": "Keep the scene grounded in real light.",
        },
    )
    assert response.status_code == 200, response.text
    composed = response.json()
    snapshot = {
        "mode": "refine",
        "creative_direction": "foggy dawn on the coast",
        "instructions": "Keep the scene grounded in real light.",
        "thinking_enabled": False,
    }
    first = generation_payload(app_client, composed["prompt"], seed=41)
    first["prompt_assistant_run_id"] = composed["composition_id"]
    first["prompt_assistant"] = snapshot
    second = generation_payload(app_client, "A lighthouse at night, stars above", seed=42)
    second["prompt_assistant"] = snapshot
    batch = app_client.post(
        "/api/generations/batch",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"items": [first, second]},
    )
    assert batch.status_code == 201, batch.text
    items = batch.json()["items"]
    assert len(items) == 2
    recalls = [
        app_client.get(f"/api/generations/{item['generation']['id']}/recall").json()
        for item in items
    ]
    for recalled in recalls:
        assistant = recalled["prompt_assistant"]
        assert assistant["mode"] == "refine"
        assert assistant["creative_direction"] == "foggy dawn on the coast"
        assert assistant["instructions"] == "Keep the scene grounded in real light."
        assert assistant["thinking_enabled"] is False
    # The run is consumed by the first item only; the second carries no provenance.
    assert recalls[0]["prompt_assistant"]["ollama_output"] == composed["prompt"]
    assert recalls[1]["prompt_assistant"]["ollama_output"] is None
    assert recalls[1]["prompt_assistant"]["model"] is None


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
