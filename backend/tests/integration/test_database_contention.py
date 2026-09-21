"""Local-only incident reproduction and the bounded concurrency acceptance workload."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.blocking import run_blocking
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import TimeoutError as PoolTimeout
from tests.helpers import create_generation, provision_user, wait_for_status


async def test_original_pool_wait_stalls_loop_but_worker_wait_does_not(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'repro.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.15,
        connect_args={"check_same_thread": False},
    )
    held = engine.connect()

    async def release_soon():
        await asyncio.sleep(0.02)
        held.close()

    def query():
        with engine.connect() as connection:
            return connection.scalar(text("SELECT 1"))

    release = asyncio.create_task(release_soon())
    started = time.monotonic()
    # This is the pre-fix pattern: a synchronous checkout inside an async task.
    with pytest.raises(PoolTimeout):
        query()
    assert time.monotonic() - started >= 0.14
    assert not release.done()
    await release
    held = engine.connect()
    release = asyncio.create_task(release_soon())
    assert await asyncio.wait_for(run_blocking(query), 0.1) == 1
    await release
    assert engine.pool.checkedout() == 0
    engine.dispose()


def test_sixty_authenticated_reads_with_thumbnails_and_active_workers(
    settings_factory,
    fake_state,
):
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="contention.reads")
        first = create_generation(client, "thumbnail fixture", seed=431)
        completed = wait_for_status(client, first["id"], "succeeded")
        thumbnail = next(
            item["thumbnail_url"] for item in completed["artifacts"] if item["thumbnail_url"]
        )
        working = create_generation(client, "worker during read burst", seed=432)
        container = client.app.state.container
        violations = []

        def observe_sql(*args):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                violations.append("database execution on event loop")
            # Local fault injection simulates storage latency during a mixed burst.
            time.sleep(0.015)

        event.listen(container.db.engine, "before_cursor_execute", observe_sql)
        try:
            with ThreadPoolExecutor(max_workers=60) as executor:
                requests = [
                    executor.submit(client.get, thumbnail if index % 2 else "/api/generations")
                    for index in range(60)
                ]
                health_started = time.monotonic()
                health = client.get("/api/health")
                assert time.monotonic() - health_started < 2
                assert health.status_code == 200, health.text
                responses = [future.result(timeout=10) for future in requests]
            assert {response.status_code for response in responses} == {200}
            wait_for_status(client, working["id"], "succeeded")
            assert not violations
            assert container.db.metrics()["database_peak_checked_out"] <= 15
            admission = client.app.state.admission
            assert admission.active == admission.media == len(admission.waiters) == 0
        finally:
            event.remove(container.db.engine, "before_cursor_execute", observe_sql)


def test_health_has_an_independent_connection_and_bounded_failed_probe(app_client, monkeypatch):
    import threading

    database = app_client.app.state.container.db
    held = [database.engine.connect() for _ in range(15)]
    try:
        started = time.monotonic()
        assert app_client.get("/api/health").status_code == 200
        assert time.monotonic() - started < 2
    finally:
        for connection in held:
            connection.close()
    release = threading.Event()
    calls = []

    def blocked_probe():
        calls.append(True)
        release.wait(5)
        return False

    monkeypatch.setattr(database, "_readonly_healthcheck", blocked_probe)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            started = time.monotonic()
            responses = list(executor.map(lambda _: app_client.get("/api/health"), range(8)))
        assert time.monotonic() - started < 2
        assert {response.status_code for response in responses} == {503}
        assert len(calls) == 1
    finally:
        release.set()
