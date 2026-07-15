from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

import pytest
from app.api.events import events
from app.main import create_app
from app.services import queue_worker as queue_worker_module
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from tests.conftest import csrf
from tests.fake_services import make_png
from tests.helpers import create_generation, provision_user, wait_for_status


def _health_completes_while_storage_is_blocked(client: TestClient) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    request = executor.submit(client.get, "/api/health")
    try:
        response = request.result(timeout=2)
    except FutureTimeoutError as exc:
        executor.shutdown(wait=False, cancel_futures=True)
        raise AssertionError("health request stalled behind blocking image storage") from exc
    executor.shutdown(wait=True)
    assert response.status_code == 200, response.text


def test_blocking_artifact_storage_does_not_stall_unrelated_http_requests(
    settings_factory, fake_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_state  # The fixture resets deterministic services before this app starts.
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="artifact.responsiveness")
        assets = client.app.state.container.assets
        original_store = assets.store_artifact
        storage_started = threading.Event()
        release_storage = threading.Event()
        first_store = True

        def blocking_store(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            nonlocal first_store
            if first_store:
                first_store = False
                storage_started.set()
                if not release_storage.wait(timeout=5):
                    raise TimeoutError("test did not release artifact storage")
            return original_store(*args, **kwargs)

        monkeypatch.setattr(assets, "store_artifact", blocking_store)
        generation = create_generation(client, "artifact offload responsiveness", seed=611)
        assert storage_started.wait(timeout=5)

        try:
            _health_completes_while_storage_is_blocked(client)
        finally:
            release_storage.set()

        assert wait_for_status(client, generation["id"], "succeeded", timeout=10)["status"] == (
            "succeeded"
        )


def test_blocking_artifact_metadata_insert_does_not_stall_unrelated_http_requests(
    settings_factory, fake_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_state
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="artifact.insert.responsiveness")
        worker = client.app.state.container.worker
        original_insert = worker._insert_artifact
        insert_started = threading.Event()
        release_insert = threading.Event()
        first_insert = True

        def blocking_insert(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            nonlocal first_insert
            if first_insert:
                first_insert = False
                insert_started.set()
                if not release_insert.wait(timeout=5):
                    raise TimeoutError("test did not release artifact metadata insertion")
            return original_insert(*args, **kwargs)

        monkeypatch.setattr(worker, "_insert_artifact", blocking_insert)
        generation = create_generation(client, "artifact insertion responsiveness", seed=612)
        assert insert_started.wait(timeout=5)

        try:
            _health_completes_while_storage_is_blocked(client)
        finally:
            release_insert.set()

        assert wait_for_status(client, generation["id"], "succeeded", timeout=10)["status"] == (
            "succeeded"
        )


def test_blocking_result_normalization_does_not_stall_unrelated_http_requests(
    settings_factory, fake_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_state
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="result.normalize.responsiveness")
        original_normalize = queue_worker_module.normalize_history
        normalization_started = threading.Event()
        release_normalization = threading.Event()
        first_normalization = True

        def blocking_normalize(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            nonlocal first_normalization
            if first_normalization:
                first_normalization = False
                normalization_started.set()
                if not release_normalization.wait(timeout=5):
                    raise TimeoutError("test did not release result normalization")
            return original_normalize(*args, **kwargs)

        monkeypatch.setattr(queue_worker_module, "normalize_history", blocking_normalize)
        generation = create_generation(client, "result normalization responsiveness", seed=613)
        assert normalization_started.wait(timeout=5)

        try:
            _health_completes_while_storage_is_blocked(client)
        finally:
            release_normalization.set()

        assert wait_for_status(client, generation["id"], "succeeded", timeout=10)["status"] == (
            "succeeded"
        )


def test_blocking_terminal_result_commit_does_not_stall_unrelated_http_requests(
    settings_factory, fake_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_state
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="result.commit.responsiveness")
        worker = client.app.state.container.worker
        original_commit = worker._commit_finalization
        commit_started = threading.Event()
        release_commit = threading.Event()

        def blocking_commit(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            commit_started.set()
            if not release_commit.wait(timeout=5):
                raise TimeoutError("test did not release terminal result commit")
            return original_commit(*args, **kwargs)

        monkeypatch.setattr(worker, "_commit_finalization", blocking_commit)
        generation = create_generation(client, "terminal result commit responsiveness", seed=614)
        assert commit_started.wait(timeout=5)

        try:
            _health_completes_while_storage_is_blocked(client)
        finally:
            release_commit.set()

        assert wait_for_status(client, generation["id"], "succeeded", timeout=10)["status"] == (
            "succeeded"
        )


def test_blocking_upload_normalization_does_not_stall_unrelated_http_requests(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provision_user(app_client, username="upload.responsiveness")
    assets = app_client.app.state.container.assets
    original_store = assets.store_upload
    storage_started = threading.Event()
    release_storage = threading.Event()

    def blocking_store(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        storage_started.set()
        if not release_storage.wait(timeout=5):
            raise TimeoutError("test did not release upload normalization")
        return original_store(*args, **kwargs)

    monkeypatch.setattr(assets, "store_upload", blocking_store)
    csrf_token = csrf(app_client)
    with ThreadPoolExecutor(max_workers=1) as executor:
        upload = executor.submit(
            app_client.post,
            "/api/uploads/images",
            headers={"X-CSRF-Token": csrf_token},
            files={"file": ("source.png", make_png("upload"), "image/png")},
        )
        assert storage_started.wait(timeout=5)
        try:
            _health_completes_while_storage_is_blocked(app_client)
        finally:
            release_storage.set()
        upload_response = upload.result(timeout=5)
    assert upload_response.status_code == 200, upload_response.text


def test_blocking_upload_metadata_insert_does_not_stall_unrelated_http_requests(
    app_client: TestClient,
) -> None:
    provision_user(app_client, username="upload.insert.responsiveness")
    insert_started = threading.Event()
    release_insert = threading.Event()
    engine = app_client.app.state.container.db.engine

    def block_upload_insert(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO UPLOADS"):
            insert_started.set()
            if not release_insert.wait(timeout=5):
                raise TimeoutError("test did not release upload metadata insertion")

    csrf_token = csrf(app_client)
    event.listen(engine, "before_cursor_execute", block_upload_insert)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            upload = executor.submit(
                app_client.post,
                "/api/uploads/images",
                headers={"X-CSRF-Token": csrf_token},
                files={"file": ("source.png", make_png("upload metadata"), "image/png")},
            )
            assert insert_started.wait(timeout=5)
            _health_completes_while_storage_is_blocked(app_client)
            release_insert.set()
            upload_response = upload.result(timeout=5)
    finally:
        release_insert.set()
        event.remove(engine, "before_cursor_execute", block_upload_insert)
    assert upload_response.status_code == 200, upload_response.text


async def test_many_live_streams_leave_the_database_pool_available(
    app_client: TestClient,
) -> None:
    user, raw_token = provision_user(app_client, username="sse.pool")
    container = app_client.app.state.container

    class ConnectedRequest:
        def __init__(self) -> None:
            self.app = app_client.app
            self.cookies = {container.settings.session_cookie_name: raw_token}

        async def is_disconnected(self) -> bool:
            return False

    request = ConnectedRequest()
    responses: list[StreamingResponse] = []
    pending: list[asyncio.Task[str | bytes]] = []
    try:
        for _ in range(20):
            response = await events(request, None, None)  # type: ignore[arg-type]
            responses.append(response)
            pending.append(asyncio.create_task(anext(response.body_iterator)))

        for _ in range(100):
            subscriber_count = len(container.broker._subscribers.get(user["id"], set()))
            if subscriber_count == len(pending):
                break
            await asyncio.sleep(0.01)
        assert subscriber_count == 20
        assert container.db.engine.pool.checkedout() == 0

        session_response = await asyncio.wait_for(
            asyncio.to_thread(app_client.get, "/api/auth/session"),
            timeout=2,
        )
        assert session_response.status_code == 200
        with container.db.session_factory() as session:
            assert session.scalar(text("SELECT 1")) == 1
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    assert not container.broker._subscribers
