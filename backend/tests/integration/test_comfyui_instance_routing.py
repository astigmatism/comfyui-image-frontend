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
                "label": "Primary ComfyUI — RTX 3090",
                "description": "24 GB VRAM",
                "base_url": primary.base_url,
                "ws_url": primary.ws_url,
                "user": "fixture-user",
                "concurrency": 1,
            },
            {
                "id": "worker-2",
                "label": "ComfyUI Worker 2 — RTX 3080",
                "description": "10 GB VRAM",
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
            ("test-instance", "Original")
        ]
        serialized = json.dumps(payload)
        assert fake_services.base_url not in serialized
        assert "fixture-user" not in serialized


def test_generation_operations_stay_on_the_pinned_comfyui_instance(
    settings_factory,
    fake_services: LiveFakeServer,
    fake_state,
) -> None:
    publication = build_publication_bundle("krea", mutate_artifacts=add_image_input)
    fake_state.workflow_files.update(publication.files)
    with LiveFakeServer() as worker:
        worker.state.reset_runtime()
        # Keep the cancellation fixture running while the independent primary lane
        # completes, even when the full suite adds scheduler contention.
        worker.state.slow_stage_delay = 2.0
        settings = _instance_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="runtime.router")
            instances = _wait_for_instances(
                client,
                lambda value: all(item["available"] for item in value["items"]),
            )
            assert instances["default_instance_id"] == "primary"
            assert instances["configuration_mode"] == "explicit"
            assert [item["id"] for item in instances["items"]] == ["primary", "worker-2"]
            serialized_instances = json.dumps(instances)
            assert fake_services.base_url not in serialized_instances
            assert worker.base_url not in serialized_instances
            assert "fixture-user" not in serialized_instances

            upload_response = client.post(
                "/api/uploads/reference-images",
                headers={"X-CSRF-Token": csrf(client)},
                files={"file": ("reference.png", make_png("worker input"), "image/png")},
            )
            assert upload_response.status_code == 200, upload_response.text
            upload = upload_response.json()
            payload = generation_payload(client, "worker-only route", seed=414)
            payload["parameters"]["reference_image"] = {"asset_id": upload["id"]}
            payload["comfyui_instance_id"] = "worker-2"

            queued = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json=payload,
            )
            assert queued.status_code == 201, queued.text
            assert queued.json()["comfyui_instance_id"] == "worker-2"
            assert queued.json()["comfyui_instance_label"] == "ComfyUI Worker 2 — RTX 3080"
            complete = wait_for_status(client, queued.json()["id"], "succeeded", timeout=10)
            assert complete["comfyui_instance_id"] == "worker-2"
            assert complete["comfyui_instance_label"] == "ComfyUI Worker 2 — RTX 3080"
            assert worker.state.submitted
            assert worker.state.uploaded
            assert worker.state.history_calls
            assert "/view" in worker.state.http_request_paths
            assert fake_state.submitted == []
            assert fake_state.uploaded == []
            assert not any(path.startswith("/history/") for path in fake_state.http_request_paths)
            assert "/view" not in fake_state.http_request_paths

            detail = client.get(f"/api/generations/{complete['id']}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["comfyui_instance_id"] == "worker-2"
            recalled = client.get(f"/api/generations/{complete['id']}/recall")
            assert recalled.status_code == 200, recalled.text
            assert recalled.json()["comfyui_instance_id"] == "worker-2"
            assert recalled.json()["comfyui_instance_configured"] is True
            assert recalled.json()["comfyui_instance_available"] is True

            primary_payload = generation_payload(client, "primary-only route", seed=415)
            primary_payload["parameters"]["reference_image"] = {"asset_id": upload["id"]}
            primary_payload["comfyui_instance_id"] = "primary"
            primary_queued = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json=primary_payload,
            )
            assert primary_queued.status_code == 201, primary_queued.text
            primary_complete = wait_for_status(
                client,
                primary_queued.json()["id"],
                "succeeded",
                timeout=10,
            )
            assert primary_complete["comfyui_instance_id"] == "primary"
            assert fake_state.submitted
            assert len(worker.state.submitted) == 1

            cancel_payload = generation_payload(client, "slow worker cancellation", seed=416)
            cancel_payload["parameters"]["reference_image"] = {"asset_id": upload["id"]}
            cancel_payload["comfyui_instance_id"] = "worker-2"
            cancel_queued = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json=cancel_payload,
            )
            assert cancel_queued.status_code == 201, cancel_queued.text
            running = wait_for_status(
                client,
                cancel_queued.json()["id"],
                "running",
                timeout=5,
            )

            # Each configured runtime owns its own local dispatch lane and native queue.
            # A slow Worker 2 prompt must not consume the primary runtime's capacity.
            parallel_primary_payload = generation_payload(client, "primary parallel lane", seed=417)
            parallel_primary_payload["parameters"]["reference_image"] = {"asset_id": upload["id"]}
            parallel_primary_payload["comfyui_instance_id"] = "primary"
            parallel_primary = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json=parallel_primary_payload,
            )
            assert parallel_primary.status_code == 201, parallel_primary.text
            parallel_primary_complete = wait_for_status(
                client,
                parallel_primary.json()["id"],
                "succeeded",
                timeout=5,
            )
            assert parallel_primary_complete["comfyui_instance_id"] == "primary"
            still_running = client.get(f"/api/generations/{running['id']}")
            assert still_running.status_code == 200, still_running.text
            assert still_running.json()["status"] == "running"

            cancelled = client.post(
                f"/api/generations/{running['id']}/cancel",
                headers={"X-CSRF-Token": csrf(client)},
            )
            assert cancelled.status_code == 200, cancelled.text
            wait_for_status(
                client,
                running["id"],
                "cancelled_with_artifacts",
                "cancelled_without_artifacts",
                timeout=10,
            )
            assert running["prompt_id"] in worker.state.cancelled_prompt_ids
            assert running["prompt_id"] not in fake_state.cancelled_prompt_ids


