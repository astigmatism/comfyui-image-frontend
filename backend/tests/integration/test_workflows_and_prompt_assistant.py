from __future__ import annotations

from typing import Any

from app.main import create_app
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.helpers import (
    first_profile,
    generation_payload,
    login_ready_admin,
    provision_user,
    restore_cookie,
)
from tests.publication_fixtures import build_publication_bundle


def _contains_private_graph_key(value: Any) -> bool:
    if isinstance(value, dict):
        if {"selector", "bindings", "node_id", "instance_uuid"}.intersection(value):
            return True
        return any(_contains_private_graph_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_graph_key(item) for item in value)
    return False


def test_discovery_registers_only_valid_pair_and_public_contract_is_semantic(
    fake_state, app_client: TestClient
) -> None:
    provision_user(app_client)
    profile = first_profile(app_client)
    assert profile["display_name"] == "Krea 2 NSFW V4"
    assert profile["readiness"] == "ready"
    assert profile["available"] is True
    assert len(profile["source_key"]) == 64
    assert "workflows/" not in str(profile)

    detail = app_client.get(f"/api/workflows/{profile['source_key']}")
    assert detail.status_code == 200
    interface = detail.json()["interface"]
    assert _contains_private_graph_key(interface) is False
    assert [parameter["id"] for parameter in interface["inputs"]] == [
        "prompt",
        "width",
        "height",
        "seed",
        "enable_seedvr2_upscale",
        "knpv4_1_strength",
    ]
    assert interface["inputs"][3]["maximum"] == "1125899906842624"
    assert interface["inputs"][3]["default"] is None
    assert [output["id"] for output in interface["outputs"]] == [
        "base",
        "second_pass",
        "final",
    ]
    assert [output["role"] for output in interface["outputs"]] == [
        "preview",
        "comparison",
        "final",
    ]
    assert all(output["kind"] == "image" for output in interface["outputs"])
    assert all(output["cardinality"] == "many" for output in interface["outputs"])

    # Ordinary users cannot inspect workflow registration diagnostics.
    assert app_client.get("/api/admin/workflows/diagnostics").status_code == 403

    # Read diagnostics directly only to verify startup discovery results; the API
    # authorization boundary above is the product behavior under test.
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        diagnostics = container.registry.diagnostics(session)
    assert {(item.basename, item.accepted, item.code) for item in diagnostics} == {
        ("Generic Landscape", True, "ready"),
        ("Krea 2 NSFW V4", True, "ready"),
    }
    assert fake_state.comfy_user_headers
    assert all(header == "fixture-user" for _, header in fake_state.comfy_user_headers)
    assert fake_state.userdata_raw_paths
    assert all(
        b"%2F" in path and b"/workflows/" not in path for path in fake_state.userdata_raw_paths
    )


def test_discovery_uses_recursive_fallback_with_headers_and_encoded_artifacts(
    fake_state, settings_factory
) -> None:
    fake_state.listing_mode = "fallback"
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="fallback.discovery")
        sources = client.get("/api/workflows").json()
        assert {item["display_name"] for item in sources} == {
            "Generic Landscape",
            "Krea 2 NSFW V4",
        }
        assert any(path == "/userdata" for path, _ in fake_state.comfy_user_headers)
        assert fake_state.userdata_raw_paths
        assert all(b"%2F" in path for path in fake_state.userdata_raw_paths)


def test_successful_empty_listing_retires_current_catalog(fake_state, settings_factory) -> None:
    with TestClient(create_app(settings_factory())) as client:
        _, user_cookie = provision_user(client, username="empty.catalog")
        assert len(client.get("/api/workflows").json()) == 2
        fake_state.workflow_files = {}
        login_ready_admin(client)
        refreshed = client.post(
            "/api/admin/workflows/refresh",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert refreshed.status_code == 200
        assert refreshed.json() == []
        restore_cookie(client, user_cookie)
        assert client.get("/api/workflows").json() == []


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
    assert recalled["parameters"]["prompt"] == composed["prompt"]
    assert len(fake_state.ollama_calls) == 1


def test_ollama_outage_only_disables_assistant(app_client: TestClient, fake_state) -> None:
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


def test_private_bindings_unpublished_parameters_and_stale_revisions_are_rejected(
    app_client: TestClient,
) -> None:
    provision_user(app_client, username="operator.guard")
    profile = first_profile(app_client)
    response = app_client.get(f"/api/workflows/{profile['source_key']}")
    assert response.status_code == 200, response.text
    assert _contains_private_graph_key(response.json()["interface"]) is False

    payload = generation_payload(app_client, "cannot elevate through semantic controls", seed=5)
    payload["parameters"]["operator_internal_switch"] = True
    rejected = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=payload,
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["fields"] == {
        "operator_internal_switch": "Unknown published parameter."
    }

    stale = generation_payload(app_client, "revision must remain exact", seed=6)
    stale["revision"]["api_sha256"] = "0" * 64
    rejected_revision = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=stale,
    )
    assert rejected_revision.status_code == 409
    assert rejected_revision.json()["error"]["code"] == "source_republished"


