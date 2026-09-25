from __future__ import annotations

import json
import time
from typing import Any

from app.main import create_app
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.fake_services import LiveFakeServer, make_png
from tests.helpers import generation_payload, provision_user, wait_for_status
from tests.publication_fixtures import add_image_input, build_publication_bundle


def _wait_for_instances(
    client: TestClient,
    predicate,
    *,
    timeout: float = 6.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get("/api/comfyui-instances")
        assert response.status_code == 200, response.text
        latest = response.json()
        if predicate(latest):
            return latest
        time.sleep(0.03)
    raise AssertionError(f"ComfyUI instance status did not settle; latest={latest}")


def _instance_settings(settings_factory, primary: LiveFakeServer, worker: LiveFakeServer):
    return settings_factory(
        enable_background_worker=True,
        comfyui_instances=[
            {
                "id": "primary",
                "label": "Primary",
                "base_url": primary.base_url,
                "ws_url": primary.ws_url,
                "user": "fixture-user",
                "concurrency": 1,
            },
            {
                "id": "worker-2",
                "label": "Secondary",
                "base_url": worker.base_url,
                "ws_url": worker.ws_url,
                "user": "fixture-user",
                "concurrency": 1,
            },
        ],
        comfyui_default_instance_id="primary",
    )


def test_legacy_instance_catalog_reports_friendly_fallback_without_private_urls(
    settings_factory,
    fake_services: LiveFakeServer,
    fake_state,
) -> None:
    del fake_state
    settings = settings_factory()
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="runtime.legacy")
        response = client.get("/api/comfyui-instances")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["configuration_mode"] == "legacy"
        assert payload["default_instance_id"] == "test-instance"
        assert [(item["id"], item["label"]) for item in payload["items"]] == [
            ("test-instance", "Primary")
        ]
        serialized = json.dumps(payload)
        assert fake_services.base_url not in serialized
        assert "fixture-user" not in serialized


def test_image_operations_use_only_the_assigned_service(
    settings_factory, fake_services, fake_state
):
    publication = build_publication_bundle("krea", mutate_artifacts=add_image_input)
    fake_state.workflow_files.update(publication.files)
    fake_state.wait_for_cancel_prompts.add("held image")
    with LiveFakeServer() as worker:
        worker.state.workflow_files = dict(fake_state.workflow_files)
        settings = _instance_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="runtime.router")
            _wait_for_instances(
                client, lambda value: all(item["available"] for item in value["items"])
            )
            upload = client.post(
                "/api/uploads/reference-images",
                headers={"X-CSRF-Token": csrf(client)},
                files={"file": ("reference.png", make_png("input"), "image/png")},
            ).json()
            payload = generation_payload(client, "assigned image", seed=414)
            payload["parameters"]["reference_image"] = {"asset_id": upload["id"]}
            rejected = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json={**payload, "comfyui_instance_id": "worker-2"},
            )
            assert rejected.status_code == 409
            assert rejected.json()["error"]["code"] == "runtime_assignment_conflict"
            queued = client.post(
                "/api/generations", headers={"X-CSRF-Token": csrf(client)}, json=payload
            )
            assert queued.status_code == 201, queued.text
            complete = wait_for_status(client, queued.json()["id"], "succeeded", timeout=10)
            assert complete["comfyui_instance_id"] == "primary"
            assert fake_state.submitted and fake_state.uploaded and fake_state.history_calls
            assert "/view" in fake_state.http_request_paths
            assert not worker.state.submitted and not worker.state.uploaded
            recalled = client.get(f"/api/generations/{complete['id']}/recall").json()
            assert recalled["comfyui_instance_id"] == "primary"
            payload["parameters"]["prompt"] = "held image"
            held_payload = generation_payload(client, "held image", seed=415)
            held_payload["parameters"]["reference_image"] = {"asset_id": upload["id"]}
            held = client.post(
                "/api/generations", headers={"X-CSRF-Token": csrf(client)}, json=held_payload
            ).json()
            running = wait_for_status(client, held["id"], "running", timeout=10)
            cancelled = client.post(
                f"/api/generations/{held['id']}/cancel", headers={"X-CSRF-Token": csrf(client)}
            )
            assert cancelled.status_code == 200, cancelled.text
            wait_for_status(
                client,
                held["id"],
                "cancelled_with_artifacts",
                "cancelled_without_artifacts",
                timeout=10,
            )
            assert running["prompt_id"] in fake_state.cancelled_prompt_ids
            assert not worker.state.cancelled_prompt_ids


def test_cached_image_catalog_waits_for_gpu_without_using_healthy_copy(
    settings_factory, fake_services, fake_state
):
    with LiveFakeServer() as worker:
        worker.state.workflow_files = dict(fake_state.workflow_files)
        settings = _instance_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="runtime.cached")
            _wait_for_instances(
                client, lambda value: all(item["available"] for item in value["items"])
            )
            payload = generation_payload(client, "wait for assigned GPU", seed=903)
            fake_state.service_available = False
            _wait_for_instances(
                client,
                lambda value: (
                    not next(item for item in value["items"] if item["id"] == "primary")[
                        "available"
                    ]
                ),
            )
            accepted = client.post(
                "/api/generations", headers={"X-CSRF-Token": csrf(client)}, json=payload
            )
            assert accepted.status_code == 201, accepted.text
            assert accepted.json()["comfyui_instance_id"] == "primary"
            assert accepted.json()["status"] == "queued"
            assert worker.state.submitted == []
            fake_state.service_available = True
            result = wait_for_status(client, accepted.json()["id"], "succeeded", timeout=10)
            assert result["comfyui_instance_id"] == "primary"
            assert fake_state.submitted and not worker.state.submitted
