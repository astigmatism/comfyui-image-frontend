"""Multiple ComfyUI image workers behind one primary catalog.

Every scenario here runs against deterministic fake ComfyUI servers, because the
first production appliance has a single GPU instance and cannot demonstrate
concurrent workers, failover, or per-source eligibility.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from app.main import create_app
from app.models import ComfyUIInstanceHealth, Generation, GenerationStatus
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.fake_services import LiveFakeServer
from tests.helpers import (
    create_generation,
    generation_payload,
    provision_user,
    restore_cookie,
    wait_for_generation,
    wait_for_status,
)


def _pool_settings(settings_factory, primary: LiveFakeServer, *workers: LiveFakeServer, **extra):
    """Primary plus opt-in workers addressed only by URL, as an operator would."""

    extra.setdefault("enable_background_worker", True)
    return settings_factory(
        comfyui_instances=[
            {
                "id": "primary",
                "label": "Primary",
                "base_url": primary.base_url,
                "ws_url": primary.ws_url,
                "user": "fixture-user",
                "concurrency": 1,
            }
        ],
        comfyui_default_instance_id="primary",
        comfyui_image_workers=",".join(worker.base_url for worker in workers),
        **extra,
    )


def _wait_for_pool(client: TestClient, predicate, *, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get("/api/comfyui-instances")
        assert response.status_code == 200, response.text
        latest = response.json()
        if predicate(latest):
            return latest
        time.sleep(0.03)
    raise AssertionError(f"image worker pool did not settle; latest={latest}")


def _all_available(payload: dict[str, Any]) -> bool:
    return bool(payload["items"]) and all(item["available"] for item in payload["items"])


def _instances(client: TestClient) -> dict[str, str | None]:
    """Recorded worker per generation id for the signed-in owner."""

    page = client.get("/api/generations?limit=60").json()
    return {item["id"]: item["comfyui_instance_id"] for item in page["items"]}


def test_two_workers_run_concurrently_while_further_work_queues(
    settings_factory, fake_services, fake_state
) -> None:
    fake_state.wait_for_cancel_prompts.update({"hold one", "hold two", "hold three"})
    with LiveFakeServer() as worker:
        worker.state.workflow_files = dict(fake_state.workflow_files)
        worker.state.wait_for_cancel_prompts.update(fake_state.wait_for_cancel_prompts)
        settings = _pool_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="pool.concurrent")
            pool = _wait_for_pool(client, _all_available)
            assert pool["image_pool"]["worker_count"] == 2
            assert pool["image_pool"]["idle_count"] == 2
            assert [item["role"] for item in pool["items"]] == ["image", "image"]
            assert pool["image_pool_instance_ids"] == [
                "primary",
                pool["items"][1]["id"],
            ]

            held = [create_generation(client, prompt) for prompt in ("hold one", "hold two")]
            queued = create_generation(client, "hold three")
            for generation in held:
                wait_for_status(client, generation["id"], "running", timeout=10)

            # One image per worker executes at the same time; the third waits
            # without being bound to any worker.
            running = _instances(client)
            assert {running[item["id"]] for item in held} == {
                "primary",
                pool["items"][1]["id"],
            }
            assert running[queued["id"]] is None
            assert client.get(f"/api/generations/{queued['id']}").json()["status"] == "queued"
            busy = _wait_for_pool(client, lambda value: value["image_pool"]["idle_count"] == 0)
            assert busy["image_pool"]["busy_count"] == 2
            assert busy["image_pool"]["free_slot_count"] == 0
            assert busy["image_pool"]["unassigned_queued_count"] == 1
            activity = client.get("/api/generation-activity").json()
            assert activity["worker_pool"]["idle_count"] == 0
            assert activity["worker_pool"]["worker_count"] == 2

            # Freeing one worker releases exactly one queued image to that worker.
            freed = running[held[0]["id"]]
            client.post(
                f"/api/generations/{held[0]['id']}/cancel", headers={"X-CSRF-Token": csrf(client)}
            )
            started = wait_for_generation(
                client,
                queued["id"],
                lambda detail: detail["comfyui_instance_id"] is not None,
                timeout=10,
            )
            assert started["comfyui_instance_id"] == freed


def test_an_offline_worker_never_holds_queued_work(
    settings_factory, fake_services, fake_state
) -> None:
    del fake_state
    with LiveFakeServer() as worker:
        worker.state.workflow_files = dict(fake_services.state.workflow_files)
        settings = _pool_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="pool.offline")
            pool = _wait_for_pool(client, _all_available)
            worker_id = pool["items"][1]["id"]

            worker.state.service_available = False
            _wait_for_pool(
                client,
                lambda value: (
                    not next(item for item in value["items"] if item["id"] == worker_id)[
                        "available"
                    ]
                ),
            )
            first = create_generation(client, "healthy worker only", seed=501)
            second = create_generation(client, "healthy worker only too", seed=502)
            for generation in (first, second):
                wait_for_status(client, generation["id"], "succeeded", timeout=15)
            assert _instances(client) == {first["id"]: "primary", second["id"]: "primary"}
            assert not worker.state.submitted

            # Recovery makes the worker eligible again without operator action.
            worker.state.service_available = True
            _wait_for_pool(client, _all_available)
            fake_services.state.service_available = False
            _wait_for_pool(
                client,
                lambda value: (
                    not next(item for item in value["items"] if item["id"] == "primary")[
                        "available"
                    ]
                ),
            )
            third = create_generation(client, "recovered worker", seed=503)
            recovered = wait_for_status(client, third["id"], "succeeded", timeout=15)
            assert recovered["comfyui_instance_id"] == worker_id
            assert worker.state.submitted


def test_a_worker_without_the_publication_never_receives_that_source(
    settings_factory, fake_services, fake_state
) -> None:
    del fake_state
    with LiveFakeServer() as worker:
        # The worker is healthy but publishes nothing, so it carries no revision.
        worker.state.workflow_files = {}
        settings = _pool_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="pool.eligibility")
            _wait_for_pool(client, _all_available)
            generations = [
                create_generation(client, f"eligible primary only {index}", seed=600 + index)
                for index in range(2)
            ]
            for generation in generations:
                wait_for_status(client, generation["id"], "succeeded", timeout=15)
            assert set(_instances(client).values()) == {"primary"}
            assert not worker.state.submitted
            # Source listings still come from the primary catalog only.
            sources = client.get("/api/workflows").json()
            assert {item["instance_id"] for item in sources} == {"primary"}


def test_an_outage_before_submission_releases_the_job_to_another_worker(
    settings_factory, fake_services, fake_state
) -> None:
    with LiveFakeServer() as worker:
        worker.state.workflow_files = dict(fake_state.workflow_files)
        settings = _pool_settings(
            settings_factory, fake_services, worker, enable_background_worker=False
        )
        with TestClient(create_app(settings)) as first:
            _, cookie = provision_user(first, username="pool.failover")
            worker_id = first.get("/api/comfyui-instances").json()["image_pool_instance_ids"][1]
            generation = create_generation(first, "released after an outage", seed=700)
            container = first.app.state.container
            # Reproduce the state a transport failure interrupts: claimed and
            # dispatching on the primary, with nothing submitted yet.
            with container.db.session_factory() as session:
                row = session.get(Generation, generation["id"])
                assert row is not None
                row.status = GenerationStatus.DISPATCHING
                row.comfyui_instance_id = "primary"
                row.comfyui_instance_label = "Primary"
                session.commit()
            asyncio.run(container.worker._requeue_after_outage(generation["id"]))
            with container.db.session_factory() as session:
                released = session.get(Generation, generation["id"])
                health = session.get(ComfyUIInstanceHealth, "primary")
                assert released is not None and health is not None
                # The job returns to the pool, and the failing worker is marked
                # unhealthy so the dispatcher skips it until it recovers.
                assert released.status == GenerationStatus.QUEUED
                assert released.comfyui_instance_id is None
                assert released.comfyui_instance_label is None
                assert health.available is False

        # Only the remaining worker can execute it while the primary is down.
        fake_state.service_available = False
        settings.enable_background_worker = True
        with TestClient(create_app(settings)) as second:
            restore_cookie(second, cookie, name=settings.session_cookie_name)
            finished = wait_for_status(second, generation["id"], "succeeded", timeout=20)
            assert finished["comfyui_instance_id"] == worker_id
            assert len(worker.state.submitted) == 1
            assert not fake_state.submitted


def test_a_removed_worker_returns_queued_work_to_the_pool_at_startup(
    settings_factory, fake_services, fake_state
) -> None:
    del fake_state
    settings = _pool_settings(settings_factory, fake_services, enable_background_worker=False)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="pool.removed")
        generation = create_generation(first, "pinned to a removed worker", seed=800)
        container = first.app.state.container
        with container.db.session_factory() as session:
            row = session.get(Generation, generation["id"])
            assert row is not None
            row.comfyui_instance_id = "retired-worker"
            row.comfyui_instance_label = "Retired Worker"
            session.commit()

    settings.enable_background_worker = True
    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie, name=settings.session_cookie_name)
        finished = wait_for_status(second, generation["id"], "succeeded", timeout=15)
        assert finished["comfyui_instance_id"] == "primary"


def test_startup_releases_a_queued_pin_even_when_its_worker_is_unhealthy(
    settings_factory, fake_services, fake_state
) -> None:
    """A queued image is never bound to one runtime across a restart."""

    with LiveFakeServer() as worker:
        worker.state.workflow_files = dict(fake_state.workflow_files)
        settings = _pool_settings(
            settings_factory, fake_services, worker, enable_background_worker=False
        )
        with TestClient(create_app(settings)) as first:
            _, cookie = provision_user(first, username="pool.restart")
            worker_id = first.get("/api/comfyui-instances").json()["image_pool_instance_ids"][1]
            generation = create_generation(first, "pinned to an unhealthy worker", seed=801)
            container = first.app.state.container
            with container.db.session_factory() as session:
                row = session.get(Generation, generation["id"])
                assert row is not None
                row.comfyui_instance_id = worker_id
                row.comfyui_instance_label = "ComfyUI worker"
                session.commit()

        worker.state.service_available = False
        settings.enable_background_worker = True
        with TestClient(create_app(settings)) as second:
            restore_cookie(second, cookie, name=settings.session_cookie_name)
            finished = wait_for_status(second, generation["id"], "succeeded", timeout=20)
            assert finished["comfyui_instance_id"] == "primary"
            assert not worker.state.submitted


def test_a_single_worker_deployment_keeps_the_previous_contract(
    settings_factory, fake_services, fake_state
) -> None:
    del fake_state
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="pool.single")
        pool = _wait_for_pool(client, _all_available)
        assert pool["configuration_mode"] == "legacy"
        assert pool["image_pool"]["worker_count"] == 1
        assert pool["image_pool_instance_ids"] == ["test-instance"]
        assert pool["items"][0]["role"] == "image"
        generation = create_generation(client, "single worker", seed=900)
        finished = wait_for_status(client, generation["id"], "succeeded", timeout=15)
        assert finished["comfyui_instance_id"] == "test-instance"
        assert finished["comfyui_instance_label"] == "Primary"
        recall = client.get(f"/api/generations/{generation['id']}/recall").json()
        assert recall["comfyui_instance_id"] == "test-instance"
        assert recall["comfyui_instance_configured"] is True
        assert recall["comfyui_pool_available"] is True
        assert recall["comfyui_instance_warning"] is None


def test_recall_of_a_removed_worker_reports_the_pool_without_warning(
    settings_factory, fake_services, fake_state
) -> None:
    del fake_state
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="pool.recall")
        generation = create_generation(client, "historical worker", seed=901)
        wait_for_status(client, generation["id"], "succeeded", timeout=15)
        container = client.app.state.container
        with container.db.session_factory() as session:
            row = session.get(Generation, generation["id"])
            assert row is not None
            row.comfyui_instance_id = "retired-worker"
            row.comfyui_instance_label = "Retired Worker"
            session.commit()
        recall = client.get(f"/api/generations/{generation['id']}/recall").json()
        assert recall["comfyui_instance_id"] == "retired-worker"
        assert recall["comfyui_instance_configured"] is False
        # The pool, not the historical worker, decides whether new work can run.
        assert recall["comfyui_pool_available"] is True
        assert recall["comfyui_instance_warning"] is None


def test_pool_status_never_discloses_worker_connection_details(
    settings_factory, fake_services, fake_state
) -> None:
    del fake_state
    with LiveFakeServer() as worker:
        worker.state.workflow_files = dict(fake_services.state.workflow_files)
        settings = _pool_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="pool.privacy")
            payload = client.get("/api/comfyui-instances").text
            activity = client.get("/api/generation-activity").text
            for secret in (worker.base_url, worker.ws_url, fake_services.base_url, "fixture-user"):
                assert secret not in payload
                assert secret not in activity
            assert "concurrency" not in payload


@pytest.mark.parametrize("supplied", ["primary", "w-unknown"])
def test_clients_still_cannot_select_a_worker(
    settings_factory, fake_services, fake_state, supplied: str
) -> None:
    del fake_state
    with LiveFakeServer() as worker:
        worker.state.workflow_files = dict(fake_services.state.workflow_files)
        settings = _pool_settings(
            settings_factory, fake_services, worker, enable_background_worker=False
        )
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="pool.selection")
            payload = generation_payload(client, "no selection", seed=902)
            response = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json={**payload, "comfyui_instance_id": supplied},
            )
            if supplied == "primary":
                assert response.status_code == 201, response.text
                assert response.json()["comfyui_instance_id"] is None
            else:
                assert response.status_code == 409
                assert response.json()["error"]["code"] == "runtime_assignment_conflict"


def test_generation_is_refused_only_when_every_worker_is_unavailable(
    settings_factory, fake_services, fake_state
) -> None:
    with LiveFakeServer() as worker:
        worker.state.workflow_files = dict(fake_state.workflow_files)
        settings = _pool_settings(settings_factory, fake_services, worker)
        with TestClient(create_app(settings)) as client:
            provision_user(client, username="pool.gate")
            _wait_for_pool(client, _all_available)
            container = client.app.state.container
            with container.db.session_factory() as session:
                assert container.image_pool.available_member_ids(session)
                # Acceptance of a captured automation request needs one worker.
                container.generations._require_available_worker(session)
            fake_state.service_available = False
            worker.state.service_available = False
            _wait_for_pool(
                client, lambda value: not any(item["available"] for item in value["items"])
            )
            with container.db.session_factory() as session:
                assert container.image_pool.available_member_ids(session) == ()
                with pytest.raises(Exception) as error:
                    container.generations._require_available_worker(session)
                assert getattr(error.value, "code", None) == "comfyui_instance_unavailable"
            # A manual request still queues while the pool recovers.
            queued = create_generation(client, "queued during outage", seed=903)
            assert queued["status"] == "queued"
            assert queued["comfyui_instance_id"] is None
            with container.db.session_factory() as session:
                row = session.get(Generation, queued["id"])
                assert row is not None
                assert row.status == GenerationStatus.QUEUED