def test_dependency_loss_disables_an_accepted_source_until_refresh_recovers(
    app_client: TestClient, fake_state
) -> None:
    _, user_cookie = provision_user(app_client, username="dependency.guard")
    generic = next(
        item
        for item in app_client.get("/api/workflows").json()
        if item["display_name"] == "Generic Landscape"
    )

    fake_state.object_info.pop("CIFPublishImage")
    login_ready_admin(app_client)
    refreshed = app_client.post(
        "/api/admin/workflows/refresh",
        headers={"X-CSRF-Token": csrf(app_client)},
    )
    assert refreshed.status_code == 200, refreshed.text
    dependency_diagnostic = next(
        item for item in refreshed.json() if item["code"] == "dependency_missing"
    )
    assert dependency_diagnostic["details"]["missing_class_types"] == ["CIFPublishImage"]

    restore_cookie(app_client, user_cookie)
    unavailable = next(
        item
        for item in app_client.get("/api/workflows").json()
        if item["source_key"] == generic["source_key"]
    )
    assert unavailable["available"] is False
    assert unavailable["readiness"] == "dependency_missing"
    assert unavailable["message"] == (
        "Required ComfyUI node classes are unavailable for this source."
    )
    assert "CIFPublishImage" not in str(unavailable)
    rejected = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "source_key": generic["source_key"],
            "revision": generic["revision"],
            "parameters": {"prompt": "must not queue", "iterations": 1},
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "source_dependency_missing"

    fake_state.object_info["CIFPublishImage"] = {"input": {"required": {"images": ["IMAGE"]}}}
    login_ready_admin(app_client)
    recovered = app_client.post(
        "/api/admin/workflows/refresh",
        headers={"X-CSRF-Token": csrf(app_client)},
    )
    assert recovered.status_code == 200, recovered.text
    restore_cookie(app_client, user_cookie)
    ready = next(
        item
        for item in app_client.get("/api/workflows").json()
        if item["source_key"] == generic["source_key"]
    )
    assert ready["available"] is True
    assert ready["readiness"] == "ready"


def test_republication_changes_only_new_generation_snapshots(
    app_client: TestClient, fake_state
) -> None:
    _, user_cookie = provision_user(app_client, username="revision.guard")
    first_payload = generation_payload(app_client, "old immutable revision", seed=31)
    first = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=first_payload,
    )
    assert first.status_code == 201, first.text

    def mark_new_revision(manifest, workflow, api) -> None:  # type: ignore[no-untyped-def]
        del manifest
        workflow["revision_marker"] = "new-publication"
        api["20"]["inputs"]["revision_marker"] = "new-publication"

    republished = build_publication_bundle(
        "krea",
        publication_id="55555555-5555-4555-8555-555555555555",
        mutate_artifacts=mark_new_revision,
    )
    fake_state.workflow_files = dict(republished.files)
    login_ready_admin(app_client)
    refreshed = app_client.post(
        "/api/admin/workflows/refresh",
        headers={"X-CSRF-Token": csrf(app_client)},
    )
    assert refreshed.status_code == 200, refreshed.text

    restore_cookie(app_client, user_cookie)
    second_payload = generation_payload(app_client, "new immutable revision", seed=32)
    second = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=second_payload,
    )
    assert second.status_code == 201, second.text

    from app.models import Generation, WorkflowProfile

    container = app_client.app.state.container
    with container.db.session_factory() as session:
        old_generation = session.get(Generation, first.json()["id"])
        new_generation = session.get(Generation, second.json()["id"])
        assert old_generation is not None and new_generation is not None
        assert old_generation.workflow_profile_id != new_generation.workflow_profile_id
        assert (
            old_generation.generation_source_json["publication_id"]
            != (new_generation.generation_source_json["publication_id"])
        )
        assert "revision_marker" not in old_generation.compiled_graph_json["20"]["inputs"]
        assert new_generation.compiled_graph_json["20"]["inputs"]["revision_marker"] == (
            "new-publication"
        )
        old_profile = session.get(WorkflowProfile, old_generation.workflow_profile_id)
        new_profile = session.get(WorkflowProfile, new_generation.workflow_profile_id)
        assert old_profile is not None and new_profile is not None
        assert "revision_marker" not in old_profile.source_ui_json
        assert new_profile.source_ui_json["revision_marker"] == "new-publication"
