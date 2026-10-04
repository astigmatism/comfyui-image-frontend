from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any

import pytest
from app.errors import AppError
from app.main import create_app
from app.models import LoraOperation, LoraOperationTarget
from app.services.lora_operations import _graph_references_file, _Target
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import csrf
from tests.helpers import provision_user, ready_admin
from tests.integration.test_checkpoint_batch_eta import _moody_payload
from tests.publication_fixtures import moody_lora_library_files

SECRET = "test-lora-management-secret-0123456789"
NAMES = ("Moody Krea 2 Mix V4", "Moody Krea 2 Mix V4 Minimal")


def _sources(client: TestClient) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get("/api/workflows")
        assert response.status_code == 200, response.text
        found = [item for item in response.json() if item["display_name"] in NAMES]
        if len(found) == 2 and all(item["readiness"] != "loading" for item in found):
            return sorted(found, key=lambda item: item["display_name"])
        time.sleep(0.01)
    raise AssertionError("LoRA library fixture sources were not discovered")


def _library(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/admin/lora-library")
    assert response.status_code == 200, response.text
    libraries = response.json()["libraries"]
    assert [library["key"] for library in libraries] == ["krea2"]
    return libraries[0]


def _request_payload(library: dict[str, Any], kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "library": library["key"],
        "expected_library": library["expected_library"],
        "idempotency_key": str(uuid.uuid4()),
        **extra,
    }


def _revision(profile) -> dict[str, str]:
    return {
        "publication_id": profile.publication_id,
        "workflow_sha256": profile.ui_graph_sha256,
        "api_sha256": profile.api_graph_sha256,
        "manifest_sha256": profile.manifest_sha256,
    }


def _mock_preflight(service, files: list[dict[str, str]] | None = None):
    files = files or [
        {"id": "a", "filename": "private/a.safetensors"},
        {"id": "b", "filename": "private/b.safetensors"},
    ]

    async def preflight(targets, *, check_expected: bool = True):
        resolved, bundles, writer = [], {}, ""
        for target in targets:
            source_id, profiles = service._profiles(target.source_key)
            revision = _revision(profiles[0])
            if check_expected and revision != target.expected:
                raise AppError("library_changed", "Library changed.", status_code=409)
            writer = str(profiles[0].instance_id)
            bundles[(writer, source_id)] = {"revision": revision, "files": files}
            resolved.append(
                _Target(source_id, target.source_key, target.expected, target.candidate, profiles)
            )
        return resolved, bundles, writer

    service._preflight = preflight


def _client(fake_state, settings_factory, **settings) -> TestClient:
    fake_state.workflow_files = moody_lora_library_files()
    return TestClient(create_app(settings_factory(**settings)))


def _targets(client: TestClient, operation_id: str) -> list[LoraOperationTarget]:
    with client.app.state.container.db.session_factory() as session:
        return list(
            session.scalars(
                select(LoraOperationTarget)
                .where(LoraOperationTarget.operation_id == operation_id)
                .order_by(LoraOperationTarget.position)
            )
        )


def _fake_run(service, client, operation_id, *, fail_commit=False, finalize_state="finalized"):
    """Replace companion I/O with a recorder that prepares one candidate per target."""

    candidates: dict[str, dict[str, str]] = {}
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    for target in _targets(client, operation_id):
        candidates[target.source_id] = {
            **target.expected_revision_json,
            "publication_id": str(uuid.uuid4()),
        }

    async def fake_request(_adapter, method, path, *, json_body=None, **_kwargs):
        calls.append((method, path, json_body))
        if path.endswith("/prepare"):
            return {
                "targets": [
                    {"source_path": source_id, "candidate_revision": revision}
                    for source_id, revision in candidates.items()
                ]
            }
        if path.endswith("/commit") and fail_commit:
            raise AppError("replica_commit_failed", "Replica commit failed.", status_code=503)
        if path.endswith(("/commit", "/quarantine", "/finalize")):
            return {}
        if path == "/capabilities":
            return {"model_writer": True}
        if path == f"/operations/{operation_id}":
            return {"state": finalize_state}
        if path == "/bundle":
            return {"revision": candidates[_kwargs["params"]["source_path"]]}
        raise AssertionError((method, path))

    async def fake_candidate(_adapter, _operation_id, source_id, _instance_id):
        return candidates[source_id]

    async def no_op(*_args, **_kwargs):
        return None

    async def no_refresh(*_args, **_kwargs):
        return []

    service._request = fake_request
    service._candidate = fake_candidate
    service._verify_publications = no_op
    service._verify_model_inventory = no_op
    service.container.registry.refresh = no_refresh
    return candidates, calls


def test_library_view_is_public_only_and_disabled_without_secret(fake_state, settings_factory):
    with _client(fake_state, settings_factory) as client:
        ready_admin(client)
        sources = _sources(client)
        response = client.get("/api/admin/lora-library")
        assert response.status_code == 200, response.text
        assert "private/a.safetensors" not in response.text
        library = _library(client)
        assert library["label"] == "Krea 2"
        assert library["in_sync"] is True and library["can_sync"] is False
        assert library["eligible"] is False
        assert library["reason"] == "LoRA administration is not enabled on this server."
        assert [item["label"] for item in library["items"]] == ["Alpha", "Beta"]
        assert all(item["lora_identity"].startswith("lr1_") for item in library["items"])
        assert {member["source_key"] for member in library["members"]} == {
            source["source_key"] for source in sources
        }
        request = _request_payload(library, "remove", lora_id="a")
        assert client.post("/api/admin/lora-operations", json=request).status_code == 403
        rejected = client.post(
            "/api/admin/lora-operations", json=request, headers={"X-CSRF-Token": csrf(client)}
        )
        assert rejected.status_code == 503


def test_workflow_detail_exposes_the_same_identity_in_every_member(fake_state, settings_factory):
    with _client(fake_state, settings_factory) as client:
        ready_admin(client)
        identities = []
        for source in _sources(client):
            detail = client.get(f"/api/workflows/{source['source_key']}").json()
            stack = next(item for item in detail["interface"]["inputs"] if item["id"] == "loras")
            identities.append([item["lora_identity"] for item in stack["items"]])
            assert "private/" not in str(stack)
        assert identities[0] == identities[1]


def test_operation_targets_every_member_with_idempotency_and_stale_library(
    fake_state, settings_factory
):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        library = _library(client)
        payload = _request_payload(library, "remove", lora_id="a")
        headers = {"X-CSRF-Token": csrf(client)}
        created = client.post("/api/admin/lora-operations", json=payload, headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "running"
        targets = _targets(client, created.json()["id"])
        assert {target.source_key for target in targets} == {
            member["source_key"] for member in library["members"]
        }
        same = client.post("/api/admin/lora-operations", json=payload, headers=headers)
        assert same.json()["id"] == created.json()["id"]
        conflicting = client.post(
            "/api/admin/lora-operations", json={**payload, "lora_id": "b"}, headers=headers
        )
        assert conflicting.status_code == 409
        assert conflicting.json()["error"]["code"] == "idempotency_conflict"
        busy = client.post(
            "/api/admin/lora-operations",
            json={**payload, "idempotency_key": str(uuid.uuid4()), "lora_id": "b"},
            headers=headers,
        )
        assert busy.status_code == 409
        assert busy.json()["error"]["code"] == "lora_operation_in_progress"
        stale_library = [
            {**member, "revision": {**member["revision"], "api_sha256": "0" * 64}}
            for member in library["expected_library"]
        ]
        stale = client.post(
            "/api/admin/lora-operations",
            json={
                **payload,
                "idempotency_key": str(uuid.uuid4()),
                "expected_library": stale_library,
            },
            headers=headers,
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "library_changed"
        partial = client.post(
            "/api/admin/lora-operations",
            json={
                **payload,
                "idempotency_key": str(uuid.uuid4()),
                "expected_library": library["expected_library"][:1],
            },
            headers=headers,
        )
        assert partial.json()["error"]["code"] == "library_changed"


def test_edit_validates_metadata_and_rejects_unchanged_requests(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        library = _library(client)
        headers = {"X-CSRF-Token": csrf(client)}
        request = _request_payload(
            library, "edit", lora_id="a", display_name=" Alpha ", trigger_word="  "
        )
        unchanged = client.post("/api/admin/lora-operations", json=request, headers=headers)
        assert unchanged.status_code == 409
        assert unchanged.json()["error"]["code"] == "lora_edit_unchanged"
        for invalid in (
            {"display_name": "   "},
            {"trigger_word": None},
            {"filename": "a.safetensors"},
        ):
            rejected = client.post(
                "/api/admin/lora-operations", json={**request, **invalid}, headers=headers
            )
            assert rejected.status_code == 422, rejected.text
        missing = client.post(
            "/api/admin/lora-operations",
            json={**request, "lora_id": "missing", "display_name": "Updated"},
            headers=headers,
        )
        assert missing.json()["error"]["code"] == "lora_not_found"
        sync = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(library, "sync"),
            headers=headers,
        )
        assert sync.json()["error"]["code"] == "lora_library_in_sync"
        trigger_only = client.post(
            "/api/admin/lora-operations",
            json={**request, "trigger_word": "  NewTrigger  "},
            headers=headers,
        )
        assert trigger_only.status_code == 201, trigger_only.text
        with client.app.state.container.db.session_factory() as session:
            row = session.get(LoraOperation, trigger_only.json()["id"])
            assert row.scope == "library"
            assert row.request_json["display_name"] == "Alpha"
            assert row.request_json["trigger_word"] == "NewTrigger"


def test_edit_republishes_every_member_in_one_companion_operation(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        library = _library(client)
        request = _request_payload(
            library, "edit", lora_id="a", display_name="  New Alpha  ", trigger_word="  "
        )
        headers = {"X-CSRF-Token": csrf(client)}
        created = client.post("/api/admin/lora-operations", json=request, headers=headers)
        assert created.status_code == 201, created.text
        operation_id = created.json()["id"]
        upload = client.put(
            f"/api/admin/lora-operations/{operation_id}/file",
            content=b"weight",
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 409
        candidates, calls = _fake_run(service, client, operation_id)
        inventory: list[tuple[str, bool]] = []

        async def fake_inventory(filename, *, present):
            inventory.append((filename, present))

        service._verify_model_inventory = fake_inventory
        service._check_active_generations = lambda _filename: pytest.fail(
            "Metadata edits should not block on active generations."
        )
        reconciled: list[int] = []
        service._reconcile_after_success = lambda targets, *_args: reconciled.append(len(targets))
        asyncio.run(service._run_guarded(operation_id))
        assert [path.rsplit("/", 1)[-1] for _, path, _ in calls] == [
            "prepare",
            "commit",
            "finalize",
        ]
        body = calls[0][2]
        assert body["change"] == {
            "action": "edit",
            "id": "a",
            "label": "New Alpha",
            "trigger_word": "",
        }
        assert sorted(target["source_path"] for target in body["targets"]) == sorted(candidates)
        assert len({target["publication_id"] for target in body["targets"]}) == 2
        assert inventory == [("private/a.safetensors", True)]
        assert reconciled == [2]
        finished = client.get(f"/api/admin/lora-operations/{operation_id}").json()
        assert finished["status"] == "succeeded"
        assert finished["message"] == "LoRA details updated in every library workflow."
        assert finished["result"]["lora_id"] == "a" and finished["result"]["workflows"] == 2
        recorded = {
            target.source_id: target.candidate_revision_json
            for target in _targets(client, operation_id)
        }
        assert recorded == candidates


def test_library_files_must_agree_before_a_removal(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        service._schedule = lambda _operation_id: None
        _mock_preflight(service)
        library = _library(client)
        created = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(library, "remove", lora_id="a"),
            headers={"X-CSRF-Token": csrf(client)},
        )
        operation_id = created.json()["id"]
        original = service._preflight

        async def diverged(targets, *, check_expected=True):
            resolved, bundles, writer = await original(targets, check_expected=check_expected)
            last = sorted(bundles)[-1]
            bundles[last] = {
                **bundles[last],
                "files": [{"id": "a", "filename": "private/other.safetensors"}],
            }
            return resolved, bundles, writer

        service._preflight = diverged
        asyncio.run(service._run_guarded(operation_id))
        result = client.get(f"/api/admin/lora-operations/{operation_id}").json()
        assert result["status"] == "failed"
        assert result["blockers"] == ["The library workflows name different files for this LoRA."]


def test_out_of_sync_library_must_sync_first_and_sync_targets_only_drifted_members(
    fake_state, settings_factory
):
    def drop_beta(manifest, workflow, api):
        import json

        catalog = json.loads(api["99"]["inputs"]["catalog_json"])[:1]
        api["99"]["inputs"]["catalog_json"] = json.dumps(catalog)
        api["99"]["inputs"]["value"] = json.dumps([{"id": "a", "strength": 0}])
        stack = next(item for item in manifest["interface"]["inputs"] if item["id"] == "loras")
        stack["items"] = stack["items"][:1]
        stack["default"] = [{"id": "a", "strength": 0}]
        for entry in manifest.get("technical_inventory", {}).get("loras", []):
            if entry.get("parameter_id") == "loras":
                entry["items"] = entry["items"][:1]
                entry["default"] = [{"id": "a", "strength": 0}]

    fake_state.workflow_files = moody_lora_library_files(second_mutation=drop_beta)
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        sources = _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        library = _library(client)
        assert library["in_sync"] is False and library["can_sync"] is True
        assert library["eligible"] is False
        assert [item["label"] for item in library["items"]] == ["Alpha", "Beta"]
        minimal = next(m for m in library["members"] if m["display_name"].endswith("Minimal"))
        assert (minimal["in_sync"], minimal["missing_count"]) == (False, 1)
        headers = {"X-CSRF-Token": csrf(client)}
        blocked = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(library, "remove", lora_id="a"),
            headers=headers,
        )
        assert blocked.json()["error"]["code"] == "lora_library_out_of_sync"
        created = client.post(
            "/api/admin/lora-operations", json=_request_payload(library, "sync"), headers=headers
        )
        assert created.status_code == 201, created.text
        operation_id = created.json()["id"]
        assert [target.source_key for target in _targets(client, operation_id)] == [
            minimal["source_key"]
        ]
        assert minimal["source_key"] in {source["source_key"] for source in sources}
        _, calls = _fake_run(service, client, operation_id)
        service._reconcile_after_success = lambda *_args: None
        asyncio.run(service._run_guarded(operation_id))
        change = calls[0][2]["change"]
        assert change["action"] == "set_catalog"
        assert [item["id"] for item in change["items"]] == ["a", "b"]
        assert change["items"][1]["filename"] == "private/b.safetensors"
        result = client.get(f"/api/admin/lora-operations/{operation_id}").json()
        assert result["status"] == "succeeded"
        assert result["message"] == "Library workflows now share the same LoRAs."


def test_companion_without_library_support_requires_an_upgrade(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations

        async def old_companion(_adapter, method, path, **_kwargs):
            assert (method, path) == ("GET", "/capabilities")
            return {"enabled": True, "version": 1, "model_writer": True, "model_root_id": "x"}

        service._request = old_companion
        library = _library(client)
        assert library["eligible"] is False
        assert (
            library["reason"] == "Upgrade the ComfyUI LoRA companion to manage the shared library."
        )


def test_lora_upload_streams_to_companion_without_app_file(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        body = b"safetensors-test-payload"
        observed: list[bytes] = []

        async def fake_request(_adapter, method, path, *, content=None, **_kwargs):
            assert method == "PUT" and path.endswith("/file")
            async for chunk in content:
                observed.append(chunk)
            return {
                "filename": f"cif-managed/{path.split('/')[2]}.safetensors",
                "sha256": hashlib.sha256(b"".join(observed)).hexdigest(),
                "size": len(b"".join(observed)),
            }

        service._request = fake_request
        request = _request_payload(
            _library(client),
            "install",
            filename="new.safetensors",
            display_name="New LoRA",
            trigger_word="newlora",
        )
        headers = {"X-CSRF-Token": csrf(client)}
        created = client.post("/api/admin/lora-operations", json=request, headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "awaiting_upload"
        operation_id = created.json()["id"]
        sent = client.put(
            f"/api/admin/lora-operations/{operation_id}/file",
            content=body,
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "running"
        assert b"".join(observed) == body
        assert not (
            client.app.state.container.settings.data_dir / "lora-operation-staging"
        ).exists()


def test_removal_blocks_frozen_generation_and_every_member_during_removal(
    fake_state, settings_factory
):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        sources = _sources(client)
        generation_request = _moody_payload(
            client,
            "zero strength still frozen",
            checkpoint="v4_int8",
            loras=[{"id": "a", "strength": 0}, {"id": "b", "strength": 0}],
        )
        generation = client.post(
            "/api/generations", json=generation_request, headers={"X-CSRF-Token": csrf(client)}
        )
        assert generation.status_code == 201, generation.text
        service = client.app.state.container.lora_operations
        with pytest.raises(AppError) as blocked_removal:
            service._check_active_generations("private/a.safetensors")
        assert blocked_removal.value.code == "lora_active_generation"
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        removal = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(_library(client), "remove", lora_id="a"),
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert removal.status_code == 201, removal.text
        for source in sources:
            request = {
                **generation_request,
                "source_key": source["source_key"],
                "revision": source["revision"],
                "parameters": {**generation_request["parameters"], "seed": 245921},
            }
            blocked = client.post(
                "/api/generations", json=request, headers={"X-CSRF-Token": csrf(client)}
            )
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["error"]["code"] == "lora_maintenance"


def test_partial_commit_rolls_back_every_target(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        service._check_active_generations = lambda _filename: None
        created = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(_library(client), "remove", lora_id="a"),
            headers={"X-CSRF-Token": csrf(client)},
        )
        operation_id = created.json()["id"]
        rolled_back: list[str] = []
        _fake_run(service, client, operation_id, fail_commit=True)

        async def rollback(operation, _replicas, _writer):
            rolled_back.append(operation)
            return True

        service._rollback = rollback
        asyncio.run(service._run_guarded(operation_id))
        assert rolled_back == [operation_id]
        assert client.get(f"/api/admin/lora-operations/{operation_id}").json()["status"] == "failed"


def test_lost_prepare_reply_still_rolls_back_removal(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        service._check_active_generations = lambda _filename: None
        created = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(_library(client), "remove", lora_id="a"),
            headers={"X-CSRF-Token": csrf(client)},
        )
        operation_id = created.json()["id"]
        rolled_back: list[str] = []

        async def lost_reply(_adapter, method, path, **_kwargs):
            assert method == "POST" and path.endswith("/prepare")
            raise AppError("lora_companion_unavailable", "Reply was lost.", status_code=503)

        async def rollback(operation, _replicas, _writer):
            rolled_back.append(operation)
            return True

        service._request = lost_reply
        service._rollback = rollback
        asyncio.run(service._run_guarded(operation_id))
        assert rolled_back == [operation_id]
        assert client.get(f"/api/admin/lora-operations/{operation_id}").json()["status"] == "failed"


@pytest.mark.parametrize("kind", ["remove", "install", "edit"])
@pytest.mark.parametrize("failure_point", ["refresh", "reconcile"])
def test_finalized_library_change_stays_recoverable_after_app_failure(
    fake_state, settings_factory, kind, failure_point
):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        library = _library(client)
        request = (
            _request_payload(
                library,
                "install",
                filename="new.safetensors",
                display_name="New LoRA",
                trigger_word="newlora",
            )
            if kind == "install"
            else _request_payload(
                library,
                kind,
                lora_id="a",
                **({"display_name": "New Alpha", "trigger_word": ""} if kind == "edit" else {}),
            )
        )
        created = client.post(
            "/api/admin/lora-operations", json=request, headers={"X-CSRF-Token": csrf(client)}
        )
        assert created.status_code == 201, created.text
        operation_id = created.json()["id"]
        if kind == "install":
            with client.app.state.container.db.session_factory() as session:
                row = session.get(LoraOperation, operation_id)
                row.status = "running"
                row.internal_json = {
                    **row.internal_json,
                    "model_filename": f"cif-managed/{operation_id}.safetensors",
                    "model_sha256": "0" * 64,
                }
                session.commit()
        candidates, calls = _fake_run(service, client, operation_id)
        service._check_active_generations = lambda _filename: None

        async def failing_refresh(*_args, **_kwargs):
            if failure_point == "refresh":
                raise AppError("refresh_failed", "Refresh failed.", status_code=503)
            return []

        def failing_reconcile(*_args):
            if failure_point == "reconcile":
                raise AppError("reconcile_failed", "Reconciliation failed.", status_code=503)

        service.container.registry.refresh = failing_refresh
        service._reconcile_after_success = failing_reconcile
        asyncio.run(service._run_guarded(operation_id))
        assert any(path.endswith("/finalize") for _, path, _ in calls)
        assert not any(path.endswith("/rollback") for _, path, _ in calls)
        pending = client.get(f"/api/admin/lora-operations/{operation_id}").json()
        assert pending["status"] == "repair_required"

        async def recovered_refresh(*_args, **_kwargs):
            return []

        reconciled: list[int] = []
        service.container.registry.refresh = recovered_refresh
        service._reconcile_after_success = lambda targets, *_args: reconciled.append(len(targets))
        asyncio.run(service.recover())
        result = client.get(f"/api/admin/lora-operations/{operation_id}").json()
        assert result["status"] == "succeeded"
        assert result["result"]["revisions"] == {
            target.source_key: candidates[target.source_id]
            for target in _targets(client, operation_id)
        }
        assert reconciled == [2]
        assert not any(path.endswith("/rollback") for _, path, _ in calls)


def test_lora_admin_routes_require_administrator(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        provision_user(client, username="lora.admin.denied")
        _sources(client)
        assert client.get("/api/admin/lora-library").status_code == 403
        assert client.get(f"/api/admin/lora-operations/{uuid.uuid4()}").status_code == 403
        response = client.post(
            "/api/admin/lora-operations",
            json={
                "kind": "remove",
                "library": "krea2",
                "expected_library": [],
                "idempotency_key": str(uuid.uuid4()),
                "lora_id": "a",
            },
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert response.status_code in {403, 422}


def test_pending_upload_can_be_cancelled_without_a_companion_stage(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        calls: list[str] = []

        async def fake_request(_adapter, method, path, *, params=None, **_kwargs):
            calls.append(path)
            if path == "/capabilities":
                return {"model_writer": True}
            if path == "/bundle":
                target = next(
                    t
                    for t in _targets(client, operation_id)
                    if t.source_id == params["source_path"]
                )
                return {"revision": target.expected_revision_json}
            if path.endswith("/rollback"):
                return {"state": "rolled_back"}
            raise AssertionError((method, path))

        created = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(
                _library(client),
                "install",
                filename="new.safetensors",
                display_name="New LoRA",
                trigger_word="newlora",
            ),
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert created.status_code == 201, created.text
        operation_id = created.json()["id"]
        service._request = fake_request
        assert client.post(f"/api/admin/lora-operations/{operation_id}/cancel").status_code == 403
        cancelled = client.post(
            f"/api/admin/lora-operations/{operation_id}/cancel",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "failed"
        assert any(path.endswith("/rollback") for path in calls)
        assert calls.count("/bundle") == 2


def test_active_rgthree_reference_and_unreadable_stack_are_conservative():
    graph = {
        "1": {
            "class_type": "Power Lora Loader (rgthree)",
            "inputs": {
                "lora_1": {"on": False, "lora": "private/a.safetensors"},
                "lora_2": {"on": True, "lora": "private/b.safetensors"},
            },
        }
    }
    assert not _graph_references_file(graph, "private/a.safetensors")
    assert _graph_references_file(graph, "private/b.safetensors")
    unreadable = {"1": {"class_type": "CIFLoraStack", "inputs": {"catalog_json": "{"}}}
    with pytest.raises(AppError) as error:
        _graph_references_file(unreadable, "private/a.safetensors")
    assert error.value.code == "lora_active_graph_uncertain"


@pytest.mark.parametrize("initial_status", ["running", "repair_required"])
def test_restart_recovers_finalized_publication_instead_of_rolling_back(
    fake_state, settings_factory, initial_status
):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        _sources(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service)
        service._schedule = lambda _operation_id: None
        created = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(_library(client), "remove", lora_id="a"),
            headers={"X-CSRF-Token": csrf(client)},
        )
        operation_id = created.json()["id"]
        candidates, _calls = _fake_run(service, client, operation_id)
        service._update(
            operation_id,
            status=initial_status,
            internal={
                "change": {"action": "remove", "id": "a"},
                "model_filename": "private/a.safetensors",
            },
            candidates=candidates,
        )
        reconciled: list[int] = []
        service._reconcile_after_success = lambda targets, *_args: reconciled.append(len(targets))
        asyncio.run(service.recover())
        operation = client.get(f"/api/admin/lora-operations/{operation_id}").json()
        assert operation["status"] == "succeeded"
        assert reconciled == [2]


def test_operation_recorded_before_the_shared_library_still_recovers(fake_state, settings_factory):
    with _client(fake_state, settings_factory, lora_management_secret=SECRET) as client:
        ready_admin(client)
        source = _sources(client)[0]
        service = client.app.state.container.lora_operations
        candidate = {**source["revision"], "publication_id": str(uuid.uuid4())}
        with client.app.state.container.db.session_factory() as session:
            row = LoraOperation(
                actor_id="someone",
                idempotency_key=str(uuid.uuid4()),
                request_digest="0" * 64,
                source_key=source["source_key"],
                source_id="workflows/comfyui-image-frontend/Moody Krea 2 Mix V4.json",
                action="remove",
                status="running",
                expected_revision_json=source["revision"],
                request_json={"kind": "remove", "lora_id": "a"},
                internal_json={
                    "change": {"action": "remove", "id": "a"},
                    "candidate_revision": candidate,
                    "model_filename": "private/a.safetensors",
                },
                blockers_json=[],
            )
            session.add(row)
            session.commit()
            operation_id = row.id

        async def fake_request(_adapter, method, path, **_kwargs):
            if path == "/capabilities":
                return {"model_writer": True}
            if path == f"/operations/{operation_id}":
                return {"state": "finalized"}
            if path == "/bundle":
                return {"revision": candidate}
            raise AssertionError((method, path))

        async def no_op(*_args, **_kwargs):
            return []

        service._request = fake_request
        service.container.registry.refresh = no_op
        service._verify_model_inventory = no_op
        reconciled: list[int] = []
        service._reconcile_after_success = lambda targets, *_args: reconciled.append(len(targets))
        asyncio.run(service.recover())
        with client.app.state.container.db.session_factory() as session:
            assert session.get(LoraOperation, operation_id).status == "succeeded"
        assert reconciled == [1]
