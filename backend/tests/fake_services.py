from __future__ import annotations

import asyncio
import copy
import json
import socket
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from urllib.parse import unquote

import uvicorn
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image, ImageDraw

from tests.publication_fixtures import build_publication_files, object_info_fixture
from tests.router_fixtures import (
    NIGHTTIME_OFFLINE,
    paired_models,
    router_document,
    solo_models,
    sse_event,
)

ROUTER_PATHS = frozenset({"/api/chat", "/api/tags", "/v1/router/capabilities", "/v1/router/events"})


def build_workflow_files() -> dict[str, bytes]:
    """Compatibility name retained for integration tests that mutate the fake catalog."""

    return build_publication_files(include_generic=True)


def make_png(label: str, *, width: int = 96, height: int = 72) -> bytes:
    image = Image.new("RGB", (width, height), (24, 38, 56))
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 3, width - 4, height - 4), outline=(96, 148, 210), width=2)
    draw.text((8, height // 2 - 5), label[:14], fill=(235, 242, 250))
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _node_value(graph: dict[str, Any], class_type: str, default: Any = None) -> Any:
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            inputs = node.get("inputs", {})
            if isinstance(inputs, dict) and "value" in inputs:
                return inputs["value"]
    return default


def _first_node_id(graph: dict[str, Any], class_type: str, default: str) -> str:
    for node_id, node in graph.items():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            return str(node_id)
    return default


def _nodes(graph: dict[str, Any], class_type: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (str(node_id), node)
        for node_id, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


@dataclass
class FakeServiceState:
    execution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    workflow_files: dict[str, bytes] = field(default_factory=build_workflow_files)
    object_info: dict[str, Any] = field(default_factory=object_info_fixture)
    service_available: bool = True
    ollama_available: bool = True
    reject_prompts: bool = False
    fail_retrieval: bool = False
    fail_artifact_cleanup: bool = False
    retrieval_failure_substrings: set[str] = field(default_factory=set)
    retrieval_failures_remaining: int = 0
    # A source the frontend already archived and deleted answers 404, unlike the 500 of
    # retrieval_failure_substrings. Only the absent case may trigger a re-execution.
    absent_source_substrings: set[str] = field(default_factory=set)
    execution_cache_resets: int = 0
    report_cached_execution: bool = False
    disconnect_websocket: bool = False
    initial_event_delay: float = 0.02
    default_stage_delay: float = 0.08
    slow_stage_delay: float = 0.5
    wait_for_cancel_prompts: set[str] = field(default_factory=set)
    cancellation_events: dict[str, asyncio.Event] = field(default_factory=dict)
    stage_delay_overrides: dict[str, float] = field(default_factory=dict)
    listing_mode: str = "v2"
    history_delay_polls: int = 0
    hide_history: bool = False
    force_nonterminal_history: bool = False
    emit_cached_only: bool = False
    terminal_event_type: str | None = None
    orphan_prompt_substrings: set[str] = field(default_factory=set)
    models: list[str] = field(default_factory=lambda: ["zeta:latest", "alpha:latest", "nighttime"])
    ollama_effective_model: str | None = None
    ollama_delay_seconds: float = 0
    ollama_include_thinking: bool = True
    ollama_response_in_thinking: bool = False
    ollama_response_prompt: str | None = None
    ollama_response_prompts: list[str] = field(default_factory=list)
    ollama_generate_responses: list[dict[str, Any]] = field(default_factory=list)
    ollama_generate_failure_status: int | None = None
    ollama_generate_failures_remaining: int = 0
    ollama_thinking_overflows: bool = False
    ollama_capabilities: list[str] = field(
        default_factory=lambda: ["completion", "thinking", "vision"]
    )
    # One score list per vision evaluation, in expectation order; empty means all 100.
    ollama_vision_scores: list[list[int]] = field(default_factory=list)
    ollama_vision_calls: list[dict[str, Any]] = field(default_factory=list)
    ollama_vision_seen: dict[str, int] = field(default_factory=dict)
    # LLM Router (docs/llm-router-contract.md): switchable configurations, the capabilities
    # document and its event stream, and the router's error codes.
    router_configuration: str = "paired"
    router_models: list[dict[str, Any]] | None = None
    router_offline_services: list[dict[str, Any]] | None = None
    router_unavailable: set[str] = field(default_factory=set)
    router_draining: bool = False
    router_maintenance: bool = False
    router_capabilities_status: int | None = None
    router_events_status: int | None = None
    router_events_generation: int = 0
    router_event_connections: int = 0
    router_keepalive_seconds: float = 0.5
    router_chat_errors: list[tuple[int, Any]] = field(default_factory=list)
    router_declining_services: set[str] = field(default_factory=set)
    router_requests: list[dict[str, Any]] = field(default_factory=list)
    histories: dict[str, dict[str, Any]] = field(default_factory=dict)
    history_calls: dict[str, int] = field(default_factory=dict)
    prompts: dict[str, dict[str, Any]] = field(default_factory=dict)
    running_prompt_ids: set[str] = field(default_factory=set)
    queued_prompt_ids: set[str] = field(default_factory=set)
    cancelled_prompt_ids: set[str] = field(default_factory=set)
    output_files: dict[tuple[str, str, str], bytes] = field(default_factory=dict)
    artifact_cleanup_requests: list[list[dict[str, str]]] = field(default_factory=list)
    event_log: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    websocket_clients: dict[str, list[WebSocket]] = field(default_factory=dict)
    submitted: list[dict[str, Any]] = field(default_factory=list)
    ollama_calls: list[dict[str, Any]] = field(default_factory=list)
    speech_to_text_available: bool = True
    speech_to_text_result: str = "transcribed speech"
    speech_to_text_calls: list[dict[str, Any]] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    http_request_paths: list[str] = field(default_factory=list)
    comfy_user_headers: list[tuple[str, str | None]] = field(default_factory=list)
    userdata_raw_paths: list[bytes] = field(default_factory=list)
    background_tasks: set[asyncio.Task[None]] = field(default_factory=set)

    def reset_runtime(self) -> None:
        self.workflow_files = build_workflow_files()
        self.object_info = object_info_fixture()
        self.service_available = True
        self.ollama_available = True
        self.reject_prompts = False
        self.fail_retrieval = False
        self.fail_artifact_cleanup = False
        self.retrieval_failure_substrings.clear()
        self.retrieval_failures_remaining = 0
        self.absent_source_substrings.clear()
        self.execution_cache_resets = 0
        self.report_cached_execution = False
        self.disconnect_websocket = False
        self.initial_event_delay = 0.02
        self.default_stage_delay = 0.08
        self.slow_stage_delay = 0.5
        self.wait_for_cancel_prompts.clear()
        self.cancellation_events.clear()
        self.stage_delay_overrides.clear()
        self.listing_mode = "v2"
        self.history_delay_polls = 0
        self.hide_history = False
        self.force_nonterminal_history = False
        self.emit_cached_only = False
        self.terminal_event_type = None
        self.orphan_prompt_substrings.clear()
        self.models = ["zeta:latest", "alpha:latest", "nighttime"]
        self.ollama_effective_model = None
        self.ollama_delay_seconds = 0
        self.ollama_include_thinking = True
        self.ollama_response_in_thinking = False
        self.ollama_response_prompt = None
        self.ollama_response_prompts.clear()
        self.ollama_generate_responses.clear()
        self.ollama_generate_failure_status = None
        self.ollama_generate_failures_remaining = 0
        self.ollama_thinking_overflows = False
        self.ollama_capabilities = ["completion", "thinking", "vision"]
        self.ollama_vision_scores.clear()
        self.ollama_vision_calls.clear()
        self.ollama_vision_seen.clear()
        self.router_configuration = "paired"
        self.router_models = None
        self.router_offline_services = None
        self.router_unavailable.clear()
        self.router_draining = False
        self.router_maintenance = False
        self.router_capabilities_status = None
        self.router_events_status = None
        self.router_events_generation += 1
        self.router_keepalive_seconds = 0.5
        self.router_chat_errors.clear()
        self.router_declining_services.clear()
        self.router_requests.clear()
        self.histories.clear()
        self.history_calls.clear()
        self.prompts.clear()
        self.running_prompt_ids.clear()
        self.queued_prompt_ids.clear()
        self.cancelled_prompt_ids.clear()
        self.output_files.clear()
        self.artifact_cleanup_requests.clear()
        self.event_log.clear()
        self.websocket_clients.clear()
        self.submitted.clear()
        self.ollama_calls.clear()
        self.speech_to_text_available = True
        self.speech_to_text_result = "transcribed speech"
        self.speech_to_text_calls.clear()
        self.uploaded.clear()
        self.http_request_paths.clear()
        self.comfy_user_headers.clear()
        self.userdata_raw_paths.clear()
        self.background_tasks.clear()

    def router_document(self) -> dict[str, Any]:
        """The capabilities document for the current fake configuration."""

        solo = self.router_configuration == "solo"
        vision = "vision" in self.ollama_capabilities
        if self.router_models is not None:
            models = copy.deepcopy(self.router_models)
        else:
            models = solo_models(vision=vision) if solo else paired_models(vision=vision)
        for model in models:
            if model["service"] in self.router_unavailable:
                model["available"] = False
        offline = (
            self.router_offline_services
            if self.router_offline_services is not None
            else ([NIGHTTIME_OFFLINE] if solo else [])
        )
        return router_document(
            models,
            configuration_id="fake-solo" if solo else f"fake-{self.router_configuration}",
            exclusive=solo,
            draining=self.router_draining,
            maintenance=self.router_maintenance,
            offline_services=offline,
        )

    async def emit(self, client_id: str, event: dict[str, Any]) -> None:
        self.event_log.setdefault(client_id, []).append(copy.deepcopy(event))
        for websocket in list(self.websocket_clients.get(client_id, [])):
            try:
                await websocket.send_json(event)
            except Exception:
                with suppress(KeyError, ValueError):
                    self.websocket_clients[client_id].remove(websocket)

    def _record_image(self, prompt_id: str, suffix: str, label: str) -> dict[str, str]:
        name = f"{prompt_id}-{suffix}.png"
        reference = {"filename": name, "subfolder": "fake", "type": "output"}
        self.output_files[(name, "fake", "output")] = make_png(label)
        return reference

    def _record_generic_file(self, prompt_id: str) -> dict[str, str]:
        name = f"{prompt_id}-metadata.json"
        reference = {"filename": name, "subfolder": "fake", "type": "output"}
        self.output_files[(name, "fake", "output")] = b'{"fixture":"generic-output"}'
        return reference

    async def execute_prompt(self, prompt_id: str) -> None:
        async with self.execution_lock:
            if prompt_id in self.queued_prompt_ids:
                await self._execute_prompt(prompt_id)

    async def _execute_prompt(self, prompt_id: str) -> None:
        record = self.prompts[prompt_id]
        client_id = str(record["client_id"])
        graph = record["graph"]
        assert isinstance(graph, dict)
        prompt_text = str(_node_value(graph, "CIFTextParameter", ""))
        stage_node = _first_node_id(graph, "FakeImageOutput", "900")
        publisher_nodes = _nodes(graph, "CIFPublishImage")
        delay = (
            self.slow_stage_delay if "slow" in prompt_text.casefold() else self.default_stage_delay
        )
        delay = self.stage_delay_overrides.get(prompt_text, delay)
        self.queued_prompt_ids.discard(prompt_id)
        self.running_prompt_ids.add(prompt_id)
        self.histories[prompt_id] = {
            "status": {"status_str": "running", "completed": False, "messages": []},
            "outputs": {},
            "prompt": [0, prompt_id, copy.deepcopy(graph), copy.deepcopy(record.get("extra_data"))],
            "extra_data": copy.deepcopy(record.get("extra_data")),
        }
        started_ms = int(time.time() * 1000)
        start_message = ["execution_start", {"prompt_id": prompt_id, "timestamp": started_ms}]
        self.histories[prompt_id]["status"]["messages"].append(start_message)
        await self.emit(client_id, {"type": "execution_start", "data": start_message[1]})
        await asyncio.sleep(self.initial_event_delay)
        text_publishers = _nodes(graph, "CIFPublishText")
        if text_publishers:
            await asyncio.sleep(delay)
            seed = _node_value(graph, "CIFSeedParameter", 1)
            text = f"{prompt_text or 'the woman'} explores a quiet forest, variation {seed}."
            for node_id, node in text_publishers:
                inputs = node["inputs"]
                self.histories[prompt_id]["outputs"][node_id] = {
                    "text": [text],
                    "comfyui_image_frontend": [
                        {
                            "schema_version": "comfyui-image-frontend.output/v1",
                            "output_id": inputs["output_id"],
                            "instance_uuid": inputs["instance_uuid"],
                            "role": "final",
                            "kind": "text",
                            "cardinality": "one",
                            "value": text,
                        }
                    ],
                }
            messages: list[Any] = []
            if self.report_cached_execution:
                messages.append(
                    ["execution_cached", {"prompt_id": prompt_id, "nodes": list(graph)}]
                )
            self.histories[prompt_id]["status"] = {
                "status_str": "success",
                "completed": True,
                "messages": messages,
            }
            self.running_prompt_ids.discard(prompt_id)
            return

        base_ref = self._record_image(prompt_id, "base", "base")
        base_output = {
            "images": [base_ref],
            "text": ["base metadata"],
            "ui": {"progress_label": "Base image"},
        }
        self.histories[prompt_id]["outputs"]["900"] = base_output
        if not self.emit_cached_only and not self.terminal_event_type:
            await self.emit(
                client_id,
                {"type": "executing", "data": {"prompt_id": prompt_id, "node": stage_node}},
            )
            await self.emit(
                client_id,
                {
                    "type": "progress_state",
                    "data": {
                        "prompt_id": prompt_id,
                        "nodes": {
                            stage_node: {
                                "value": 1,
                                "max": 2,
                                "state": "running",
                                "node_id": stage_node,
                                "display_node_id": stage_node,
                                "real_node_id": stage_node,
                                "parent_node_id": None,
                            }
                        },
                    },
                },
            )
            # Real ComfyUI versions can emit both forms. The application should prefer the
            # structured state for this node and ignore the equivalent legacy callback.
            await self.emit(
                client_id,
                {
                    "type": "progress",
                    "data": {"prompt_id": prompt_id, "node": stage_node, "value": 1, "max": 2},
                },
            )
            await self.emit(
                client_id,
                {
                    "type": "progress_state",
                    "data": {
                        "prompt_id": prompt_id,
                        "nodes": {
                            stage_node: {
                                "value": 2,
                                "max": 2,
                                "state": "running",
                                "node_id": stage_node,
                                "display_node_id": stage_node,
                                "real_node_id": stage_node,
                                "parent_node_id": None,
                            }
                        },
                    },
                },
            )
            await self.emit(
                client_id,
                {
                    "type": "executed",
                    "data": {
                        "prompt_id": prompt_id,
                        "node": "900",
                        "output": copy.deepcopy(base_output),
                    },
                },
            )
        if any(
            fragment.casefold() in prompt_text.casefold()
            for fragment in self.orphan_prompt_substrings
        ):
            # Model an external interruption/reset that removes a prompt from ComfyUI's live
            # queue without updating its partial history or emitting a terminal event.
            self.running_prompt_ids.discard(prompt_id)
            return
        await asyncio.sleep(delay)

        # Cancellation tests hold the preview until their explicit interrupt.
        # Their outcome must not depend on beating a short wall-clock window.
        if (
            prompt_text in self.wait_for_cancel_prompts
            and prompt_id not in self.cancelled_prompt_ids
        ):
            await self.cancellation_events.setdefault(prompt_id, asyncio.Event()).wait()

        cancelled = prompt_id in self.cancelled_prompt_ids
        if cancelled and "race-success" not in prompt_text.casefold():
            self.histories[prompt_id]["status"] = {
                # ComfyUI records InterruptProcessingException as an unsuccessful executor
                # result. The message, rather than status_str, distinguishes interruption
                # from an ordinary execution failure.
                "status_str": "error",
                "completed": False,
                "messages": [["execution_interrupted", {"prompt_id": prompt_id}]],
            }
            await self.emit(
                client_id,
                {"type": "execution_interrupted", "data": {"prompt_id": prompt_id}},
            )
            self.running_prompt_ids.discard(prompt_id)
            return

        if "fail" in prompt_text.casefold():
            self.histories[prompt_id]["status"] = {
                "status_str": "error",
                "completed": True,
                "messages": [
                    ["execution_error", {"node_id": stage_node, "exception_type": "FakeFailure"}]
                ],
            }
            await self.emit(
                client_id,
                {
                    "type": "execution_error",
                    "data": {
                        "prompt_id": prompt_id,
                        "node_id": stage_node,
                        "node_type": "FakeImageOutput",
                        "exception_type": "FakeFailure",
                    },
                },
            )
            self.running_prompt_ids.discard(prompt_id)
            return

        batch_count = 2 if "multi" in prompt_text.casefold() else 1
        terminal_node = "901"
        if publisher_nodes:
            native_ref = self._record_image(prompt_id, "native-result", "native result")
            native_output: dict[str, Any] = {
                "images": [native_ref],
                "text": ["complete native text result"],
                "hashes": {
                    "asset_sha256": "f" * 64,
                    "model_sha256": "e" * 64,
                },
                "dimensions": {"width": 96, "height": 72},
                "custom_ui": {
                    "palette": ["indigo", "gold"],
                    "quality": {"score": 0.875, "accepted": True},
                },
            }
            if "generic locator" in prompt_text.casefold():
                native_output["files"] = [self._record_generic_file(prompt_id)]
            self.histories[prompt_id]["outputs"]["901"] = native_output
            if not self.emit_cached_only and not self.terminal_event_type:
                await self.emit(
                    client_id,
                    {
                        "type": "executed",
                        "data": {
                            "prompt_id": prompt_id,
                            "node": "901",
                            "output": copy.deepcopy(native_output),
                        },
                    },
                )

            for publisher_index, (publisher_node_id, publisher_node) in enumerate(publisher_nodes):
                inputs = publisher_node.get("inputs", {})
                assert isinstance(inputs, dict)
                output_id = str(inputs.get("output_id") or "final_image")
                role = str(inputs.get("role") or "final")
                instance_uuid = str(
                    inputs.get("instance_uuid")
                    or f"00000000-0000-4000-8000-{int(publisher_node_id):012d}"
                )
                description = str(inputs.get("description") or f"Fixture {role} image output.")
                references = [
                    self._record_image(
                        prompt_id,
                        f"{output_id}-{batch_index}",
                        f"{output_id} {batch_index + 1}",
                    )
                    for batch_index in range(batch_count)
                ]
                publisher_output = {
                    "images": copy.deepcopy(references),
                    "comfyui_image_frontend": [
                        {
                            "schema_version": "comfyui-image-frontend.interface/v1",
                            "output_id": output_id,
                            "instance_uuid": instance_uuid,
                            "role": role,
                            "kind": "image",
                            "cardinality": "many",
                            "description": description,
                            "artifacts": [
                                {"batch_index": batch_index, **copy.deepcopy(reference)}
                                for batch_index, reference in enumerate(references)
                            ],
                        }
                    ],
                    "ui": {
                        "publisher_timing": {
                            "sequence": publisher_index,
                            "seconds": round(0.15 + publisher_index * 0.05, 2),
                        }
                    },
                }
                self.histories[prompt_id]["outputs"][publisher_node_id] = publisher_output
                terminal_node = publisher_node_id
                if not self.emit_cached_only and not self.terminal_event_type:
                    await self.emit(
                        client_id,
                        {
                            "type": "executing",
                            "data": {"prompt_id": prompt_id, "node": publisher_node_id},
                        },
                    )
                    await self.emit(
                        client_id,
                        {
                            "type": "executed",
                            "data": {
                                "prompt_id": prompt_id,
                                "node": publisher_node_id,
                                "output": copy.deepcopy(publisher_output),
                            },
                        },
                    )
        else:
            final_refs = [
                self._record_image(prompt_id, f"final-{index}", f"final {index + 1}")
                for index in range(batch_count)
            ]
            final_output: dict[str, Any] = {
                "images": final_refs,
                "text": ["complete native text result"],
                "ui": {"comparison": {"enabled": True}},
            }
            if "generic locator" in prompt_text.casefold():
                final_output["files"] = [self._record_generic_file(prompt_id)]
            self.histories[prompt_id]["outputs"][terminal_node] = final_output
            if not self.emit_cached_only and not self.terminal_event_type:
                await self.emit(
                    client_id,
                    {
                        "type": "executing",
                        "data": {"prompt_id": prompt_id, "node": terminal_node},
                    },
                )
                await self.emit(
                    client_id,
                    {
                        "type": "executed",
                        "data": {
                            "prompt_id": prompt_id,
                            "node": terminal_node,
                            "output": copy.deepcopy(final_output),
                        },
                    },
                )
        if self.terminal_event_type:
            event_data: dict[str, Any] = {"prompt_id": prompt_id}
            if self.terminal_event_type == "execution_error":
                event_data.update(
                    {
                        "node_id": terminal_node,
                        "node_type": "FakeImageOutput",
                        "exception_type": "FakeWebSocketOnlyFailure",
                    }
                )
            await self.emit(
                client_id,
                {"type": self.terminal_event_type, "data": event_data},
            )
            # The terminal event deliberately leads durable history so reconciliation tests
            # can prove that a contradictory terminal history record wins.
            await asyncio.sleep(0.02)
        self.histories[prompt_id]["status"] = {
            "status_str": "success",
            "completed": True,
            "messages": [
                start_message,
                [
                    "execution_success",
                    {"prompt_id": prompt_id, "timestamp": int(time.time() * 1000)},
                ],
            ],
        }
        if self.emit_cached_only:
            self.histories[prompt_id]["status"]["messages"].append(
                ["execution_cached", {"prompt_id": prompt_id, "nodes": list(graph)}]
            )
        if self.terminal_event_type:
            self.running_prompt_ids.discard(prompt_id)
            return
        if self.emit_cached_only:
            await self.emit(
                client_id,
                {"type": "execution_cached", "data": {"prompt_id": prompt_id, "nodes": []}},
            )
        else:
            await self.emit(
                client_id,
                {"type": "execution_success", "data": {"prompt_id": prompt_id}},
            )
        self.running_prompt_ids.discard(prompt_id)


def create_fake_services_app(state: FakeServiceState) -> FastAPI:
    app = FastAPI(title="Deterministic fake ComfyUI and Ollama")

    @app.middleware("http")
    async def preserve_userdata_route_segment(request: Request, call_next):  # type: ignore[no-untyped-def]
        state.http_request_paths.append(request.url.path)
        if request.url.path in ROUTER_PATHS:
            state.router_requests.append(
                {
                    "path": request.url.path,
                    "client_name": request.headers.get("x-client-name"),
                    "if_none_match": request.headers.get("if-none-match"),
                    "authorization": request.headers.get("authorization"),
                }
            )
        raw_path = request.scope.get("raw_path", b"")
        if isinstance(raw_path, bytes) and raw_path.startswith(b"/userdata/"):
            state.userdata_raw_paths.append(raw_path.split(b"?", 1)[0])
            raw_segment = raw_path[len(b"/userdata/") :].split(b"?", 1)[0]
            if b"/" in raw_segment:
                return Response(status_code=404)
            try:
                request.scope["path"] = f"/userdata/{raw_segment.decode('ascii')}"
            except UnicodeDecodeError:
                return Response(status_code=404)
        if request.url.path.startswith(("/userdata", "/v2/userdata", "/object_info")):
            state.comfy_user_headers.append((request.url.path, request.headers.get("Comfy-User")))
        return await call_next(request)

    def require_comfy() -> None:
        if not state.service_available:
            raise HTTPException(status_code=503, detail="fake ComfyUI unavailable")

    @app.get("/object_info")
    async def object_info() -> dict[str, Any]:
        require_comfy()
        return state.object_info

    @app.post("/free")
    async def free(payload: dict[str, Any]) -> dict[str, bool]:
        require_comfy()
        if payload.get("free_memory"):
            state.execution_cache_resets += 1
            state.report_cached_execution = False
        return {"ok": True}

    @app.get("/system_stats")
    async def system_stats() -> dict[str, Any]:
        require_comfy()
        return {"system": {"comfyui_version": "fake-1.0"}, "devices": [{"name": "fake-gpu"}]}

    @app.get("/v2/userdata")
    async def userdata_v2(path: str = "workflows") -> Any:
        require_comfy()
        if state.listing_mode != "v2":
            raise HTTPException(status_code=404)
        prefix = path.rstrip("/") + "/"
        return [
            {"name": key.rsplit("/", 1)[-1], "path": key, "type": "file"}
            for key in sorted(state.workflow_files)
            if key.startswith(prefix)
        ]

    @app.get("/userdata")
    async def userdata_fallback(request: Request) -> Any:
        require_comfy()
        if state.listing_mode not in {"fallback", "v2"}:
            raise HTTPException(status_code=404)
        directory = request.query_params.get("dir", "workflows").rstrip("/")
        prefix = directory + "/"
        return {"files": [key for key in sorted(state.workflow_files) if key.startswith(prefix)]}

    @app.get("/userdata/{file}")
    async def userdata_path(file: str) -> Response:
        require_comfy()
        key = unquote(file)
        if key not in state.workflow_files:
            raise HTTPException(status_code=404)
        return Response(state.workflow_files[key], media_type="application/json")

    @app.post("/prompt")
    async def submit_prompt(request: Request) -> Response:
        require_comfy()
        if state.reject_prompts:
            return JSONResponse(
                {
                    "error": {"type": "prompt_outputs_failed_validation"},
                    "node_errors": {"20": {"errors": [{"message": "fake validation error"}]}},
                },
                status_code=400,
            )
        payload = await request.json()
        graph = payload.get("prompt")
        client_id = payload.get("client_id")
        extra_data = payload.get("extra_data")
        if not isinstance(graph, dict) or not isinstance(client_id, str):
            raise HTTPException(status_code=422)
        prompt_id = str(uuid.uuid4())
        state.prompts[prompt_id] = {
            "graph": copy.deepcopy(graph),
            "client_id": client_id,
            "extra_data": copy.deepcopy(extra_data),
        }
        state.queued_prompt_ids.add(prompt_id)
        state.submitted.append(
            {
                "prompt_id": prompt_id,
                "client_id": client_id,
                "prompt": str(_node_value(graph, "CIFTextParameter", "")),
                "seed": str(_node_value(graph, "CIFSeedParameter", "")),
                "width": _node_value(graph, "CIFIntegerParameter"),
                "choice": _node_value(graph, "CIFChoiceParameter"),
                "strength": _node_value(graph, "CIFDecimalParameter"),
                "extra_data": copy.deepcopy(extra_data),
                "graph": copy.deepcopy(graph),
                "websocket_connected_before_submit": bool(state.websocket_clients.get(client_id)),
            }
        )
        task = asyncio.create_task(state.execute_prompt(prompt_id), name=f"fake-prompt-{prompt_id}")
        state.background_tasks.add(task)
        task.add_done_callback(state.background_tasks.discard)
        return JSONResponse({"prompt_id": prompt_id, "number": len(state.submitted)})

    @app.websocket("/ws")
    async def websocket_events(websocket: WebSocket) -> None:
        state.comfy_user_headers.append(("/ws", websocket.headers.get("Comfy-User")))
        client_id = websocket.query_params.get("clientId") or ""
        await websocket.accept()
        if state.disconnect_websocket:
            await websocket.close(code=1011)
            return
        state.websocket_clients.setdefault(client_id, []).append(websocket)
        await websocket.send_json(
            {
                "type": "status",
                "data": {
                    "status": {"exec_info": {"queue_remaining": 0}},
                    "sid": client_id,
                },
            }
        )
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            with suppress(ValueError):
                state.websocket_clients.get(client_id, []).remove(websocket)

    @app.get("/history/{prompt_id}")
    async def history(prompt_id: str) -> dict[str, Any]:
        require_comfy()
        state.history_calls[prompt_id] = state.history_calls.get(prompt_id, 0) + 1
        if state.hide_history:
            raise HTTPException(status_code=404)
        if state.history_calls[prompt_id] <= state.history_delay_polls:
            raise HTTPException(status_code=404)
        if prompt_id not in state.histories:
            raise HTTPException(status_code=404)
        history = copy.deepcopy(state.histories[prompt_id])
        if state.force_nonterminal_history:
            history["status"] = {"status_str": "running", "completed": False, "messages": []}
        return {prompt_id: history}

    @app.get("/queue")
    async def queue_state() -> dict[str, Any]:
        require_comfy()
        return {
            "queue_running": [
                [0, value, state.prompts.get(value, {}).get("graph", {})]
                for value in sorted(state.running_prompt_ids)
            ],
            "queue_pending": [
                [0, value, state.prompts.get(value, {}).get("graph", {})]
                for value in sorted(state.queued_prompt_ids)
            ],
        }

    @app.post("/queue")
    async def delete_queued(request: Request) -> dict[str, Any]:
        require_comfy()
        payload = await request.json()
        for prompt_id in payload.get("delete", []):
            state.queued_prompt_ids.discard(str(prompt_id))
            state.cancelled_prompt_ids.add(str(prompt_id))
            if signal := state.cancellation_events.get(str(prompt_id)):
                signal.set()
        return {"ok": True}

    @app.post("/interrupt")
    async def interrupt() -> Response:
        require_comfy()
        state.cancelled_prompt_ids.update(state.running_prompt_ids)
        for prompt_id in state.running_prompt_ids:
            if signal := state.cancellation_events.get(prompt_id):
                signal.set()
        return Response(status_code=204)

    @app.post("/upload/image")
    async def upload_image(
        image: UploadFile = File(...),
        type: str = Form("input"),
        subfolder: str = Form(""),
        overwrite: str = Form("false"),
    ) -> dict[str, str]:
        require_comfy()
        if type != "input" or overwrite != "false":
            raise HTTPException(status_code=400)
        content = await image.read()
        if not content:
            raise HTTPException(status_code=400)
        name = image.filename or f"upload-{uuid.uuid4()}.png"
        locator = f"{subfolder}/{name}" if subfolder else name
        state.uploaded.append(locator)
        state.output_files[(name, subfolder, "input")] = content
        return {"name": name, "subfolder": subfolder, "type": "input"}

    @app.get("/view")
    async def view(filename: str, subfolder: str = "", type: str = "output") -> Response:
        require_comfy()
        if any(fragment in filename for fragment in state.absent_source_substrings):
            raise HTTPException(status_code=404)
        targeted_failure = any(
            fragment in filename for fragment in state.retrieval_failure_substrings
        )
        if state.fail_retrieval or targeted_failure or state.retrieval_failures_remaining > 0:
            if state.retrieval_failures_remaining > 0:
                state.retrieval_failures_remaining -= 1
            raise HTTPException(status_code=500, detail="retrieval failed")
        key = (filename, subfolder, type)
        if key not in state.output_files:
            raise HTTPException(status_code=404)
        return Response(state.output_files[key], media_type="image/png")

    @app.post("/comfyui-image-frontend/artifacts/delete")
    async def delete_frontend_artifacts(request: Request) -> dict[str, int]:
        require_comfy()
        if state.fail_artifact_cleanup:
            raise HTTPException(status_code=500, detail="cleanup failed")
        payload = await request.json()
        artifacts = payload.get("artifacts", []) if isinstance(payload, dict) else []
        if not isinstance(artifacts, list):
            raise HTTPException(status_code=400)
        normalized: list[dict[str, str]] = []
        deleted = 0
        missing = 0
        for reference in artifacts:
            if not isinstance(reference, dict):
                raise HTTPException(status_code=400)
            filename = reference.get("filename")
            subfolder = reference.get("subfolder", "")
            storage_type = reference.get("type", "output")
            if not all(isinstance(value, str) for value in (filename, subfolder, storage_type)):
                raise HTTPException(status_code=400)
            if storage_type not in {"output", "temp"}:
                raise HTTPException(status_code=400)
            item = {
                "filename": str(filename),
                "subfolder": str(subfolder),
                "type": str(storage_type),
            }
            normalized.append(item)
            key = (item["filename"], item["subfolder"], item["type"])
            if state.output_files.pop(key, None) is None:
                missing += 1
            else:
                deleted += 1
        state.artifact_cleanup_requests.append(normalized)
        return {"deleted": deleted, "missing": missing}

    @app.get("/api/tags")
    async def ollama_tags() -> dict[str, Any]:
        if not state.ollama_available:
            raise HTTPException(status_code=503)
        return {
            "models": [
                {"name": name, "capabilities": list(state.ollama_capabilities)}
                for name in state.models
            ]
        }

    @app.get("/v1/router/capabilities")
    async def router_capabilities(request: Request) -> Response:
        if not state.ollama_available:
            return Response(status_code=503)
        if state.router_capabilities_status is not None:
            return JSONResponse(
                {"error": {"code": "FAKE_UNAVAILABLE", "message": "fake failure"}},
                status_code=state.router_capabilities_status,
            )
        document = state.router_document()
        etag = f'"{document["revision"]}"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(document, headers=headers)

    @app.get("/v1/router/events")
    async def router_events(request: Request) -> Response:
        if not state.ollama_available:
            return Response(status_code=503)
        if state.router_events_status is not None:
            return JSONResponse(
                {"error": {"code": "TOO_MANY_SUBSCRIBERS", "message": "fake subscriber limit"}},
                status_code=state.router_events_status,
            )
        generation = state.router_events_generation

        async def stream():  # type: ignore[no-untyped-def]
            state.router_event_connections += 1
            try:
                document = state.router_document()
                revision = document["revision"]
                yield "retry: 3000\n\n"
                yield sse_event("capabilities", document, identity=revision)
                yield sse_event("load", {"revision": revision, "load": {}})
                last_keepalive = time.monotonic()
                while state.router_events_generation == generation and state.ollama_available:
                    if await request.is_disconnected():
                        break
                    await asyncio.sleep(0.02)
                    document = state.router_document()
                    if document["revision"] != revision:
                        revision = document["revision"]
                        yield sse_event("capabilities", document, identity=revision)
                    if time.monotonic() - last_keepalive >= state.router_keepalive_seconds:
                        last_keepalive = time.monotonic()
                        yield ": keepalive\n\n"
            finally:
                state.router_event_connections -= 1

        return StreamingResponse(stream(), media_type="text/event-stream")

    def router_error(code: str, status: int, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    @app.post("/api/chat")
    async def ollama_generate(request: Request) -> Any:
        if not state.ollama_available:
            raise HTTPException(status_code=503)
        payload = await request.json()
        state.ollama_calls.append(copy.deepcopy(payload))
        if state.ollama_delay_seconds:
            await asyncio.sleep(state.ollama_delay_seconds)
        if state.router_chat_errors:
            status, body = state.router_chat_errors.pop(0)
            return JSONResponse(body, status_code=status)
        if state.ollama_generate_failures_remaining > 0:
            state.ollama_generate_failures_remaining -= 1
            # A legacy string error: clients must tolerate it (contract section 10).
            return JSONResponse(
                {"error": "configured Ollama generation failure"},
                status_code=state.ollama_generate_failure_status or 503,
            )
        # Router semantics: draining, then service resolution. Nothing is ever substituted.
        document = state.router_document()
        if state.router_maintenance:
            return router_error("MAINTENANCE_MODE", 503, "fake maintenance")
        if state.router_draining:
            return router_error("BACKEND_DRAINING", 503, "fake configuration switch")
        requested = payload.get("model") or "daytime"
        canonical = document["ids"].get(requested)
        if canonical is None:
            if any(
                requested in entry.get("aliases", []) or requested == entry.get("model")
                for entry in document["offline_services"]
            ):
                return router_error("SERVICE_OFFLINE", 503, f"{requested} is offline")
            return router_error("MODEL_NOT_FOUND", 404, f"Model {requested!r} was not found")
        served = next(model for model in document["models"] if model["id"] == canonical)
        if not served["available"]:
            return router_error("BACKEND_UNAVAILABLE", 503, f"{requested} backend is unhealthy")
        think = payload.get("think")
        if isinstance(think, str):
            efforts = set(served["metadata"]["reasoning"].get("efforts") or {})
            aliases = {"none": "off", "minimal": "low", "high": "xhigh", "max": "xhigh"}
            if aliases.get(think, think) not in efforts:
                return router_error("INVALID_THINK_VALUE", 400, f"{think} is not supported")
        if state.ollama_generate_responses:
            return copy.deepcopy(state.ollama_generate_responses.pop(0))
        images = (payload.get("messages") or [{}])[0].get("images")
        declining = served["service"] in state.router_declining_services
        if images:
            # Mirrors the router: image input requires an advertised vision profile.
            if "image" not in served["input_modalities"]:
                return JSONResponse(
                    {"error": {"code": "UNSUPPORTED_PROFILE_CAPABILITY"}}, status_code=400
                )
            if declining:
                return router_error(
                    "MALFORMED_STRUCTURED_OUTPUT", 502, "output does not match the schema"
                )
            state.ollama_vision_calls.append(copy.deepcopy(payload))
            count = int(payload["format"]["properties"]["results"]["minItems"])
            content = str(payload["messages"][0].get("content", ""))
            listed = content.rsplit("Expectations:\n", 1)[-1].splitlines()
            expectations = [line.split(". ", 1)[-1].lower() for line in listed[:count]]
            # Browser journeys cannot reach this state, so marker phrases script the
            # reviewer: "needs a second look" fails the first review of an expectation set and
            # "never passes" always fails. Explicit queued scores take precedence.
            seen_key = "\n".join(expectations)
            seen = state.ollama_vision_seen.get(seen_key, 0) + 1
            state.ollama_vision_seen[seen_key] = seen
            scores = state.ollama_vision_scores.pop(0) if state.ollama_vision_scores else []

            def scripted(index: int) -> int:
                if index < len(scores):
                    return scores[index]
                text = expectations[index] if index < len(expectations) else ""
                if "never passes" in text:
                    return 30
                if "needs a second look" in text and seen == 1:
                    return 35
                return 100

            results = [
                {
                    "expectation": index + 1,
                    "score": scripted(index),
                    "observation": f"Observed expectation {index + 1}.",
                }
                for index in range(count)
            ]
            effective_model = state.ollama_effective_model or canonical
            return {
                "model": str(effective_model),
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"results": results, "summary": "Fake vision review."}),
                },
                "done": True,
                "done_reason": "stop",
            }
        if state.ollama_thinking_overflows and payload.get("think") is not False:
            # Thinking responses outgrow every token allowance; the
            # no-thinking pass still completes within the base allowance.
            effective_model = state.ollama_effective_model or canonical
            return {
                "model": str(effective_model or ""),
                "message": {
                    "role": "assistant",
                    "content": "",
                    "thinking": "Partial reasoning that outgrows the token allowance.",
                },
                "done": True,
                "done_reason": "length",
                # As the router reports a reached output limit: not a completed answer.
                "x_router": {"status": "incomplete", "stop_reason": "max_output_tokens"},
            }
        instruction = str(payload["messages"][0]["content"])
        current = ""
        if "Current prompt:\n" in instruction:
            current = (
                instruction.split("Current prompt:\n", 1)[1]
                .split("\n\nCreative direction:", 1)[0]
                .strip()
            )
            direction = instruction.split("Creative direction:\n", 1)[-1].strip()
        else:
            direction = instruction.rsplit("\n\n", 1)[-1].strip()
        if declining:
            composed = "I'm sorry, but I can't help with that request."
        elif state.ollama_response_prompts:
            composed = state.ollama_response_prompts.pop(0)
        elif state.ollama_response_prompt is not None:
            composed = state.ollama_response_prompt
        elif current and direction:
            composed = f"{current}, {direction}"
        elif current:
            composed = current
        elif direction:
            # Create mode: a healthy model expands the direction; the default fake
            # models that expansion instead of echoing the direction verbatim.
            composed = (
                f"{direction}, detailed photographic rendering, soft natural light, "
                "shallow depth of field, high detail"
            )
        else:
            composed = "composed image prompt"
        effective_model = state.ollama_effective_model or canonical
        response_text = json.dumps({"prompt": composed})
        thinking_text = ""
        if state.ollama_include_thinking and payload.get("think") is not False:
            thinking_text = (
                response_text
                if state.ollama_response_in_thinking
                else "Analyzed the prompt composition requirements."
            )
        result = {
            "model": str(effective_model or ""),
            "message": {
                "role": "assistant",
                "content": "" if state.ollama_response_in_thinking else response_text,
            },
            "done": True,
            "done_reason": "stop",
        }
        if state.ollama_include_thinking:
            result["message"]["thinking"] = thinking_text
        return result

    @app.post("/v1/audio/transcriptions")
    async def speech_to_text(
        request: Request,
        file: UploadFile = File(...),
        model: str = Form(...),
        response_format: str = Form(...),
    ) -> dict[str, str]:
        if not state.speech_to_text_available:
            raise HTTPException(status_code=503)
        content = await file.read()
        state.speech_to_text_calls.append(
            {
                "authorization": request.headers.get("authorization"),
                "filename": file.filename,
                "content_type": file.content_type,
                "content": content,
                "model": model,
                "response_format": response_format,
            }
        )
        return {"text": state.speech_to_text_result}

    return app


class LiveFakeServer:
    def __init__(self, state: FakeServiceState | None = None) -> None:
        self.state = state or FakeServiceState()
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.ws_url = f"ws://127.0.0.1:{self.port}/ws"
        self._server = uvicorn.Server(
            uvicorn.Config(
                create_fake_services_app(self.state),
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                access_log=False,
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> LiveFakeServer:
        self._thread.start()
        deadline = time.monotonic() + 5
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("fake service server failed to start")
            if time.monotonic() > deadline:
                raise RuntimeError("fake service server did not become ready")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)

    def __enter__(self) -> LiveFakeServer:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
