from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from app.main import create_app
from app.services import queue_worker as queue_worker_module
from fastapi.testclient import TestClient
from tests.helpers import create_generation, provision_user, wait_for_status


def _wait_for_worker_health(
    client: TestClient,
    predicate: Callable[[dict[str, Any], int], bool],
    *,
    timeout: float = 3.0,
) -> tuple[dict[str, Any], int]:
    deadline = time.monotonic() + timeout
    last_payload: dict[str, Any] | None = None
    last_status = 0
    while time.monotonic() < deadline:
        response = client.get("/api/health")
        last_status = response.status_code
        last_payload = response.json()
        if predicate(last_payload["worker"], last_status):
            return last_payload["worker"], last_status
        time.sleep(0.01)
    raise AssertionError(
        f"worker health did not reach expected state; status={last_status}, payload={last_payload}"
    )


def _start_worker(client: TestClient) -> Any:
    worker = client.app.state.container.worker
    worker.settings.enable_background_worker = True
    assert client.portal is not None
    client.portal.call(worker.start)
    return worker


def test_transient_claim_failure_is_logged_and_later_generations_succeed(
    settings_factory, fake_state, monkeypatch, capsys
) -> None:
    settings = settings_factory(enable_background_worker=False, log_level="INFO")
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="dispatcher.transient")
        first = create_generation(client, "dispatcher-transient-1", seed=301)
        second = create_generation(client, "dispatcher-transient-2", seed=302)
        worker = client.app.state.container.worker
        original_claim = worker._claim_next
        original_backoff = worker._dispatcher_backoff
        attempts = 0

        def claim_with_one_failure():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("sanitized injected claim failure")
            return original_claim()

        monkeypatch.setattr(worker, "_claim_next", claim_with_one_failure)
        monkeypatch.setattr(
            worker,
            "_dispatcher_backoff",
            lambda failures: max(0.5, original_backoff(failures)),
        )
        _start_worker(client)

        failing_health, status_code = _wait_for_worker_health(
            client,
            lambda snapshot, status: (
                status == 503
                and snapshot["state"] == "backing_off"
                and snapshot["consecutive_failures"] == 1
            ),
        )
        assert status_code == 503
        assert failing_health["dispatcher_running"] is True
        assert failing_health["last_exception_class"] == "RuntimeError"
        assert failing_health["last_failure_at"] is not None
        assert client.get(f"/api/generations/{first['id']}").json()["status"] == "queued"

        assert wait_for_status(client, first["id"], "succeeded", timeout=10)["status"] == (
            "succeeded"
        )
        assert wait_for_status(client, second["id"], "succeeded", timeout=10)["status"] == (
            "succeeded"
        )
        assert len(fake_state.submitted) == 2

    records = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    failure = next(
        record
        for record in records
        if record["message"] == "generation_dispatcher_iteration_failed"
    )
    assert failure["consecutive_failures"] == 1
    assert failure["exception_class"] == "RuntimeError"
    assert failure["traceback"]
    assert any(record["message"] == "generation_dispatcher_recovered" for record in records)


def test_repeated_claim_failures_back_off_without_a_busy_loop_and_recover(
    settings_factory, fake_state, monkeypatch
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="dispatcher.repeated")
        generation = create_generation(client, "dispatcher-repeated", seed=303)
        worker = client.app.state.container.worker
        original_claim = worker._claim_next
        original_backoff = worker._dispatcher_backoff
        failure_times: list[float] = []
        backoffs: list[float] = []

        def fail_three_times():
            if len(failure_times) < 3:
                failure_times.append(time.monotonic())
                raise RuntimeError("sanitized repeated claim failure")
            return original_claim()

        def record_backoff(consecutive_failures: int) -> float:
            delay = original_backoff(consecutive_failures)
            backoffs.append(delay)
            return delay

        monkeypatch.setattr(worker, "_claim_next", fail_three_times)
        monkeypatch.setattr(worker, "_dispatcher_backoff", record_backoff)
        _start_worker(client)

        failure_health, status_code = _wait_for_worker_health(
            client,
            lambda snapshot, status: status == 503 and snapshot["consecutive_failures"] >= 2,
        )
        assert status_code == 503
        assert failure_health["state"] == "backing_off"
        assert (
            wait_for_status(client, generation["id"], "succeeded", timeout=10)["status"]
            == "succeeded"
        )

        assert backoffs[:3] == [0.1, 0.2, 0.4]
        assert all(delay <= 5.0 for delay in backoffs)
        assert failure_times[1] - failure_times[0] >= backoffs[0] * 0.8
        assert failure_times[2] - failure_times[1] >= backoffs[1] * 0.8
        assert original_backoff(100) == 5.0
        recovered, recovered_status = _wait_for_worker_health(
            client,
            lambda snapshot, status: (
                status == 200
                and snapshot["state"] == "running"
                and snapshot["consecutive_failures"] == 0
            ),
        )
        assert recovered_status == 200
        assert recovered["last_exception_class"] == "RuntimeError"
        assert recovered["last_failure_at"] is not None
        assert len(fake_state.submitted) == 1


