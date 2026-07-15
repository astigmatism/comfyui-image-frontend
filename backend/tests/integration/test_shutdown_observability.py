from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from app import main as main_module
from app.container import AppContainer
from app.main import RequestContextMiddleware, create_app
from app.models import Generation, GenerationStatus
from app.services import workflow_registry as workflow_registry_module
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send
from tests.helpers import create_generation, first_profile, provision_user, restore_cookie


class _GatedNetworkTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self._release = threading.Event()
        self.started = threading.Event()
        self.paths: list[str] = []
        self._network = httpx.AsyncHTTPTransport()

    @property
    def blocked(self) -> bool:
        return not self._release.is_set()

    @blocked.setter
    def blocked(self, value: bool) -> None:
        if value:
            self._release.clear()
        else:
            self._release.set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        self.started.set()
        await asyncio.to_thread(self._release.wait)
        return await self._network.handle_async_request(request)

    async def aclose(self) -> None:
        self._release.set()
        await self._network.aclose()


async def test_cancelled_startup_failure_record_waits_for_thread_completion() -> None:
    record_started = threading.Event()
    release_record = threading.Event()

    async def fail_refresh() -> None:
        raise RuntimeError("refresh failed")

    def blocking_failure_record() -> None:
        record_started.set()
        if not release_record.wait(timeout=5):
            raise TimeoutError("test did not release startup failure record")

    container = object.__new__(AppContainer)
    container.registry = SimpleNamespace(
        refresh=fail_refresh,
        record_background_refresh_failure=blocking_failure_record,
    )
    discovery = asyncio.create_task(container._run_startup_discovery())
    try:
        assert await asyncio.wait_for(asyncio.to_thread(record_started.wait, 1), timeout=2)
        discovery.cancel()
        await asyncio.sleep(0)
        assert not discovery.done()
    finally:
        release_record.set()

    with pytest.raises(asyncio.CancelledError):
        await discovery


def test_health_and_structured_shutdown_logs_survive_migration_logging(
    settings_factory, fake_state, capsys
) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(log_level="INFO"))) as client:
        private_query = "must-not-appear-in-logs"
        response = client.get(
            f"/api/health?prompt={private_query}",
            headers={"X-Request-ID": "timing-test-request"},
        )
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "timing-test-request"
        assert response.headers["Server-Timing"].startswith("app_ttfb;dur=")
        unsafe_request_id = "unsafe request/id?must-not-appear-in-logs"
        sanitized = client.get(
            "/api/health",
            headers={"X-Request-ID": unsafe_request_id},
        )
        sanitized_request_id = sanitized.headers["X-Request-ID"]
        assert str(uuid.UUID(sanitized_request_id)) == sanitized_request_id
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["database"] is True
        assert payload["worker"] == {
            "enabled": False,
            "ready": True,
            "dispatcher_running": False,
            "dispatcher_done": False,
            "heartbeat_fresh": False,
            "state": "not_started",
            "last_heartbeat_at": None,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "last_exception_class": None,
            "restart_count": 0,
        }

    records: list[dict[str, Any]] = []
    for line in capsys.readouterr().out.splitlines():
        if line.startswith("{"):
            records.append(json.loads(line))
    messages = [record["message"] for record in records]

    expected = [
        "application_shutdown_started",
        "worker_cancellation_complete",
        "external_clients_closed",
        "database_closed",
        "application_shutdown_complete",
    ]
    assert expected == [message for message in messages if message in expected]
    completion = next(
        record for record in records if record["message"] == "application_shutdown_complete"
    )
    assert completion["shutdown_duration_seconds"] >= 0
    request_record = next(
        record
        for record in records
        if record["message"] == "http_request_completed"
        and record.get("request_id") == "timing-test-request"
    )
    assert request_record == {
        **{key: request_record[key] for key in ("timestamp", "level", "logger")},
        "message": "http_request_completed",
        "request_id": "timing-test-request",
        "method": "GET",
        "route": "/api/health",
        "status_code": 200,
        "duration_ms": request_record["duration_ms"],
        "client_disconnected": False,
    }
    assert request_record["duration_ms"] >= 0
    assert private_query not in json.dumps(records)
    assert unsafe_request_id not in json.dumps(records)


async def test_send_side_broken_pipe_is_logged_as_a_disconnect_without_being_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: list[tuple[str, dict[str, Any]]] = []

    def record(message: str, *, extra: dict[str, Any]) -> None:
        records.append((message, extra))

    monkeypatch.setattr(main_module.logger, "info", record)

    async def application(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def broken_send(_message: Message) -> None:
        raise BrokenPipeError("client connection closed")

    middleware = RequestContextMiddleware(application)
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/health",
        "raw_path": b"/api/health",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8000),
        "state": {},
    }

    with pytest.raises(BrokenPipeError, match="client connection closed"):
        await middleware(scope, receive, broken_send)

    completed = next(extra for message, extra in records if message == "http_request_completed")
    assert completed["status_code"] == 204
    assert completed["client_disconnected"] is True


