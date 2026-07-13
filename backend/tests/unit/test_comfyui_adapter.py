from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.errors import AppError
from app.services.comfyui import ComfyUIAdapter, _queue_prompt_ids


def settings(tmp_path: Path, *, concurrency: int = 1) -> Settings:
    return Settings(
        data_dir=tmp_path,
        session_secret="test-secret",
        test_mode=True,
        comfyui_base_url="http://comfy.test",
        comfyui_concurrency=concurrency,
    )


def test_queue_prompt_id_parser_accepts_comfyui_list_and_object_shapes() -> None:
    running, pending = _queue_prompt_ids(
        {
            "queue_running": [[1, "run-1", {}], {"prompt_id": "run-2"}],
            "queue_pending": [[2, "wait-1", {}], {"id": "wait-2"}],
        }
    )
    assert running == {"run-1", "run-2"}
    assert pending == {"wait-1", "wait-2"}


def test_cancel_deletes_pending_prompt_even_when_application_status_is_running(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "GET" and request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": [[0, "p1", {}]]})
        if request.method == "POST" and request.url.path == "/queue":
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            await adapter.cancel("p1", running=True)
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert ("POST", "/queue", {"delete": ["p1"]}) in calls
    assert not any(path == "/interrupt" for _, path, _ in calls)


def test_cancel_refuses_global_interrupt_when_multiple_prompts_are_running(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/queue":
            return httpx.Response(
                200,
                json={"queue_running": [[0, "p1", {}], [1, "p2", {}]], "queue_pending": []},
            )
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(
            settings(tmp_path, concurrency=2), transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(AppError) as exc:
                await adapter.cancel("p1", running=True)
            assert exc.value.code == "cancellation_targeting_unavailable"
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_probe_and_list_support_modern_v2_userdata_listing(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/object_info":
            return httpx.Response(200, json={})
        if request.url.path == "/api/v2/userdata":
            assert dict(request.url.params) == {"path": "workflows/front-end"}
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "profile.workflow.json",
                        "path": "workflows/front-end/profile.workflow.json",
                        "type": "file",
                    },
                    {
                        "name": "profile.api.json",
                        "path": "workflows/front-end/profile.api.json",
                        "type": "file",
                    },
                    {
                        "name": "nested",
                        "path": "workflows/front-end/nested",
                        "type": "directory",
                    },
                ],
            )
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(404)
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "test"}})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            capabilities = await adapter.probe()
            assert capabilities.workflow_list_route == "v2_query:/api/v2/userdata"
            assert capabilities.workflow_get_route == "path:/userdata/{path}"
            assert await adapter.list_workflow_files() == [
                "profile.api.json",
                "profile.workflow.json",
            ]
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert calls.count(("/api/v2/userdata", {"path": "workflows/front-end"})) == 2
