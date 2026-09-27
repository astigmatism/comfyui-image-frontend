from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any

import pytest
from app.errors import AppError
from app.main import create_app
from app.models import LoraOperation
from app.services.lora_operations import _graph_references_file
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.helpers import provision_user, ready_admin
from tests.integration.test_checkpoint_batch_eta import _moody_payload
from tests.publication_fixtures import add_lora_stack, build_publication_bundle

SECRET = "test-lora-management-secret-0123456789"


def _source(client: TestClient) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get("/api/workflows")
        assert response.status_code == 200, response.text
        match = next(
            (item for item in response.json() if item["display_name"] == "Moody Krea 2 Mix V4"),
            None,
        )
        if match and match["readiness"] != "loading":
            return match
        time.sleep(0.01)
    raise AssertionError("LoRA fixture source was not discovered")


def _request_payload(source: dict[str, Any], kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "source_key": source["source_key"],
        "expected_revision": source["revision"],
        "idempotency_key": str(uuid.uuid4()),
        **extra,
    }


def _mock_preflight(service, client: TestClient):
    async def preflight(source_key: str, expected_revision=None):
        source_id, profiles = service._profiles(source_key)
        revision = {
            "publication_id": profiles[0].publication_id,
            "workflow_sha256": profiles[0].ui_graph_sha256,
            "api_sha256": profiles[0].api_graph_sha256,
            "manifest_sha256": profiles[0].manifest_sha256,
        }
        if expected_revision is not None and revision != expected_revision:
            raise AppError("source_republished", "Source changed.", status_code=409)
        instance_id = str(profiles[0].instance_id)
        return (
            source_id,
            profiles,
            {
                instance_id: {
                    "revision": revision,
                    "files": [
                        {"id": "a", "filename": "private/a.safetensors"},
                        {"id": "b", "filename": "private/b.safetensors"},
                    ],
                }
            },
            instance_id,
        )

    service._preflight = preflight


def test_admin_lora_catalog_is_public_only_and_disabled_without_secret(
    fake_state, settings_factory
):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory())) as client:
        ready_admin(client)
        source = _source(client)
        response = client.get(f"/api/admin/workflows/{source['source_key']}/loras")
        assert response.status_code == 200, response.text
        catalog = response.json()
        assert catalog["eligible"] is False
        assert catalog["revision"] == source["revision"]
        assert [item["label"] for item in catalog["items"]] == ["Alpha", "Beta"]
        assert "private/a.safetensors" not in response.text
        request = _request_payload(source, "remove", lora_id="a")
        assert client.post("/api/admin/lora-operations", json=request).status_code == 403
        rejected = client.post(
            "/api/admin/lora-operations", json=request, headers={"X-CSRF-Token": csrf(client)}
        )
        assert rejected.status_code == 503