def test_startup_discovery_logs_refresh_failure_before_failure_record_error(
    settings_factory, fake_state, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    del fake_state
    app = create_app(settings_factory(log_level="INFO"))
    container = app.state.container

    async def fail_refresh() -> None:
        raise RuntimeError("refresh failed")

    def fail_failure_record() -> None:
        raise OSError("failure state could not be recorded")

    monkeypatch.setattr(container.registry, "refresh", fail_refresh)
    monkeypatch.setattr(
        container.registry,
        "record_background_refresh_failure",
        fail_failure_record,
    )

    with TestClient(app):
        task = container._startup_discovery_task
        assert task is not None
        for _ in range(100):
            if task.done():
                break
            time.sleep(0.01)
        assert task.done()

    records = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    messages = [record["message"] for record in records]
    refresh_index = messages.index("startup_workflow_discovery_failed")
    record_index = messages.index("startup_workflow_discovery_failure_record_failed")
    assert refresh_index < record_index
    assert records[refresh_index]["exception_class"] == "RuntimeError"
    assert records[record_index]["exception_class"] == "OSError"


def test_unexpected_startup_discovery_task_exception_is_observed_once(
    settings_factory, fake_state, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    del fake_state
    app = create_app(settings_factory(log_level="INFO"))
    container = app.state.container

    async def fail_unexpectedly() -> None:
        raise RuntimeError("unexpected task failure")

    monkeypatch.setattr(container, "_run_startup_discovery", fail_unexpectedly)

    with TestClient(app):
        task = container._startup_discovery_task
        assert task is not None
        for _ in range(100):
            if task.done():
                break
            time.sleep(0.01)
        assert task.done()

    records = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    failures = [
        record
        for record in records
        if record["message"] == "startup_workflow_discovery_task_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["exception_class"] == "RuntimeError"


def test_slow_startup_discovery_does_not_delay_local_http_and_recovers_once(
    settings_factory, fake_state, monkeypatch
) -> None:
    del fake_state
    transport = _GatedNetworkTransport()
    settings = settings_factory(
        enable_background_worker=True,
        external_health_interval_seconds=0.1,
        log_level="WARNING",
    )
    app = create_app(settings, comfy_transport=transport)
    original_refresh = app.state.container.registry.refresh
    refresh_calls = 0

    async def counted_refresh() -> Any:
        nonlocal refresh_calls
        refresh_calls += 1
        return await original_refresh()

    monkeypatch.setattr(app.state.container.registry, "refresh", counted_refresh)
    started_at = time.monotonic()
    with TestClient(app) as client:
        startup_seconds = time.monotonic() - started_at
        assert startup_seconds < 1.5
        assert transport.started.wait(timeout=1)

        request_started = time.monotonic()
        session_response = client.get("/api/auth/session")
        assert time.monotonic() - request_started < 0.5
        assert session_response.status_code == 200
        assert client.get("/api/health").status_code == 200

        provision_user(client, username="background.discovery")
        assert client.get("/api/workflows").json() == []

        transport.blocked = False
        profile = first_profile(client, timeout=5)
        assert profile["available"] is True
        assert profile["readiness"] in {"ready", "ready_with_warnings"}

        assert refresh_calls == 1
        time.sleep(0.25)
        assert refresh_calls == 1


def test_slow_restart_reconciliation_does_not_hide_retained_history(
    settings_factory, fake_state
) -> None:
    del fake_state
    settings = settings_factory(
        enable_background_worker=False,
        reconciliation_grace_seconds=0.05,
        log_level="WARNING",
    )
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="background.recovery")
        generation = create_generation(first, "retained during slow restart", seed=731)
        container = first.app.state.container
        with container.db.session_factory() as session:
            stored = session.get(Generation, generation["id"])
            assert stored is not None
            stored.status = GenerationStatus.RUNNING
            stored.comfyui_prompt_id = "hanging-recovery-prompt"
            session.commit()

    settings.enable_background_worker = True
    transport = _GatedNetworkTransport()
    started_at = time.monotonic()
    with TestClient(create_app(settings, comfy_transport=transport)) as second:
        assert time.monotonic() - started_at < 1.5
        assert transport.started.wait(timeout=1)
        restore_cookie(second, cookie, name=settings.session_cookie_name)

        request_started = time.monotonic()
        retained = second.get(f"/api/generations/{generation['id']}")
        assert time.monotonic() - request_started < 0.5
        assert retained.status_code == 200
        assert retained.json()["status"] == "running"

        health = second.get("/api/health")
        assert health.status_code == 200
        assert health.json()["worker"]["state"] == "recovering"


def test_blocking_publication_validation_runs_off_the_request_event_loop(
    settings_factory, fake_state, monkeypatch
) -> None:
    del fake_state
    validation_started = threading.Event()
    release_validation = threading.Event()
    original_validate = workflow_registry_module.validate_publication

    def blocking_validate(*args: Any, **kwargs: Any) -> Any:
        validation_started.set()
        if not release_validation.wait(timeout=2):
            raise AssertionError("test did not release publication validation")
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(workflow_registry_module, "validate_publication", blocking_validate)
    settings = settings_factory(enable_background_worker=False, log_level="WARNING")
    try:
        with TestClient(create_app(settings)) as client:
            assert validation_started.wait(timeout=2)
            started_at = time.monotonic()
            response = client.get("/api/health")
            assert time.monotonic() - started_at < 0.5
            assert response.status_code == 200
            release_validation.set()
    finally:
        release_validation.set()
