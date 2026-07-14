from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from app import __main__ as entrypoint
from app.config import Settings
from app.services.queue_worker import QueueWorker
from pydantic import ValidationError


def test_uvicorn_receives_configured_graceful_shutdown_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        test_mode=True,
        data_dir=tmp_path,
        graceful_shutdown_timeout_seconds=12,
    )
    application = object()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(entrypoint, "get_settings", lambda: settings)
    monkeypatch.setattr(entrypoint, "create_app", lambda configured: application)

    def fake_run(app: object, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(entrypoint.uvicorn, "run", fake_run)

    entrypoint.main()

    assert captured["app"] is application
    assert captured["timeout_graceful_shutdown"] == 12
    assert captured["host"] == settings.listen_host
    assert captured["port"] == settings.listen_port


@pytest.mark.parametrize("invalid_timeout", [0, -1])
def test_graceful_shutdown_timeout_must_be_positive(invalid_timeout: int, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="graceful_shutdown_timeout_seconds"):
        Settings(
            test_mode=True,
            data_dir=tmp_path,
            graceful_shutdown_timeout_seconds=invalid_timeout,
        )


async def test_worker_stop_cancels_and_joins_all_owned_tasks() -> None:
    async def wait_forever() -> None:
        await asyncio.Event().wait()

    worker = object.__new__(QueueWorker)
    worker._stop = asyncio.Event()
    worker._main_task = asyncio.create_task(wait_forever(), name="test-worker-main")
    worker._health_task = asyncio.create_task(wait_forever(), name="test-worker-health")
    worker._active = {
        "generation-1": asyncio.create_task(wait_forever(), name="test-worker-generation")
    }
    owned_tasks = [worker._main_task, worker._health_task, *worker._active.values()]
    await asyncio.sleep(0)

    await worker.stop()

    assert worker._stop.is_set()
    assert all(task.done() and task.cancelled() for task in owned_tasks)
    assert worker._active == {}
    assert worker._main_task is None
    assert worker._health_task is None