@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
def test_broker_notification_failure_or_cancellation_cannot_strand_or_duplicate_a_claim(
    settings_factory, fake_state, monkeypatch, failure_type
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="dispatcher.broker")
        generation = create_generation(client, "dispatcher-broker", seed=304)
        original_publish = queue_worker_module.publish_event
        dispatch_failures = 0

        async def fail_first_dispatch_notification(broker, event) -> None:
            nonlocal dispatch_failures
            if event.event_type == "generation.dispatching" and dispatch_failures == 0:
                dispatch_failures += 1
                raise failure_type("sanitized broker failure")
            await original_publish(broker, event)

        monkeypatch.setattr(
            queue_worker_module,
            "publish_event",
            fail_first_dispatch_notification,
        )
        _start_worker(client)

        completed = wait_for_status(client, generation["id"], "succeeded", timeout=10)
        assert completed["status"] == "succeeded"
        assert completed["prompt_id"] is not None
        assert dispatch_failures == 1
        assert len(fake_state.submitted) == 1


def test_generation_is_requeued_if_execution_task_cannot_be_scheduled(
    settings_factory, fake_state, monkeypatch
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="dispatcher.schedule")
        generation = create_generation(client, "dispatcher-schedule", seed=307)
        worker = client.app.state.container.worker

        def reject_task(generation_id, coroutine, *, name) -> None:
            del generation_id, name
            coroutine.close()
            raise RuntimeError("sanitized task scheduling failure")

        monkeypatch.setattr(worker, "_start_generation_task", reject_task)
        assert client.portal is not None
        with pytest.raises(RuntimeError, match="task scheduling failure"):
            client.portal.call(worker._dispatch_iteration)

        assert client.get(f"/api/generations/{generation['id']}").json()["status"] == "queued"
        assert len(fake_state.submitted) == 0


def test_ambiguous_prompt_submission_is_not_retried_and_next_work_continues(
    settings_factory, fake_state, monkeypatch
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="dispatcher.ambiguous")
        uncertain = create_generation(client, "dispatcher-ambiguous", seed=308)
        successor = create_generation(client, "dispatcher-after-ambiguous", seed=309)
        worker = client.app.state.container.worker
        original_submit = worker.comfyui.submit_prompt
        submission_attempts = 0

        async def lose_first_response(*args, **kwargs):
            nonlocal submission_attempts
            submission_attempts += 1
            if submission_attempts == 1:
                raise httpx.ReadError("sanitized ambiguous submission response")
            return await original_submit(*args, **kwargs)

        monkeypatch.setattr(worker.comfyui, "submit_prompt", lose_first_response)
        _start_worker(client)

        failed = wait_for_status(
            client,
            uncertain["id"],
            "failed_without_artifacts",
            timeout=10,
        )
        assert failed["error_code"] == "comfyui_submission_uncertain"
        assert (
            wait_for_status(client, successor["id"], "succeeded", timeout=10)["status"]
            == "succeeded"
        )
        assert submission_attempts == 2
        assert len(fake_state.submitted) == 1


