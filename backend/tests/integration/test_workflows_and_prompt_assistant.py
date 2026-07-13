from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import csrf
from tests.helpers import first_profile, generation_payload, provision_user


def _contains_private_graph_key(value: Any) -> bool:
    if isinstance(value, dict):
        if {"selector", "bindings", "node_id"}.intersection(value):
            return True
        return any(_contains_private_graph_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_graph_key(item) for item in value)
    return False


def test_discovery_registers_only_valid_pair_and_public_contract_is_semantic(
    app_client: TestClient,
) -> None:
    provision_user(app_client)
    profile = first_profile(app_client)
    assert profile["workflow_id"] == "fake-progressive-v1"

    detail = app_client.get(f"/api/workflows/{profile['profile_id']}")
    assert detail.status_code == 200
    contract = detail.json()["contract"]
    assert _contains_private_graph_key(contract) is False
    assert [control["id"] for control in contract["controls"]][0] == "prompt.text"
    assert contract["outputs"][0]["id"] == "base_image"

    # Ordinary users cannot inspect workflow registration diagnostics.
    assert app_client.get("/api/admin/workflows/diagnostics").status_code == 403

    # Read diagnostics directly only to verify startup discovery results; the API
    # authorization boundary above is the product behavior under test.
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        diagnostics = container.registry.diagnostics(session)
    by_basename = {item.basename: item for item in diagnostics}
    assert by_basename["profiles/progressive"].accepted is True
    assert by_basename["profiles/incomplete"].code == "incomplete_pair"
    assert by_basename["profiles/hash-mismatch"].accepted is False
    assert by_basename["profiles/invalid-binding"].accepted is False
    assert by_basename["profiles/missing-dependency"].accepted is False


def test_prompt_assistant_is_explicit_deterministic_and_does_not_queue(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client)
    assert app_client.get("/api/prompt-assistant/status").json()["available"] is True
    before = app_client.get("/api/generations").json()["items"]
    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "refine",
            "prompt": "portrait",
            "creative_direction": "soft window light",
        },
    )
    assert response.status_code == 200, response.text
    composed = response.json()
    assert composed["prompt"] == "portrait, soft window light"
    assert composed["model"] == "alpha:latest"
    assert fake_state.ollama_calls[-1]["model"] == "alpha:latest"
    assert app_client.get("/api/generations").json()["items"] == before

    payload = generation_payload(app_client, composed["prompt"], seed=123)
    payload["prompt_assistant_run_id"] = composed["composition_id"]
    accepted = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=payload,
    )
    assert accepted.status_code == 201, accepted.text
    generation_id = accepted.json()["id"]
    # Generate submits the visible prompt; it does not invoke Ollama again.
    assert len(fake_state.ollama_calls) == 1
    container = app_client.app.state.container
    from app.models import PromptAssistantRun
    from sqlalchemy import select

    with container.db.session_factory() as session:
        run = session.scalar(
            select(PromptAssistantRun).where(PromptAssistantRun.id == composed["composition_id"])
        )
        assert run is not None
        assert run.generation_id == generation_id
        assert run.prompt_before == "portrait"
        assert run.creative_direction == "soft window light"
        assert run.model_name == "alpha:latest"
        assert run.ollama_output == composed["prompt"]
    recalled = app_client.get(f"/api/generations/{generation_id}/recall").json()
    assert recalled["available"] is True
    assert recalled["controls"]["prompt.text"] == composed["prompt"]
    assert len(fake_state.ollama_calls) == 1


def test_ollama_outage_only_disables_assistant(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client)
    fake_state.ollama_available = False
    status = app_client.get("/api/prompt-assistant/status")
    assert status.status_code == 200
    assert status.json()["available"] is False
    compose = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"mode": "create", "prompt": "", "creative_direction": "a moonlit lake"},
    )
    assert compose.status_code == 503

    validation = app_client.post(
        "/api/generations/validate",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=generation_payload(app_client, "manual prompt", seed=44),
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True


def test_operator_controls_and_preset_values_are_never_exposed_or_user_settable(
    app_client: TestClient,
) -> None:
    provision_user(app_client, username="operator.guard")
    profile = first_profile(app_client)
    container = app_client.app.state.container

    from app.models import WorkflowProfile

    with container.db.session_factory() as session:
        stored = session.get(WorkflowProfile, profile["profile_id"])
        assert stored is not None
        contract = dict(stored.resolved_contract_json)
        controls = list(contract["controls"])
        controls.append(
            {
                "id": "operator.internal_switch",
                "label": "Internal switch",
                "type": "boolean",
                "tier": "operator",
                "default": False,
                "bindings": [{"strategy": "fixed", "value": False}],
            }
        )
        presets = list(contract.get("presets", []))
        presets.append(
            {
                "id": "operator-safe-preset",
                "label": "Safe preset",
                "values": {
                    "sampling.steps": 5,
                    "operator.internal_switch": False,
                },
            }
        )
        contract["controls"] = controls
        contract["presets"] = presets
        stored.resolved_contract_json = contract
        session.commit()

    response = app_client.get(f"/api/workflows/{profile['profile_id']}")
    assert response.status_code == 200, response.text
    public = response.json()["contract"]
    assert "operator.internal_switch" not in {item["id"] for item in public["controls"]}
    public_preset = next(item for item in public["presets"] if item["id"] == "operator-safe-preset")
    assert public_preset["values"] == {"sampling.steps": 5}

    payload = generation_payload(app_client, "cannot elevate through semantic controls", seed=5)
    payload["controls"]["operator.internal_switch"] = True
    rejected = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=payload,
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["fields"]["operator.internal_switch"].startswith(
        "This operator-only control"
    )