def test_unavailable_instance_is_explicit_and_does_not_block_another_lane(
    settings_factory,
    fake_services: LiveFakeServer,
    fake_state,
) -> None:
    with LiveFakeServer() as worker:
        worker.state.reset_runtime()
        settings = _instance_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="runtime.outage")
            _wait_for_instances(
                client,
                lambda value: all(item["available"] for item in value["items"]),
            )
            worker.state.service_available = False
            unavailable = _wait_for_instances(
                client,
                lambda value: (
                    next(item for item in value["items"] if item["id"] == "worker-2")["available"]
                    is False
                ),
            )
            worker_status = next(item for item in unavailable["items"] if item["id"] == "worker-2")
            assert worker_status["message"]

            worker_payload = generation_payload(client, "offline worker", seed=901)
            worker_payload["comfyui_instance_id"] = "worker-2"
            rejected = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json=worker_payload,
            )
            assert rejected.status_code == 503, rejected.text
            assert rejected.json()["error"]["code"] == "comfyui_instance_unavailable"
            assert "RTX 3080" in rejected.json()["error"]["message"]

            primary_payload = generation_payload(client, "healthy primary", seed=902)
            primary_payload["comfyui_instance_id"] = "primary"
            accepted = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json=primary_payload,
            )
            assert accepted.status_code == 201, accepted.text
            complete = wait_for_status(client, accepted.json()["id"], "succeeded", timeout=10)
            assert complete["comfyui_instance_id"] == "primary"
            assert fake_state.submitted
            assert worker.state.submitted == []


def test_cached_catalog_can_dispatch_to_a_healthy_secondary_instance(
    settings_factory,
    fake_services: LiveFakeServer,
    fake_state,
) -> None:
    with LiveFakeServer() as worker:
        worker.state.reset_runtime()
        settings = _instance_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="runtime.cached.catalog")
            _wait_for_instances(
                client,
                lambda value: all(item["available"] for item in value["items"]),
            )

            fake_state.service_available = False
            statuses = _wait_for_instances(
                client,
                lambda value: (
                    next(item for item in value["items"] if item["id"] == "primary")["available"]
                    is False
                ),
            )
            assert (
                next(item for item in statuses["items"] if item["id"] == "worker-2")["available"]
                is True
            )

            sources = client.get("/api/workflows")
            assert sources.status_code == 200, sources.text
            current_source = next(source for source in sources.json() if source["source_key"])
            assert current_source["cached"] is True
            assert current_source["readiness"] == "cached_offline"
            assert current_source["available"] is True

            worker_payload = generation_payload(client, "cached catalog worker", seed=903)
            worker_payload["comfyui_instance_id"] = "worker-2"
            accepted = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json=worker_payload,
            )
            assert accepted.status_code == 201, accepted.text
            complete = wait_for_status(client, accepted.json()["id"], "succeeded", timeout=10)
            assert complete["comfyui_instance_id"] == "worker-2"
            assert worker.state.submitted
