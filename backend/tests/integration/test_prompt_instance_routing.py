from __future__ import annotations

import copy
import json
from uuid import uuid4

import pytest
from app.main import create_app
from app.models import (
    AutoGeneration,
    Generation,
    GenerationPreparation,
    PromptGenerationRun,
    WorkflowCatalogHealth,
    WorkflowDiagnostic,
    WorkflowProfile,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.fake_services import LiveFakeServer
from tests.helpers import create_generation, provision_user, wait_for_status
from tests.integration.test_auto_generation import enable, tick
from tests.integration.test_prompt_generation import post, wait_for
from tests.publication_fixtures import build_publication_bundle


@pytest.fixture
def routing(settings_factory, fake_services, fake_state):
    files = {**build_publication_bundle("krea").files, **build_publication_bundle("text").files}
    fake_state.workflow_files = dict(files)
    with LiveFakeServer() as cpu:
        cpu.state.workflow_files = dict(files)
        settings = settings_factory(
            comfyui_instances=[
                {
                    "id": "primary",
                    "label": "Primary",
                    "base_url": fake_services.base_url,
                    "ws_url": fake_services.ws_url,
                    "concurrency": 1,
                },
                {
                    "id": "promptgen",
                    "label": "Prompt Generator",
                    "base_url": cpu.base_url,
                    "ws_url": cpu.ws_url,
                    "concurrency": 1,
                },
            ],
            comfyui_default_instance_id="primary",
            comfyui_text_instance_id="promptgen",
        )
        with TestClient(create_app(settings)) as client:
            owner, _ = provision_user(client)
            container = client.app.state.container
            client.portal.call(container.registry.refresh)
            for instance in ("primary", "promptgen"):
                container.worker._persist_instance_health(instance, True, None)
            yield client, container, fake_state, cpu.state, owner["id"]


def text_payload(client):
    source = client.get("/api/workflows?output_kind=text").json()[0]
    return {
        "source_key": source["source_key"],
        "revision": source["revision"],
        "parameters": {"subject_name": "Mira", "seed": "17"},
    }


def test_catalog_replicas_are_scoped_deduplicated_and_private(routing):
    client, container, _gpu, cpu, _ = routing
    images = client.get("/api/workflows").json()
    texts = client.get("/api/workflows?output_kind=text").json()
    assert len(images) == len(texts) == 1
    assert images[0]["instance_id"] == "primary"
    assert texts[0]["instance_id"] == "promptgen"
    assert client.get("/api/comfyui-instances").json()["text_instance_id"] == "promptgen"
    with container.db.session_factory() as session:
        assert (
            len(
                list(
                    session.scalars(
                        select(WorkflowProfile).where(WorkflowProfile.is_current.is_(True))
                    )
                )
            )
            == 4
        )
        assert len(list(session.scalars(select(WorkflowDiagnostic)))) == 4
    for source in images + texts:
        assert {replica["instance_id"] for replica in source["replicas"]} == {
            "primary",
            "promptgen",
        }
        for replica in source["replicas"]:
            detail = client.get("/api/workflows/" + replica["source_key"])
            assert detail.status_code == 200
            assert detail.json()["instance_id"] == source["instance_id"]
        serialized = json.dumps(source)
        for private in ("source_id", "workflows/", "bindings", "base_url", "dependencies"):
            assert private not in serialized

    cpu.service_available = False
    client.portal.call(container.registry.refresh, "promptgen")
    offline = client.get("/api/workflows?output_kind=text").json()[0]
    assert offline["source_key"] == texts[0]["source_key"]
    assert offline["cached"] is True
    assert client.get("/api/workflows").json()[0]["cached"] is False
    with container.db.session_factory() as session:
        assert session.get(WorkflowCatalogHealth, "primary").available
        assert not session.get(WorkflowCatalogHealth, "promptgen").available
        assert (
            len(
                list(
                    session.scalars(
                        select(WorkflowDiagnostic).where(
                            WorkflowDiagnostic.instance_id == "primary"
                        )
                    )
                )
            )
            == 2
        )

    cpu.service_available = True
    cpu.workflow_files = {}
    client.portal.call(container.registry.refresh, "promptgen")
    assert client.get("/api/workflows?output_kind=text").json() == []
    with container.db.session_factory() as session:
        assert {
            row.instance_id
            for row in session.scalars(
                select(WorkflowProfile).where(WorkflowProfile.is_current.is_(True))
            )
        } == {"primary"}


def test_assignments_reject_overrides_and_preserve_idempotent_replay(routing):
    client, container, _, _, _ = routing
    payload = text_payload(client)
    key = str(uuid4())
    accepted = post(client, "/api/prompt-generations", payload, key)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["comfyui_instance_id"] == "promptgen"
    override = post(
        client, "/api/prompt-generations", {**payload, "comfyui_instance_id": "primary"}
    )
    assert override.status_code == 409, override.text
    assert override.json()["error"]["code"] == "runtime_assignment_conflict"
    with container.db.session_factory() as session:
        for result in (accepted.json(),):
            run = session.get(PromptGenerationRun, result["id"])
            assert run.instance_id == result["comfyui_instance_id"]
            assert run.request_json["comfyui_instance_id"] == run.instance_id
            assert session.get(WorkflowProfile, run.profile_id).instance_id == run.instance_id
    container.settings.comfyui_text_instance_id = None
    assert post(client, "/api/prompt-generations", payload, key).json() == accepted.json()
    conflict = post(
        client, "/api/prompt-generations", {**payload, "comfyui_instance_id": "primary"}, key
    )
    assert conflict.status_code == 409
    image = create_generation(client, "image default")
    assert image["comfyui_instance_id"] == "primary"
    container.settings.comfyui_text_instance_id = "promptgen"
    unknown = post(client, "/api/prompt-generations", {**payload, "comfyui_instance_id": "gone"})
    assert unknown.json()["error"]["code"] == "runtime_assignment_conflict"


def test_text_rejects_missing_drifted_and_dependency_missing_copies(routing):
    client, container, gpu, cpu, _ = routing
    payload = text_payload(client)
    # Validate a request from the primary alias against the configured CPU default.
    replicas = client.get("/api/workflows?output_kind=text").json()[0]["replicas"]
    payload["source_key"] = next(
        item["source_key"] for item in replicas if item["instance_id"] == "primary"
    )
    original = dict(cpu.workflow_files)
    cpu.workflow_files = dict(build_publication_bundle("krea").files)
    client.portal.call(container.registry.refresh, "promptgen")
    assert (
        post(client, "/api/prompt-generations", payload).json()["error"]["code"]
        == "text_publication_missing"
    )
    cpu.workflow_files = original
    client.portal.call(container.registry.refresh, "promptgen")
    with container.db.session_factory() as session:
        profile = session.scalar(
            select(WorkflowProfile).where(
                WorkflowProfile.instance_id == "promptgen",
                WorkflowProfile.source_key == text_payload(client)["source_key"],
            )
        )
        old_hash = profile.api_graph_sha256
        profile.api_graph_sha256 = "f" * 64
        session.commit()
    assert (
        post(client, "/api/prompt-generations", payload).json()["error"]["code"]
        == "text_publication_mismatch"
    )
    with container.db.session_factory() as session:
        profile = session.get(WorkflowProfile, profile.id)
        profile.api_graph_sha256 = old_hash
        session.commit()
    cpu.object_info.pop("CIFPublishText")
    client.portal.call(container.registry.refresh, "promptgen")
    assert (
        post(client, "/api/prompt-generations", payload).json()["error"]["code"]
        == "source_dependency_missing"
    )
    # GPU copies cannot mask a broken CPU publication.
    logical = client.get("/api/workflows?output_kind=text").json()[0]
    assert logical["instance_id"] == "promptgen" and not logical["available"]
    assert not next(item for item in logical["replicas"] if item["instance_id"] == "promptgen")[
        "available"
    ]
    assert client.get("/api/workflows/" + logical["source_key"]).status_code == 409
    # Primary dependencies cannot be overwritten by CPU discovery.
    override = post(
        client, "/api/prompt-generations", {**payload, "comfyui_instance_id": "primary"}
    )
    assert override.status_code == 409, override.text
    assert not gpu.submitted and not cpu.submitted


def test_cpu_text_completes_while_gpu_image_occupies_its_lane(routing):
    client, container, gpu, cpu, _ = routing
    gpu.wait_for_cancel_prompts.add("held gpu image")
    image = create_generation(client, "held gpu image")
    client.portal.call(container.worker.start)
    client.portal.call(container.prompt_generation.start)
    wait_for_status(client, image["id"], "running")
    text = post(client, "/api/prompt-generations", text_payload(client)).json()
    result = wait_for(
        client,
        "/api/prompt-generations/" + text["id"],
        lambda value: value["status"] == "succeeded",
    )
    assert result["comfyui_instance_id"] == "promptgen"
    assert client.get("/api/generations/" + image["id"]).json()["status"] == "running"
    assert len(gpu.submitted) == len(cpu.submitted) == 1


def test_automation_and_legacy_snapshots_pin_both_stages(routing):
    client, container, _, _, owner = routing
    payload = text_payload(client)
    enabled = enable(client, prompt_generation=payload, quantity=2, max_generations=2)
    assert enabled["snapshot"]["prompt_generation"]["comfyui_instance_id"] == "promptgen"
    assert enabled["snapshot"]["generation"]["comfyui_instance_id"] == "primary"
    with container.db.session_factory() as session:
        row = session.get(AutoGeneration, owner)
        legacy = copy.deepcopy(row.snapshot_json)
        legacy["prompt_generation"]["comfyui_instance_id"] = "primary"
        legacy["generation"]["comfyui_instance_id"] = "promptgen"
        row.snapshot_json = legacy
        session.commit()
    tick(client, owner)
    container.settings.comfyui_text_instance_id = None
    with container.db.session_factory() as session:
        assert (
            session.get(AutoGeneration, owner).snapshot_json["prompt_generation"][
                "comfyui_instance_id"
            ]
            == "promptgen"
        )
        texts = list(session.scalars(select(PromptGenerationRun)))
        assert len(texts) == 1 and texts[0].instance_id == "promptgen"
        prepared = list(session.scalars(select(GenerationPreparation)))
        assert len(prepared) == 2
        assert all(
            row.request_json["generation"]["comfyui_instance_id"] == "primary" for row in prepared
        )
        assert all(
            row.request_json["prompt_generation"]["comfyui_instance_id"] == "promptgen"
            for row in prepared
        )
        assert not list(session.scalars(select(Generation)))


def test_removed_text_pin_is_failed_during_recovery(routing):
    client, container, _, _, _ = routing
    accepted = post(client, "/api/prompt-generations", text_payload(client)).json()
    container.comfyui_instances.configured_ids = frozenset({"primary"})
    client.portal.call(container.prompt_generation.recover, container.worker)
    result = client.get("/api/prompt-generations/" + accepted["id"]).json()
    assert result["status"] == "failed"
    assert result["error"]["code"] == "comfyui_instance_unconfigured"


def test_loading_and_offline_catalogs_are_retryable_and_cached_jobs_wait(routing):
    client, container, _, cpu, _ = routing
    payload = text_payload(client)
    source = client.get("/api/workflows?output_kind=text").json()[0]
    payload["source_key"] = next(
        item["source_key"] for item in source["replicas"] if item["instance_id"] == "primary"
    )
    cpu.workflow_files = {}
    client.portal.call(container.registry.refresh, "promptgen")
    container.registry.mark_startup_loading()
    loading = post(client, "/api/prompt-generations", payload)
    assert loading.status_code == 503
    assert loading.json()["error"]["code"] == "source_catalog_loading"
    cpu.service_available = False
    client.portal.call(container.registry.refresh, "promptgen")
    offline = post(client, "/api/prompt-generations", payload)
    assert offline.status_code == 503
    cpu.service_available = True
    cpu.workflow_files = dict(build_publication_bundle("text").files)
    client.portal.call(container.registry.refresh)
    cpu.service_available = False
    client.portal.call(container.registry.refresh, "promptgen")
    container.worker._persist_instance_health("promptgen", False, "offline")
    accepted = post(client, "/api/prompt-generations", payload)
    assert accepted.status_code == 202, accepted.text
    client.portal.call(container.worker._dispatch_iteration)
    assert (
        client.get("/api/prompt-generations/" + accepted.json()["id"]).json()["status"] == "queued"
    )
    cpu.service_available = True
    client.portal.call(container.worker.start)
    result = wait_for(
        client,
        "/api/prompt-generations/" + accepted.json()["id"],
        lambda value: value["status"] == "succeeded",
    )
    assert result["comfyui_instance_id"] == "promptgen"
    with container.db.session_factory() as session:
        assert session.get(WorkflowCatalogHealth, "promptgen").available


def test_dependency_rejected_replicas_are_deduplicated_and_recover_independently(routing):
    from sqlalchemy import delete

    client, container, gpu, cpu, _ = routing
    with container.db.session_factory() as session:
        session.execute(delete(WorkflowProfile))
        session.commit()
    publisher = cpu.object_info.pop("CIFPublishText")
    gpu.object_info.pop("CIFPublishText")
    client.portal.call(container.registry.refresh)
    rejected = client.get("/api/workflows?output_kind=text").json()
    assert len(rejected) == 1
    assert rejected[0]["instance_id"] == "promptgen"
    assert not rejected[0]["available"]
    assert len(rejected[0]["replicas"]) == 2
    gpu.object_info["CIFPublishText"] = publisher
    client.portal.call(container.registry.refresh, "primary")
    accepted = client.get("/api/workflows?output_kind=text").json()
    assert len(accepted) == 1 and accepted[0]["instance_id"] == "promptgen"
    assert not accepted[0]["available"]
    assert len(accepted[0]["replicas"]) == 2
    with container.db.session_factory() as session:
        assert any(
            item.code == "dependency_missing"
            for item in session.scalars(
                select(WorkflowDiagnostic).where(WorkflowDiagnostic.instance_id == "promptgen")
            )
        )


def test_distinct_sources_with_equal_names_stay_separate_and_removed_instances_stay_historical(
    routing,
):
    client, container, gpu, cpu, _ = routing
    extra = build_publication_bundle("generic")
    gpu.workflow_files.update(extra.files)
    cpu.workflow_files.update(extra.files)
    client.portal.call(container.registry.refresh)
    with container.db.session_factory() as session:
        for profile in session.scalars(select(WorkflowProfile)):
            profile.display_name = "Same display name"
        session.commit()
        assert len(container.registry.list_current(session)) == 3
    images = client.get("/api/workflows").json()
    assert len(images) == 2
    assert {row["display_name"] for row in images} == {"Same display name"}
    old_text_key = text_payload(client)["source_key"]
    container.registry.configured_ids = ("primary",)
    current = client.get("/api/workflows?output_kind=text").json()
    assert current == []
    assert client.get("/api/workflows/" + old_text_key).status_code == 503
    with container.db.session_factory() as session:
        assert len(list(session.scalars(select(WorkflowProfile)))) == 6


@pytest.mark.parametrize("preparation", [False, True])
def test_legacy_submission_digest_replays_after_text_routing_upgrade(routing, preparation):
    import hashlib

    from app.models import GenerationSubmission
    from app.schemas import GenerationPreparationCreate, PromptGenerationCreate
    from tests.helpers import generation_payload

    client, container, _, _, owner = routing
    prompt = text_payload(client)
    if preparation:
        path, endpoint = "/api/generation-preparations", "preparation"
        payload = {
            "items": [{"generation": generation_payload(client, ""), "prompt_generation": prompt}]
        }
        original = GenerationPreparationCreate.model_validate(payload).model_dump(mode="json")
        original["items"][0]["prompt_generation"].pop("comfyui_instance_id")
    else:
        path, endpoint, payload = "/api/prompt-generations", "prompt", prompt
        original = PromptGenerationCreate.model_validate(payload).model_dump(mode="json")
        original.pop("comfyui_instance_id")
    old_digest = hashlib.sha256(
        json.dumps(
            {"endpoint": endpoint, "payload": original}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    key = str(uuid4())
    accepted = post(client, path, payload, key)
    assert accepted.status_code == 202, accepted.text
    with container.db.session_factory() as session:
        receipt = session.get(GenerationSubmission, (owner, key))
        assert receipt.request_digest == old_digest
        receipt.request_digest = old_digest
        session.commit()
    container.settings.comfyui_text_instance_id = None
    assert post(client, path, payload, key).json() == accepted.json()


def test_missing_text_assignment_disables_only_prompt_generation(routing):
    client, container, gpu, cpu, _ = routing
    payload = text_payload(client)
    container.settings.comfyui_text_instance_id = None
    container.registry.text_instance_id = None
    assert client.get("/api/workflows?output_kind=text").json() == []
    rejected = post(client, "/api/prompt-generations", payload)
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "prompt_runtime_not_configured"
    assert create_generation(client, "images still work")["comfyui_instance_id"] == "primary"
    assert not gpu.submitted and not cpu.submitted


def test_all_new_request_paths_reject_conflicting_runtime_assignments(routing):
    from tests.helpers import generation_payload
    from tests.integration.test_auto_generation import command

    client, _, gpu, cpu, _ = routing
    image = generation_payload(client, "fixed image")
    text = text_payload(client)
    bad_image = {**image, "comfyui_instance_id": "promptgen"}
    bad_text = {**text, "comfyui_instance_id": "primary"}
    for path, payload in [
        ("/api/generations", bad_image),
        ("/api/generations/batch", {"items": [bad_image]}),
        (
            "/api/generation-preparations",
            {"items": [{"generation": bad_image, "prompt_generation": text}]},
        ),
        (
            "/api/generation-preparations",
            {"items": [{"generation": image, "prompt_generation": bad_text}]},
        ),
    ]:
        response = post(client, path, payload)
        errors = response.json().get("items", [])
        if errors:  # Batch endpoint records per-item outcomes.
            assert errors[0]["error"]["code"] == "runtime_assignment_conflict"
        else:
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "runtime_assignment_conflict"
    current = client.get("/api/auto-generation").json()
    for generation, prompt in [(bad_image, text), (image, bad_text)]:
        response = command(
            client,
            expected_revision=current["revision"],
            enabled=True,
            snapshot={"generation": generation, "prompt_generation": prompt},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "runtime_assignment_conflict"
    assert not gpu.submitted and not cpu.submitted


@pytest.mark.parametrize("kind", ["image", "text"])
def test_undiscovered_identity_is_retryable_until_assigned_catalog_is_ready(routing, kind):
    from tests.helpers import generation_payload

    client, container, gpu, cpu, _ = routing
    payload = text_payload(client) if kind == "text" else generation_payload(client, "loading")
    payload["source_key"] = "f" * 64
    path = "/api/prompt-generations" if kind == "text" else "/api/generations"
    instance = "promptgen" if kind == "text" else "primary"
    service = cpu if kind == "text" else gpu
    container.registry.mark_startup_loading()
    loading = post(client, path, payload)
    assert loading.status_code == 503, loading.text
    assert loading.json()["error"]["code"] == "source_catalog_loading"
    assert client.get("/api/workflows/" + payload["source_key"]).status_code == 503
    service.service_available = False
    client.portal.call(container.registry.refresh, instance)
    offline = post(client, path, payload)
    assert offline.status_code == 503, offline.text
    assert offline.json()["error"]["code"] == "comfyui_instance_unavailable"
    service.service_available = True
    client.portal.call(container.registry.refresh, instance)
    assert post(client, path, payload).status_code == 409


@pytest.mark.parametrize("kind", ["image", "text"])
def test_authoritative_catalog_ignores_other_revision_and_compiles_its_own_graph(routing, kind):
    from tests.helpers import generation_payload

    client, container, _, _, _ = routing
    source = client.get("/api/workflows?output_kind=" + kind).json()[0]
    authority = "primary" if kind == "image" else "promptgen"
    alias = next(r for r in source["replicas"] if r["instance_id"] != authority)
    with container.db.session_factory() as session:
        copy_profile = session.scalar(
            select(WorkflowProfile).where(WorkflowProfile.source_key == alias["source_key"])
        )
        copy_profile.api_graph_sha256 = "d" * 64
        session.commit()
    detail = client.get("/api/workflows/" + alias["source_key"]).json()
    assert detail["source_key"] == source["source_key"]
    assert detail["revision"] == source["revision"]
    payload = text_payload(client) if kind == "text" else generation_payload(client, "canonical")
    payload["source_key"] = alias["source_key"]
    path = "/api/prompt-generations" if kind == "text" else "/api/generations"
    result = post(client, path, payload)
    assert result.status_code in {201, 202}, result.text
    assert result.json()["comfyui_instance_id"] == authority
    payload["revision"] = {**payload["revision"], "api_sha256": "d" * 64}
    assert post(client, path, payload).status_code == 409


def test_wrong_output_kind_is_rejected_at_both_execution_boundaries(routing):
    from tests.helpers import generation_payload

    client, _, gpu, cpu, _ = routing
    for path, payload in [
        ("/api/generations", text_payload(client)),
        ("/api/prompt-generations", generation_payload(client, "wrong stage")),
    ]:
        response = post(client, path, payload)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "source_kind_invalid"
    assert not gpu.submitted and not cpu.submitted


def test_saved_runtime_preferences_are_discarded_without_losing_controls(routing):
    from app.models import UserPreference
    from tests.conftest import csrf

    client, container, _, _, owner = routing
    with container.db.session_factory() as session:
        preference = UserPreference(
            user_id=owner,
            settings_initialized=True,
            settings_json={
                "runtime_id": "promptgen",
                "quantity": 3,
                "prompt_generation": {
                    "runtime_id": "primary",
                    "enabled": True,
                    "sources": {"old-source": {"values": {"subject_name": "Mira"}}},
                },
            },
        )
        stored = session.get(UserPreference, owner)
        stored.settings_json = preference.settings_json
        stored.settings_initialized = True
        session.commit()
    current = client.get("/api/preferences").json()
    assert "runtime_id" not in current["settings"]
    assert "runtime_id" not in current["settings"]["prompt_generation"]
    assert current["settings"]["quantity"] == 3
    assert current["settings"]["prompt_generation"]["sources"]["old-source"]["values"] == {
        "subject_name": "Mira"
    }
    old_client = {**current["settings"], "runtime_id": "promptgen"}
    old_client["prompt_generation"]["runtime_id"] = "primary"
    saved = client.put(
        "/api/preferences",
        headers={"X-CSRF-Token": csrf(client)},
        json={
            "settings": old_client,
            "expected_revision": current["revision"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert "runtime_id" not in saved.json()["settings"]
    assert "runtime_id" not in saved.json()["settings"]["prompt_generation"]


def test_restart_preserves_accepted_preparation_pins_when_assignments_change(routing):
    from app.main import create_app
    from fastapi.testclient import TestClient
    from tests.helpers import generation_payload, restore_cookie

    client, container, gpu, cpu, _ = routing
    payload = {
        "items": [
            {
                "generation": generation_payload(client, ""),
                "prompt_generation": text_payload(client),
            }
        ]
    }
    key = str(uuid4())
    accepted = post(client, "/api/generation-preparations", payload, key)
    assert accepted.status_code == 202, accepted.text
    group = accepted.json()["id"]
    # Change assignments at a real app restart, retaining both recorded adapters.
    settings = container.settings.model_copy(
        update={
            "comfyui_default_instance_id": "promptgen",
            "comfyui_text_instance_id": "primary",
            "enable_background_worker": True,
        }
    )
    with TestClient(create_app(settings)) as restarted:
        restore_cookie(restarted, client.cookies.get(settings.session_cookie_name))
        result = wait_for(
            restarted,
            "/api/generation-preparations/" + group,
            lambda value: all(item["status"] in {"accepted", "failed"} for item in value["items"]),
        )
        assert result["items"][0]["status"] == "accepted", result
        image = result["items"][0]["generation"]
        assert image["comfyui_instance_id"] == "primary"
        text = restarted.get(
            "/api/prompt-generations/" + result["items"][0]["prompt_run_id"]
        ).json()
        assert text["comfyui_instance_id"] == "promptgen"
        wait_for_status(restarted, image["id"], "succeeded", timeout=10)
        assert post(restarted, "/api/generation-preparations", payload, key).json()["id"] == group
        assert len(gpu.submitted) == len(cpu.submitted) == 1
