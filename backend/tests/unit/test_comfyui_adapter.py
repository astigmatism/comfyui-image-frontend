from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from app.config import Settings
from app.errors import AppError
from app.services.comfyui import ComfyUIAdapter, _queue_prompt_ids


def settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path,
        "session_secret": "test-secret",
        "test_mode": True,
        "comfyui_base_url": "http://comfy.test",
        "comfyui_instance_id": "fixture-instance",
        "comfyui_user": "alice",
        "comfyui_workflow_directory": "workflows",
    }
    values.update(overrides)
    return Settings(**values)


def test_queue_prompt_id_parser_accepts_comfyui_list_and_object_shapes() -> None:
    running, pending = _queue_prompt_ids(
        {
            "queue_running": [[1, "run-1", {}], {"prompt_id": "run-2"}],
            "queue_pending": [[2, "wait-1", {}], {"id": "wait-2"}],
        }
    )
    assert running == {"run-1", "run-2"}
    assert pending == {"wait-1", "wait-2"}


def test_preferred_v2_listing_is_recursive_and_preserves_comfy_user(tmp_path: Path) -> None:
    calls: list[tuple[bytes, dict[str, str], str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.split(b"?", 1)[0]
        calls.append((raw_path, dict(request.url.params), request.headers.get("Comfy-User")))
        if raw_path == b"/object_info":
            return httpx.Response(200, json={})
        if raw_path == b"/v2/userdata":
            assert dict(request.url.params) == {"path": "workflows"}
            return httpx.Response(
                200,
                json=[
                    {
                        "path": "workflows/nested/source.interface.json",
                        "name": "source.interface.json",
                        "type": "file",
                    },
                    {"path": "workflows/nested", "name": "nested", "type": "directory"},
                ],
            )
        if raw_path == b"/userdata/workflows%2F.__frontend_probe_missing__.json":
            return httpx.Response(404)
        if raw_path == b"/system_stats":
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            capabilities = await adapter.probe()
            assert capabilities.workflow_list_route == "v2_query:/v2/userdata"
            assert await adapter.list_workflow_files() == ["workflows/nested/source.interface.json"]
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert calls
    assert {header for _, _, header in calls} == {"alice"}


def test_documented_fallback_includes_recurse_and_full_info(tmp_path: Path) -> None:
    fallback_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fallback_calls
        raw_path = request.url.raw_path.split(b"?", 1)[0]
        assert request.headers.get("Comfy-User") == "alice"
        if raw_path == b"/object_info":
            return httpx.Response(200, json={})
        if raw_path == b"/v2/userdata":
            return httpx.Response(404)
        if raw_path == b"/userdata" and request.url.params:
            fallback_calls += 1
            assert dict(request.url.params) == {
                "dir": "workflows",
                "recurse": "true",
                "full_info": "true",
            }
            return httpx.Response(200, json={"files": ["workflows/a/b.interface.json"]})
        if raw_path == b"/userdata/workflows%2F.__frontend_probe_missing__.json":
            return httpx.Response(404)
        if raw_path == b"/system_stats":
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            capabilities = await adapter.probe()
            assert capabilities.workflow_list_route == "query:/userdata"
            assert await adapter.list_workflow_files() == ["workflows/a/b.interface.json"]
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert fallback_calls == 2


@pytest.mark.parametrize(
    "invalid_response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"unexpected": []}),
        httpx.Response(200, content=b"[]" * (2 * 1024 * 1024 + 1)),
    ],
)
def test_invalid_preferred_listing_response_uses_documented_fallback(
    tmp_path: Path, invalid_response: httpx.Response
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.split(b"?", 1)[0]
        if raw_path == b"/object_info":
            return httpx.Response(200, json={})
        if raw_path == b"/v2/userdata":
            return invalid_response
        if raw_path == b"/userdata" and request.url.params:
            return httpx.Response(200, json={"files": ["workflows/source.interface.json"]})
        if raw_path == b"/userdata/workflows%2F.__frontend_probe_missing__.json":
            return httpx.Response(404)
        if raw_path == b"/system_stats":
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            capabilities = await adapter.probe()
            assert capabilities.workflow_list_route == "query:/userdata"
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_nested_artifact_path_is_encoded_as_one_route_segment_and_returns_exact_bytes(
    tmp_path: Path,
) -> None:
    expected = b'{"exact":"bytes", "spacing": true}\n'
    encoded = b"/userdata/workflows%2Fnested%2Fsource.interface.json"
    raw_paths: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.split(b"?", 1)[0]
        raw_paths.append(raw_path)
        if raw_path == b"/object_info":
            return httpx.Response(200, json={})
        if raw_path == b"/v2/userdata":
            return httpx.Response(200, json=[])
        if raw_path == b"/userdata/workflows%2F.__frontend_probe_missing__.json":
            return httpx.Response(404)
        if raw_path == b"/system_stats":
            return httpx.Response(200, json={})
        if raw_path == encoded:
            return httpx.Response(200, content=expected)
        return httpx.Response(404)

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            await adapter.probe()
            result = await adapter.get_userdata_file(
                "workflows/nested/source.interface.json", maximum_bytes=1024
            )
            assert result == expected
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert encoded in raw_paths
    assert b"/userdata/workflows/nested/source.interface.json" not in raw_paths


def test_encoded_slash_normalization_by_proxy_fails_clearly(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.split(b"?", 1)[0]
        if raw_path == b"/object_info":
            return httpx.Response(200, json={})
        if raw_path == b"/v2/userdata":
            return httpx.Response(200, json=[])
        if raw_path == b"/userdata/workflows%2F.__frontend_probe_missing__.json":
            return httpx.Response(404)
        if raw_path == b"/system_stats":
            return httpx.Response(200, json={})
        # Models a reverse proxy that cannot route the encoded single segment to ComfyUI.
        return httpx.Response(404)

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            await adapter.probe()
            with pytest.raises(httpx.HTTPStatusError) as exc:
                await adapter.get_userdata_file(
                    "workflows/nested/source.interface.json", maximum_bytes=1024
                )
            assert exc.value.response.status_code == 404
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_userdata_response_size_is_enforced_before_parsing(tmp_path: Path) -> None:
    oversized = b"x" * 1025

    def handler(request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.split(b"?", 1)[0]
        if raw_path == b"/object_info":
            return httpx.Response(200, json={})
        if raw_path == b"/v2/userdata":
            return httpx.Response(200, json=[])
        if raw_path == b"/userdata/workflows%2F.__frontend_probe_missing__.json":
            return httpx.Response(404)
        if raw_path == b"/system_stats":
            return httpx.Response(200, json={})
        return httpx.Response(200, content=oversized, headers={"content-length": "1025"})

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            await adapter.probe()
            with pytest.raises(AppError) as exc:
                await adapter.get_userdata_file("workflows/source.json", maximum_bytes=1024)
            assert exc.value.code == "response_too_large"
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_prompt_submission_preserves_extra_pnginfo_and_queue_validation_details(
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(200, json={"prompt_id": "native-123"})
        return httpx.Response(
            400,
            json={"error": {"type": "invalid_prompt"}, "node_errors": {"10": {"errors": []}}},
        )

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            workflow = {"nodes": [{"id": 10}]}
            prompt_id = await adapter.submit_prompt(
                {"10": {"class_type": "CIFTextParameter", "inputs": {"value": "x"}}},
                "client-1",
                extra_data={"extra_pnginfo": {"workflow": workflow}},
            )
            assert prompt_id == "native-123"
            with pytest.raises(AppError) as exc:
                await adapter.submit_prompt({}, "client-2")
            assert exc.value.code == "comfyui_prompt_rejected"
            assert exc.value.details["response"] == {
                "error": {"type": "invalid_prompt"},
                "node_errors": {"10": {"errors": []}},
            }
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert calls[0]["extra_data"]["extra_pnginfo"]["workflow"] == {"nodes": [{"id": 10}]}


def test_prompt_submission_server_and_transport_failures_are_uncertain(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": {"type": "server_error"}})
        raise httpx.ReadError("response connection closed", request=request)

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(AppError) as server_exc:
                await adapter.submit_prompt({}, "server-error")
            assert server_exc.value.code == "comfyui_submission_uncertain"
            assert server_exc.value.details == {"status": 503}

            with pytest.raises(AppError) as transport_exc:
                await adapter.submit_prompt({}, "read-error")
            assert transport_exc.value.code == "comfyui_submission_uncertain"
            assert transport_exc.value.details == {"transport": "ReadError"}
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_cancel_interrupts_only_when_target_is_proven_sole_running_prompt(
    tmp_path: Path,
) -> None:
    interrupt_payloads: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [[0, "ours", {}]]})
        if request.method == "POST" and request.url.path == "/interrupt":
            interrupt_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            await adapter.cancel("ours", running=True)
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert interrupt_payloads == [{"prompt_id": "ours"}]


@pytest.mark.parametrize("queue_inspection_fails", [False, True])
def test_cancel_never_interrupts_an_external_or_unproven_running_prompt(
    tmp_path: Path, queue_inspection_fails: bool
) -> None:
    methods_and_paths: list[tuple[str, str]] = []
    delete_payloads: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods_and_paths.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/queue":
            if queue_inspection_fails:
                return httpx.Response(503, json={})
            return httpx.Response(200, json={"queue_running": [[0, "external", {}]]})
        if request.method == "POST" and request.url.path == "/queue":
            delete_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={})
        if request.url.path == "/interrupt":
            raise AssertionError("global interrupt must not target an external prompt")
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            await adapter.cancel("ours", running=True)
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert ("POST", "/interrupt") not in methods_and_paths
    assert delete_payloads == [{"delete": ["ours"]}]


@pytest.mark.parametrize(
    "queue_running",
    [
        [[0, "ours", {}], [1, "external", {}]],
        [[0, "ours", {}], [1]],
        [[0, "ours", {}], [1, "ours", {}]],
    ],
)
def test_cancel_refuses_global_interrupt_without_an_unambiguous_sole_target(
    tmp_path: Path, queue_running: list[list[object]]
) -> None:
    interrupt_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal interrupt_called
        if request.method == "GET" and request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": queue_running})
        if request.url.path == "/interrupt":
            interrupt_called = True
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(AppError) as exc:
                await adapter.cancel("ours", running=True)
            assert exc.value.code == "cancellation_targeting_unavailable"
        finally:
            await adapter.close()

    asyncio.run(scenario())
    assert interrupt_called is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"different-prompt": {"outputs": {}, "status": {"status_str": "running"}}},
    ],
)
def test_history_without_requested_prompt_is_not_mistaken_for_terminal_history(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/history/wanted-prompt"
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            assert await adapter.history("wanted-prompt") is None
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid_number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_history_rejects_nonfinite_and_overflowing_numbers(
    tmp_path: Path, invalid_number: str
) -> None:
    raw_history = (
        '{"wanted-prompt":{"outputs":{"1":{"ui":{"value":' + invalid_number + "}}}}}"
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/history/wanted-prompt"
        return httpx.Response(200, content=raw_history)

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(AppError) as exc:
                await adapter.history("wanted-prompt")
            assert exc.value.code == "invalid_comfyui_response"
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_websocket_events_drop_nonfinite_and_overflowing_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.messages = iter(
                [
                    '{"type":"progress","data":{"value":NaN}}',
                    '{"type":"progress","data":{"value":1e999}}',
                    '{"type":"progress","data":{"value":0.5}}',
                ]
            )

        async def __aenter__(self) -> FakeWebSocket:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def __aiter__(self) -> FakeWebSocket:
            return self

        async def __anext__(self) -> str:
            try:
                return next(self.messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    monkeypatch.setattr(
        "app.services.comfyui.websockets.connect", lambda *args, **kwargs: FakeWebSocket()
    )

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path))
        try:
            assert [event async for event in adapter.events("client-1")] == [
                {"type": "progress", "data": {"value": 0.5}}
            ]
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "reference",
    [
        {"filename": "../secret.png", "subfolder": "", "type": "output"},
        {"filename": "image.png", "subfolder": "../private", "type": "output"},
        {"filename": "image.png", "subfolder": "", "type": "filesystem"},
    ],
)
def test_view_references_reject_paths_and_storage_types_before_network(
    tmp_path: Path, reference: dict[str, str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unsafe reference reached network: {request.url}")

    async def scenario() -> None:
        adapter = ComfyUIAdapter(settings(tmp_path), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(AppError) as exc:
                await adapter.retrieve_artifact(reference)
            assert exc.value.code == "output_unclassified"
        finally:
            await adapter.close()

    asyncio.run(scenario())