def test_unexpected_dispatcher_completion_is_observed_restarted_and_recovers_work(
    settings_factory, fake_state, monkeypatch
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="dispatcher.restart")
        worker = _start_worker(client)
        original_run = worker._run_dispatcher
        replacement_calls = 0

        async def return_once_then_run() -> None:
            nonlocal replacement_calls
            replacement_calls += 1
            if replacement_calls == 1:
                return
            await original_run()

        monkeypatch.setattr(worker, "_run_dispatcher", return_once_then_run)

        async def cancel_current_dispatcher() -> None:
            assert worker._dispatcher_task is not None
            worker._dispatcher_task.cancel()
            await asyncio.sleep(0)

        assert client.portal is not None
        client.portal.call(cancel_current_dispatcher)
        failed, status_code = _wait_for_worker_health(
            client,
            lambda snapshot, status: (
                status == 503
                and snapshot["state"] == "backing_off"
                and snapshot["dispatcher_done"] is True
            ),
        )
        assert status_code == 503
        assert failed["dispatcher_running"] is False

        restarted, restarted_status = _wait_for_worker_health(
            client,
            lambda snapshot, status: (
                status == 200
                and snapshot["restart_count"] >= 2
                and snapshot["dispatcher_running"] is True
            ),
        )
        assert restarted_status == 200
        assert restarted["heartbeat_fresh"] is True
        assert restarted["last_exception_class"] == "RuntimeError"

        generation = create_generation(client, "dispatcher-after-restart", seed=305)
        assert (
            wait_for_status(client, generation["id"], "succeeded", timeout=10)["status"]
            == "succeeded"
        )
        assert len(fake_state.submitted) == 1


def test_stale_dispatcher_heartbeat_degrades_health_while_external_service_is_available(
    settings_factory, fake_state, monkeypatch
) -> None:
    settings = settings_factory(enable_background_worker=False)
    settings.dispatcher_heartbeat_stale_seconds = 0.05
    with TestClient(create_app(settings)) as client:
        del fake_state
        provision_user(client, username="dispatcher.stale")
        worker = _start_worker(client)
        assert client.portal is not None

        async def make_event() -> asyncio.Event:
            return asyncio.Event()

        blocker = client.portal.call(make_event)
        entered = threading.Event()

        async def blocked_iteration() -> None:
            entered.set()
            await blocker.wait()

        monkeypatch.setattr(worker, "_dispatch_iteration", blocked_iteration)
        assert entered.wait(timeout=1.0)
        time.sleep(settings.dispatcher_heartbeat_stale_seconds + 0.03)

        stale, status_code = _wait_for_worker_health(
            client,
            lambda snapshot, status: (
                status == 503
                and snapshot["dispatcher_running"] is True
                and snapshot["heartbeat_fresh"] is False
            ),
        )
        assert status_code == 503
        assert stale["state"] == "running"
        assert stale["last_exception_class"] is None

        client.portal.call(blocker.set)
        fresh, fresh_status = _wait_for_worker_health(
            client,
            lambda snapshot, status: status == 200 and snapshot["heartbeat_fresh"] is True,
        )
        assert fresh_status == 200
        assert fresh["dispatcher_running"] is True


def test_expected_shutdown_does_not_count_as_failure_and_preserves_queued_work(
    settings_factory, fake_state, monkeypatch, capsys
) -> None:
    del fake_state
    settings = settings_factory(enable_background_worker=False, log_level="INFO")
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="dispatcher.shutdown")
        generation = create_generation(client, "dispatcher-shutdown", seed=306)
        worker = client.app.state.container.worker
        monkeypatch.setattr(worker, "_comfyui_available", lambda: False)
        _start_worker(client)
        assert client.portal is not None
        client.portal.call(worker.stop)

        assert client.get(f"/api/generations/{generation['id']}").json()["status"] == "queued"
        snapshot = worker.health_snapshot()
        assert snapshot["state"] == "stopped"
        assert snapshot["consecutive_failures"] == 0
        assert snapshot["last_failure_at"] is None
        assert snapshot["last_exception_class"] is None

    messages = [
        json.loads(line)["message"]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]
    assert "generation_dispatcher_iteration_failed" not in messages
    assert "generation_dispatcher_unexpected_completion" not in messages
    assert "generation_dispatcher_supervisor_failed" not in messages