def test_lora_operation_idempotency_and_stale_revision(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service, client)
        service._schedule = lambda _operation_id: None
        payload = _request_payload(source, "remove", lora_id="a")
        headers = {"X-CSRF-Token": csrf(client)}
        created = client.post("/api/admin/lora-operations", json=payload, headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "running"
        same = client.post("/api/admin/lora-operations", json=payload, headers=headers)
        assert same.status_code == 201
        assert same.json()["id"] == created.json()["id"]
        conflicting = client.post(
            "/api/admin/lora-operations",
            json={**payload, "lora_id": "b"},
            headers=headers,
        )
        assert conflicting.status_code == 409
        assert conflicting.json()["error"]["code"] == "idempotency_conflict"
        stale = client.post(
            "/api/admin/lora-operations",
            json={
                **payload,
                "idempotency_key": str(uuid.uuid4()),
                "expected_revision": {**source["revision"], "api_sha256": "0" * 64},
            },
            headers=headers,
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "source_republished"


def test_lora_edit_validates_metadata_and_rejects_unchanged_or_stale_requests(
    fake_state, settings_factory
):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service, client)
        service._schedule = lambda _operation_id: None
        headers = {"X-CSRF-Token": csrf(client)}
        request = _request_payload(
            source, "edit", lora_id="a", display_name=" Alpha ", trigger_word="  "
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
                "/api/admin/lora-operations",
                json={**request, **invalid},
                headers=headers,
            )
            assert rejected.status_code == 422, rejected.text
        missing = client.post(
            "/api/admin/lora-operations",
            json={**request, "lora_id": "missing", "display_name": "Updated"},
            headers=headers,
        )
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "lora_not_found"
        stale = client.post(
            "/api/admin/lora-operations",
            json={
                **request,
                "display_name": "Updated",
                "expected_revision": {**source["revision"], "api_sha256": "0" * 64},
            },
            headers=headers,
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "source_republished"
        trigger_only = client.post(
            "/api/admin/lora-operations",
            json={**request, "trigger_word": "  NewTrigger  "},
            headers=headers,
        )
        assert trigger_only.status_code == 201, trigger_only.text
        with client.app.state.container.db.session_factory() as session:
            row = session.get(LoraOperation, trigger_only.json()["id"])
            assert row.request_json["display_name"] == "Alpha"
            assert row.request_json["trigger_word"] == "NewTrigger"


def test_lora_edit_republishes_metadata_without_upload_or_weight_removal(
    fake_state, settings_factory
):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service, client)
        service._schedule = lambda _operation_id: None
        request = _request_payload(
            source, "edit", lora_id="a", display_name="  New Alpha  ", trigger_word="  "
        )
        headers = {"X-CSRF-Token": csrf(client)}
        created = client.post("/api/admin/lora-operations", json=request, headers=headers)
        assert created.status_code == 201, created.text
        operation_id = created.json()["id"]
        assert created.json()["status"] == "running"
        assert (
            client.post("/api/admin/lora-operations", json=request, headers=headers).json()["id"]
            == operation_id
        )
        upload = client.put(
            f"/api/admin/lora-operations/{operation_id}/file",
            content=b"weight",
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert upload.status_code == 409

        candidate = {**source["revision"], "publication_id": str(uuid.uuid4())}
        calls: list[tuple[str, str, dict[str, Any] | None]] = []
        inventory: list[tuple[str, bool]] = []

        async def fake_request(_adapter, method, path, *, json_body=None, **_kwargs):
            calls.append((method, path, json_body))
            if path.endswith("/prepare"):
                return {"candidate_revision": candidate}
            if path.endswith(("/commit", "/finalize")):
                return {}
            raise AssertionError((method, path))

        async def fake_candidate(*_args):
            return candidate

        async def fake_verify(*_args, **_kwargs):
            return None

        async def fake_inventory(filename, _profiles, *, present):
            inventory.append((filename, present))

        async def fake_refresh(*_args, **_kwargs):
            return []

        service._request = fake_request
        service._candidate = fake_candidate
        service._verify_publication = fake_verify
        service._verify_model_inventory = fake_inventory
        service._check_active_generations = lambda _filename: pytest.fail(
            "Metadata edits should not block on active generations."
        )
        service.container.registry.refresh = fake_refresh
        service._reconcile_after_success = lambda *_args: None
        asyncio.run(service._run_guarded(operation_id))
        assert [path.rsplit("/", 1)[-1] for _, path, _ in calls] == [
            "prepare",
            "commit",
            "finalize",
        ]
        assert calls[0][2]["change"] == {
            "action": "edit",
            "id": "a",
            "label": "New Alpha",
            "trigger_word": "",
        }
        assert inventory == [("private/a.safetensors", True)]
        finished = client.get(f"/api/admin/lora-operations/{operation_id}").json()
        assert finished["status"] == "succeeded"
        assert finished["message"] == "LoRA details updated."
        assert finished["result"] == {"revision": candidate, "lora_id": "a"}


def test_lora_upload_streams_to_companion_without_app_file(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service, client)
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
            source,
            "install",
            filename="new.safetensors",
            display_name="New LoRA",
            trigger_word="newlora",
        )
        headers = {"X-CSRF-Token": csrf(client)}
        created = client.post("/api/admin/lora-operations", json=request, headers=headers)
        assert created.status_code == 201, created.text
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


def test_removal_blocks_zero_strength_frozen_generation(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        generation_request = _moody_payload(
            client,
            "zero strength still frozen",
            checkpoint="v4_int8",
            loras=[{"id": "a", "strength": 0}, {"id": "b", "strength": 0}],
        )
        generation = client.post(
            "/api/generations",
            json=generation_request,
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert generation.status_code == 201, generation.text
        service = client.app.state.container.lora_operations
        try:
            service._check_active_generations("private/a.safetensors")
        except AppError as exc:
            assert exc.code == "lora_active_generation"
        else:
            raise AssertionError("zero-strength frozen catalog was not checked")
        _mock_preflight(service, client)
        service._schedule = lambda _operation_id: None
        removal = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(source, "remove", lora_id="a"),
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert removal.status_code == 201, removal.text
        blocked = client.post(
            "/api/generations",
            json={
                **generation_request,
                "parameters": {**generation_request["parameters"], "seed": 245921},
            },
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "lora_maintenance"


def test_partial_commit_rolls_back_operation(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service, client)
        service._schedule = lambda _operation_id: None
        payload = _request_payload(source, "remove", lora_id="a")
        created = client.post(
            "/api/admin/lora-operations",
            json=payload,
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert created.status_code == 201, created.text
        operation_id = created.json()["id"]
        rolled_back: list[str] = []

        async def fake_request(_adapter, method, path, **_kwargs):
            if path.endswith("/prepare"):
                return {"candidate_revision": source["revision"]}
            if path.endswith("/commit"):
                raise AppError("replica_commit_failed", "Replica commit failed.", status_code=503)
            raise AssertionError((method, path))

        async def candidate(*_args):
            return source["revision"]

        async def rollback(operation, _replicas, _writer):
            rolled_back.append(operation)
            return True

        service._request = fake_request
        service._candidate = candidate
        service._rollback = rollback
        asyncio.run(service._run_guarded(operation_id))
        assert rolled_back == [operation_id]
        assert client.get(f"/api/admin/lora-operations/{operation_id}").json()["status"] == "failed"
        with client.app.state.container.db.session_factory() as session:
            assert session.get(LoraOperation, operation_id).status == "failed"


def test_lost_prepare_reply_still_rolls_back_removal(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service, client)
        service._schedule = lambda _operation_id: None
        created = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(source, "remove", lora_id="a"),
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert created.status_code == 201, created.text
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
def test_finalized_lora_change_stays_recoverable_after_app_failure(
    fake_state, settings_factory, kind, failure_point
):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service, client)
        service._schedule = lambda _operation_id: None
        request = (
            _request_payload(
                source,
                "install",
                filename="new.safetensors",
                display_name="New LoRA",
                trigger_word="newlora",
            )
            if kind == "install"
            else _request_payload(
                source,
                kind,
                lora_id="a",
                **({"display_name": "New Alpha", "trigger_word": ""} if kind == "edit" else {}),
            )
        )
        created = client.post(
            "/api/admin/lora-operations",
            json=request,
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert created.status_code == 201, created.text
        operation_id = created.json()["id"]
        if kind == "install":
            with client.app.state.container.db.session_factory() as session:
                row = session.get(LoraOperation, operation_id)
                row.status = "running"
                row.internal_json = {
                    "model_filename": f"cif-managed/{operation_id}.safetensors",
                    "model_sha256": "0" * 64,
                }
                session.commit()
        candidate = {**source["revision"], "publication_id": str(uuid.uuid4())}
        calls: list[str] = []

        async def fake_request(_adapter, method, path, **_kwargs):
            calls.append(path)
            if path.endswith("/prepare"):
                return {"candidate_revision": candidate}
            if path in {"/capabilities", f"/operations/{operation_id}"}:
                return {"model_writer": True, "state": "finalized"}
            if path == "/bundle":
                return {"revision": candidate}
            if path.endswith(("/commit", "/quarantine", "/finalize")):
                return {}
            raise AssertionError((method, path))

        async def prepared_candidate(*_args):
            return candidate

        async def no_verify(*_args, **_kwargs):
            return None

        async def failing_refresh(*_args, **_kwargs):
            if failure_point == "refresh":
                raise AppError("refresh_failed", "Refresh failed.", status_code=503)
            return []

        def failing_reconcile(*_args):
            if failure_point == "reconcile":
                raise AppError("reconcile_failed", "Reconciliation failed.", status_code=503)

        service._request = fake_request
        service._candidate = prepared_candidate
        service._check_active_generations = lambda _filename: None
        service._verify_publication = no_verify
        service._verify_model_inventory = no_verify
        service.container.registry.refresh = failing_refresh
        service._reconcile_after_success = failing_reconcile
        asyncio.run(service._run_guarded(operation_id))
        assert any(path.endswith("/finalize") for path in calls)
        assert not any(path.endswith("/rollback") for path in calls)
        pending = client.get(f"/api/admin/lora-operations/{operation_id}").json()
        assert pending["status"] == "repair_required"

        async def recovered_refresh(*_args, **_kwargs):
            return []

        reconciled: list[str] = []
        service.container.registry.refresh = recovered_refresh
        service._reconcile_after_success = lambda source_id, _change: reconciled.append(source_id)
        asyncio.run(service.recover())
        result = client.get(f"/api/admin/lora-operations/{operation_id}").json()
        assert result["status"] == "succeeded"
        assert result["result"]["revision"] == candidate
        assert reconciled
        assert not any(path.endswith("/rollback") for path in calls)


def test_lora_admin_routes_require_administrator(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        provision_user(client, username="lora.admin.denied")
        source = _source(client)
        assert client.get(f"/api/admin/workflows/{source['source_key']}/loras").status_code == 403
        assert client.get(f"/api/admin/lora-operations/{uuid.uuid4()}").status_code == 403
        response = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(source, "remove", lora_id="a"),
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert response.status_code == 403


def test_pending_upload_can_be_cancelled_without_a_companion_stage(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service, client)
        calls: list[str] = []

        async def fake_request(_adapter, method, path, **_kwargs):
            calls.append(path)
            if path == "/capabilities":
                return {"model_writer": True}
            if path == "/bundle":
                return {"revision": source["revision"]}
            if path.endswith("/rollback"):
                return {"state": "rolled_back"}
            raise AssertionError((method, path))

        service._request = fake_request
        created = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(
                source,
                "install",
                filename="new.safetensors",
                display_name="New LoRA",
                trigger_word="newlora",
            ),
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert created.status_code == 201, created.text
        operation_id = created.json()["id"]
        assert client.post(f"/api/admin/lora-operations/{operation_id}/cancel").status_code == 403
        cancelled = client.post(
            f"/api/admin/lora-operations/{operation_id}/cancel",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "failed"
        assert any(path.endswith("/rollback") for path in calls)


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
    try:
        _graph_references_file(unreadable, "private/a.safetensors")
    except AppError as exc:
        assert exc.code == "lora_active_graph_uncertain"
    else:
        raise AssertionError("unreadable active catalog was not blocked")


@pytest.mark.parametrize("initial_status", ["running", "repair_required"])
def test_restart_recovers_finalized_publication_instead_of_rolling_back(
    fake_state, settings_factory, initial_status
):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(lora_management_secret=SECRET))) as client:
        ready_admin(client)
        source = _source(client)
        service = client.app.state.container.lora_operations
        _mock_preflight(service, client)
        service._schedule = lambda _operation_id: None
        created = client.post(
            "/api/admin/lora-operations",
            json=_request_payload(source, "remove", lora_id="a"),
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert created.status_code == 201, created.text
        operation_id = created.json()["id"]
        candidate = {**source["revision"], "publication_id": str(uuid.uuid4())}
        with client.app.state.container.db.session_factory() as session:
            row = session.get(LoraOperation, operation_id)
            row.status = initial_status
            row.internal_json = {
                "change": {"action": "remove", "id": "a"},
                "candidate_revision": candidate,
                "model_filename": "private/a.safetensors",
            }
            session.commit()
        reconciled: list[str] = []

        async def fake_request(_adapter, method, path, **_kwargs):
            if path == "/capabilities":
                return {"model_writer": True}
            if path == f"/operations/{operation_id}":
                return {"state": "finalized"}
            if path == "/bundle":
                return {"revision": candidate}
            raise AssertionError((method, path))

        async def no_refresh(*_args, **_kwargs):
            return []

        async def no_inventory(*_args, **_kwargs):
            return None

        service._request = fake_request
        service.container.registry.refresh = no_refresh
        service._verify_model_inventory = no_inventory
        service._reconcile_after_success = lambda _source, _change: reconciled.append(_source)
        asyncio.run(service.recover())
        operation = client.get(f"/api/admin/lora-operations/{operation_id}")
        assert operation.status_code == 200
        assert operation.json()["status"] == "succeeded"
        assert operation.json()["result"]["revision"] == candidate
        assert reconciled
