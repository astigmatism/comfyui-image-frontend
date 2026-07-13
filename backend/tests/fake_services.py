from __future__ import annotations

import asyncio
import copy
import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from urllib.parse import unquote

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from PIL import Image, ImageDraw

from app.domain.contract import extract_manifest, normalized_ui_hash, sha256_json


def object_info_fixture() -> dict[str, Any]:
    return {
        "PromptNode": {
            "input": {"required": {"text": ["STRING", {"multiline": True}]}}
        },
        "SeedNode": {"input": {"required": {"seed": ["INT", {"min": 0}]}}},
        "NumberNode": {
            "input": {"required": {"steps": ["INT", {"min": 1, "max": 50}]}}
        },
        "ImageNode": {
            "input": {
                "required": {
                    "prompt": ["STRING"],
                    "seed": ["INT"],
                    "width": ["INT"],
                    "height": ["INT"],
                    "enabled": ["BOOLEAN"],
                }
            }
        },
        "LoadImage": {
            "input": {"required": {"image": ["STRING"]}}
        },
        "ModelLoader": {
            "input": {
                "required": {
                    "model_name": [["models/fake.safetensors", "models/other.safetensors"]]
                }
            }
        },
    }


def _selector(node_id: str, class_type: str, title: str, *expected: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "class_type": class_type,
        "title": title,
        "expected_inputs": list(expected),
    }


def build_valid_workflow_pair(
    *,
    workflow_id: str = "fake-progressive-v1",
    version: str = "1.0.0",
    display_name: str = "Fake Progressive Workflow",
) -> tuple[dict[str, Any], dict[str, Any]]:
    graph: dict[str, Any] = {
        "1": {
            "class_type": "PromptNode",
            "inputs": {"text": ""},
            "_meta": {"title": "Prompt Input"},
        },
        "2": {
            "class_type": "SeedNode",
            "inputs": {"seed": 0},
            "_meta": {"title": "Generation Seed"},
        },
        "3": {
            "class_type": "NumberNode",
            "inputs": {"steps": 8},
            "_meta": {"title": "Sampling Steps"},
        },
        "4": {
            "class_type": "ImageNode",
            "inputs": {
                "prompt": ["1", 0],
                "seed": ["2", 0],
                "width": 512,
                "height": 512,
                "enabled": True,
            },
            "_meta": {"title": "Base Artifact"},
        },
        "5": {
            "class_type": "ImageNode",
            "inputs": {
                "prompt": ["1", 0],
                "seed": ["2", 0],
                "width": 512,
                "height": 512,
                "enabled": True,
            },
            "_meta": {"title": "Final Artifact"},
        },
        "6": {
            "class_type": "LoadImage",
            "inputs": {"image": ""},
            "_meta": {"title": "Reference Image"},
        },
        "7": {
            "class_type": "ModelLoader",
            "inputs": {"model_name": "models/fake.safetensors"},
            "_meta": {"title": "Approved Model"},
        },
    }
    api_document = copy.deepcopy(graph)
    manifest: dict[str, Any] = {
        "kind": "comfyui.frontend.workflow-contract",
        "contract_schema_version": "1.1.0",
        "workflow": {
            "id": workflow_id,
            "display_name": display_name,
            "version": version,
            "description": "Deterministic progressive integration fixture.",
            "ui_graph_sha256": "0" * 64,
            "api_graph_sha256": sha256_json(api_document),
            "adapter_version": "1.0.0",
        },
        "presentation": {"groups": [{"id": "sampling", "label": "Sampling"}]},
        "requirements": {
            "node_classes": [
                {"class_type": name, "required": True}
                for name in ("PromptNode", "SeedNode", "NumberNode", "ImageNode", "LoadImage", "ModelLoader")
            ],
            "assets": [
                {
                    "id": "fake-model",
                    "kind": "checkpoint",
                    "path": "models/fake.safetensors",
                    "required": True,
                }
            ],
            "runtime": {"features": []},
        },
        "controls": [
            {
                "id": "prompt.text",
                "label": "Prompt",
                "description": "The exact prompt submitted to the workflow.",
                "group": "prompt",
                "order": 10,
                "type": "multiline_string",
                "default": "",
                "required": True,
                "tier": "basic",
                "constraints": {"maximum_length": 4000},
                "bindings": [
                    {
                        "strategy": "patch_input",
                        "selector": _selector("1", "PromptNode", "Prompt Input", "text"),
                        "input": "text",
                    }
                ],
            },
            {
                "id": "generation.seed",
                "label": "Seed",
                "group": "sampling",
                "order": 20,
                "type": "seed",
                "default": "random",
                "tier": "basic",
                "constraints": {"minimum": 0, "maximum": 2**31 - 1},
                "bindings": [
                    {
                        "strategy": "patch_input",
                        "selector": _selector("2", "SeedNode", "Generation Seed", "seed"),
                        "input": "seed",
                    }
                ],
            },
            {
                "id": "size.resolution",
                "label": "Resolution",
                "group": "size",
                "order": 30,
                "type": "resolution",
                "default": {"width": 512, "height": 512},
                "required": True,
                "tier": "basic",
                "constraints": {
                    "minimum_width": 64,
                    "maximum_width": 2048,
                    "minimum_height": 64,
                    "maximum_height": 2048,
                    "multiple": 8,
                },
                "bindings": [
                    {
                        "strategy": "derive",
                        "targets": [
                            {
                                "component": "width",
                                "selector": _selector("4", "ImageNode", "Base Artifact", "width"),
                                "input": "width",
                            },
                            {
                                "component": "height",
                                "selector": _selector("4", "ImageNode", "Base Artifact", "height"),
                                "input": "height",
                            },
                            {
                                "component": "width",
                                "selector": _selector("5", "ImageNode", "Final Artifact", "width"),
                                "input": "width",
                            },
                            {
                                "component": "height",
                                "selector": _selector("5", "ImageNode", "Final Artifact", "height"),
                                "input": "height",
                            },
                        ],
                    }
                ],
            },
            {
                "id": "post.enabled",
                "label": "Final processing",
                "group": "post",
                "order": 40,
                "type": "boolean",
                "default": True,
                "tier": "basic",
                "bindings": [{"strategy": "select_branch", "branch_id": "postprocess"}],
            },
            {
                "id": "sampling.steps",
                "label": "Steps",
                "group": "sampling",
                "order": 50,
                "type": "integer",
                "default": 8,
                "tier": "advanced",
                "constraints": {"minimum": 1, "maximum": 50, "step": 1},
                "bindings": [
                    {
                        "strategy": "patch_input",
                        "selector": _selector("3", "NumberNode", "Sampling Steps", "steps"),
                        "input": "steps",
                    }
                ],
            },
            {
                "id": "source.image",
                "label": "Reference image",
                "group": "reference",
                "order": 60,
                "type": "image_upload",
                "default": None,
                "required": False,
                "tier": "advanced",
                "bindings": [
                    {
                        "strategy": "upload_then_patch",
                        "upload_kind": "image",
                        "selector": _selector("6", "LoadImage", "Reference Image", "image"),
                        "input": "image",
                    }
                ],
            },
            {
                "id": "model.asset",
                "label": "Model",
                "group": "sampling",
                "order": 70,
                "type": "asset_selector",
                "default": "models/fake.safetensors",
                "tier": "advanced",
                "options": {"values": ["models/fake.safetensors"]},
                "bindings": [
                    {
                        "strategy": "patch_input",
                        "selector": _selector("7", "ModelLoader", "Approved Model", "model_name"),
                        "input": "model_name",
                    }
                ],
            },
        ],
        "branches": [
            {
                "id": "postprocess",
                "label": "Final processing",
                "default_enabled": True,
                "strategy": "graph_transform",
                "transforms": {
                    "enable": [
                        {
                            "op": "set_input",
                            "selector": _selector("5", "ImageNode", "Final Artifact", "enabled"),
                            "input": "enabled",
                            "value": True,
                        }
                    ],
                    "disable": [
                        {
                            "op": "set_input",
                            "selector": _selector("5", "ImageNode", "Final Artifact", "enabled"),
                            "input": "enabled",
                            "value": False,
                        }
                    ],
                },
                "invariants": ["base_output_remains_available"],
            }
        ],
        "stages": [
            {
                "id": "base_generation",
                "label": "Creating base image",
                "sequence": 30,
                "node_selectors": [_selector("4", "ImageNode", "Base Artifact")],
                "emits_output_ids": ["base_image"],
                "cancellable_after_emission": True,
            },
            {
                "id": "final_processing",
                "label": "Finishing image",
                "sequence": 90,
                "node_selectors": [_selector("5", "ImageNode", "Final Artifact")],
                "emits_output_ids": ["final_image"],
                "terminal": True,
            },
        ],
        "outputs": [
            {
                "id": "base_image",
                "role": "image.base",
                "kind": "image",
                "selector": _selector("4", "ImageNode", "Base Artifact"),
                "history_field": "images",
                "availability": "on_node_execution",
                "temporary": True,
                "durable": False,
                "canonical_on_success": False,
                "usable_on_cancel": True,
                "usable_on_failure": True,
                "persist_on_cancel": True,
                "progression": {"sequence": 30, "quality_tier": "base"},
                "presentation": {"auto_render": True, "label": "Base image"},
                "batch_semantics": "one_per_batch_item",
            },
            {
                "id": "final_image",
                "role": "image.final",
                "kind": "image",
                "selector": _selector("5", "ImageNode", "Final Artifact"),
                "history_field": "images",
                "availability": "on_node_execution",
                "durable": True,
                "temporary": False,
                "canonical_on_success": True,
                "usable_on_cancel": False,
                "usable_on_failure": False,
                "progression": {
                    "sequence": 90,
                    "quality_tier": "final",
                    "supersedes": ["base_image"],
                },
                "presentation": {"auto_render": True, "label": "Final image"},
                "batch_semantics": "one_per_batch_item",
            },
        ],
        "progression": {
            "enabled": True,
            "ordered_output_ids": ["base_image", "final_image"],
            "continue_automatically": True,
            "terminal_output_id": "final_image",
            "on_cancel": {
                "retain_available_outputs": True,
                "best_available_strategy": "highest_sequence_usable_on_cancel",
                "promote_to_canonical": False,
            },
            "on_failure": {
                "retain_available_outputs": True,
                "best_available_strategy": "highest_sequence_usable_on_failure",
            },
        },
        "presets": [
            {
                "id": "quick",
                "label": "Quick",
                "values": {"sampling.steps": 4, "size.resolution": {"width": 384, "height": 384}},
            }
        ],
        "policies": {"maximum_initial_pixels": 4_194_304},
        "extensions": {},
    }
    ui_document: dict[str, Any] = {
        "last_node_id": 100,
        "last_link_id": 0,
        "nodes": [
            {
                "id": 100,
                "type": "FrontendWorkflowContract",
                "pos": [0, 0],
                "size": [480, 320],
                "flags": {},
                "order": 0,
                "mode": 0,
                "properties": {"manifest": manifest},
            }
        ],
        "links": [],
        "groups": [],
        "config": {},
        "extra": {},
        "version": 0.4,
    }
    _, location = extract_manifest(ui_document)
    manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(ui_document, location)
    return ui_document, api_document


def build_workflow_files() -> dict[str, dict[str, Any]]:
    valid_ui, valid_api = build_valid_workflow_pair()

    mismatch_ui = copy.deepcopy(valid_ui)
    mismatch_manifest = mismatch_ui["nodes"][0]["properties"]["manifest"]
    mismatch_manifest["workflow"]["id"] = "hash-mismatch"
    mismatch_manifest["workflow"]["display_name"] = "Hash Mismatch"
    mismatch_manifest["workflow"]["api_graph_sha256"] = "f" * 64
    # Keep the UI hash valid after changing the embedded manifest so rejection is specifically API hash.
    _, mismatch_location = extract_manifest(mismatch_ui)
    mismatch_manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(
        mismatch_ui, mismatch_location
    )

    invalid_binding_ui = copy.deepcopy(valid_ui)
    invalid_binding_manifest = invalid_binding_ui["nodes"][0]["properties"]["manifest"]
    _, invalid_location = extract_manifest(invalid_binding_ui)
    invalid_binding_manifest["workflow"]["id"] = "invalid-binding"
    invalid_binding_manifest["workflow"]["display_name"] = "Invalid Binding"
    invalid_binding_manifest["controls"][0]["bindings"][0]["input"] = "missing_input"
    invalid_binding_manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(
        invalid_binding_ui, invalid_location
    )

    missing_dependency_ui = copy.deepcopy(valid_ui)
    missing_manifest = missing_dependency_ui["nodes"][0]["properties"]["manifest"]
    _, missing_location = extract_manifest(missing_dependency_ui)
    missing_manifest["workflow"]["id"] = "missing-dependency"
    missing_manifest["workflow"]["display_name"] = "Missing Dependency"
    missing_manifest["requirements"]["node_classes"].append(
        {"class_type": "DefinitelyMissingNode", "required": True}
    )
    missing_manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(
        missing_dependency_ui, missing_location
    )

    return {
        "profiles/progressive.workflow.json": valid_ui,
        "profiles/progressive.api.json": valid_api,
        "profiles/incomplete.workflow.json": copy.deepcopy(valid_ui),
        "profiles/hash-mismatch.workflow.json": mismatch_ui,
        "profiles/hash-mismatch.api.json": copy.deepcopy(valid_api),
        "profiles/invalid-binding.workflow.json": invalid_binding_ui,
        "profiles/invalid-binding.api.json": copy.deepcopy(valid_api),
        "profiles/missing-dependency.workflow.json": missing_dependency_ui,
        "profiles/missing-dependency.api.json": copy.deepcopy(valid_api),
    }


def make_png(label: str, *, width: int = 96, height: int = 72) -> bytes:
    image = Image.new("RGB", (width, height), (24, 38, 56))
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 3, width - 4, height - 4), outline=(96, 148, 210), width=2)
    draw.text((8, height // 2 - 5), label[:14], fill=(235, 242, 250))
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


@dataclass
class FakeServiceState:
    workflow_files: dict[str, dict[str, Any]] = field(default_factory=build_workflow_files)
    object_info: dict[str, Any] = field(default_factory=object_info_fixture)
    service_available: bool = True
    ollama_available: bool = True
    reject_prompts: bool = False
    fail_retrieval: bool = False
    disconnect_websocket: bool = False
    default_stage_delay: float = 0.08
    models: list[str] = field(default_factory=lambda: ["zeta:latest", "alpha:latest"])
    histories: dict[str, dict[str, Any]] = field(default_factory=dict)
    prompts: dict[str, dict[str, Any]] = field(default_factory=dict)
    running_prompt_ids: set[str] = field(default_factory=set)
    queued_prompt_ids: set[str] = field(default_factory=set)
    cancelled_prompt_ids: set[str] = field(default_factory=set)
    output_files: dict[tuple[str, str, str], bytes] = field(default_factory=dict)
    event_log: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    websocket_clients: dict[str, list[WebSocket]] = field(default_factory=dict)
    submitted: list[dict[str, Any]] = field(default_factory=list)
    ollama_calls: list[dict[str, Any]] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    _lock: asyncio.Lock | None = None

    def reset_runtime(self) -> None:
        self.service_available = True
        self.ollama_available = True
        self.reject_prompts = False
        self.fail_retrieval = False
        self.disconnect_websocket = False
        self.histories.clear()
        self.prompts.clear()
        self.running_prompt_ids.clear()
        self.queued_prompt_ids.clear()
        self.cancelled_prompt_ids.clear()
        self.output_files.clear()
        self.event_log.clear()
        self.websocket_clients.clear()
        self.submitted.clear()
        self.ollama_calls.clear()
        self.uploaded.clear()

    async def emit(self, client_id: str, event: dict[str, Any]) -> None:
        self.event_log.setdefault(client_id, []).append(copy.deepcopy(event))
        clients = list(self.websocket_clients.get(client_id, []))
        for websocket in clients:
            try:
                await websocket.send_json(event)
            except Exception:
                try:
                    self.websocket_clients[client_id].remove(websocket)
                except (KeyError, ValueError):
                    pass

    async def execute_prompt(self, prompt_id: str) -> None:
        record = self.prompts[prompt_id]
        client_id = record["client_id"]
        graph = record["graph"]
        prompt_text = str(graph.get("1", {}).get("inputs", {}).get("text", ""))
        delay = 0.5 if "slow" in prompt_text.casefold() else self.default_stage_delay
        self.queued_prompt_ids.discard(prompt_id)
        self.running_prompt_ids.add(prompt_id)
        self.histories[prompt_id] = {
            "status": {"status_str": "running", "completed": False},
            "outputs": {},
        }
        await asyncio.sleep(0.02)
        await self.emit(
            client_id,
            {"type": "executing", "data": {"prompt_id": prompt_id, "node": "4"}},
        )
        await self.emit(
            client_id,
            {
                "type": "progress",
                "data": {"prompt_id": prompt_id, "node": "4", "value": 1, "max": 2},
            },
        )
        base_name = f"{prompt_id}-base.png"
        base_ref = {"filename": base_name, "subfolder": "fake", "type": "output"}
        self.output_files[(base_name, "fake", "output")] = make_png("base")
        base_output = {"images": [base_ref]}
        self.histories[prompt_id]["outputs"]["4"] = base_output
        await self.emit(
            client_id,
            {
                "type": "executed",
                "data": {
                    "prompt_id": prompt_id,
                    "node": "4",
                    "output": copy.deepcopy(base_output),
                },
            },
        )
        await asyncio.sleep(delay)

        cancel_requested = prompt_id in self.cancelled_prompt_ids
        race_success = "race-success" in prompt_text.casefold()
        if cancel_requested and not race_success:
            self.histories[prompt_id]["status"] = {
                "status_str": "cancelled",
                "completed": True,
            }
            await self.emit(
                client_id,
                {
                    "type": "execution_interrupted",
                    "data": {"prompt_id": prompt_id},
                },
            )
            self.running_prompt_ids.discard(prompt_id)
            return

        if "fail" in prompt_text.casefold():
            self.histories[prompt_id]["status"] = {
                "status_str": "error",
                "completed": True,
            }
            await self.emit(
                client_id,
                {
                    "type": "execution_error",
                    "data": {
                        "prompt_id": prompt_id,
                        "node_id": "5",
                        "node_type": "ImageNode",
                        "exception_type": "FakeFailure",
                    },
                },
            )
            self.running_prompt_ids.discard(prompt_id)
            return

        await self.emit(
            client_id,
            {"type": "executing", "data": {"prompt_id": prompt_id, "node": "5"}},
        )
        final_refs: list[dict[str, str]] = []
        count = 2 if "multi" in prompt_text.casefold() else 1
        for index in range(count):
            name = f"{prompt_id}-final-{index}.png"
            ref = {"filename": name, "subfolder": "fake", "type": "output"}
            final_refs.append(ref)
            self.output_files[(name, "fake", "output")] = make_png(f"final {index + 1}")
        final_output = {"images": final_refs}
        self.histories[prompt_id]["outputs"]["5"] = final_output
        await self.emit(
            client_id,
            {
                "type": "executed",
                "data": {
                    "prompt_id": prompt_id,
                    "node": "5",
                    "output": copy.deepcopy(final_output),
                },
            },
        )
        self.histories[prompt_id]["status"] = {
            "status_str": "success",
            "completed": True,
        }
        await self.emit(
            client_id,
            {"type": "execution_success", "data": {"prompt_id": prompt_id}},
        )
        self.running_prompt_ids.discard(prompt_id)


def create_fake_services_app(state: FakeServiceState) -> FastAPI:
    app = FastAPI(title="Deterministic fake ComfyUI and Ollama")

    @app.middleware("http")
    async def preserve_userdata_route_segment(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Uvicorn exposes a decoded scope path, while ComfyUI matches /userdata/{file}
        # against the raw route segment. Preserve that behavior for this test double:
        # encoded separators stay inside one segment and literal separators do not match.
        prefix = b"/userdata/"
        raw_path = request.scope.get("raw_path", b"")
        if isinstance(raw_path, bytes) and raw_path.startswith(prefix):
            raw_segment = raw_path[len(prefix) :]
            if b"/" in raw_segment:
                return Response(status_code=404)
            try:
                request.scope["path"] = f"/userdata/{raw_segment.decode('ascii')}"
            except UnicodeDecodeError:
                return Response(status_code=404)
        return await call_next(request)

    def require_comfy() -> None:
        if not state.service_available:
            raise HTTPException(status_code=503, detail="fake ComfyUI unavailable")

    @app.get("/object_info")
    async def object_info() -> dict[str, Any]:
        require_comfy()
        return state.object_info

    @app.get("/system_stats")
    async def system_stats() -> dict[str, Any]:
        require_comfy()
        return {"system": {"comfyui_version": "fake-1.0"}, "devices": [{"name": "fake-gpu"}]}

    @app.get("/userdata")
    async def userdata(request: Request) -> Any:
        require_comfy()
        path = request.query_params.get("path")
        if path:
            key = _strip_workflow_root(path)
            if key not in state.workflow_files:
                raise HTTPException(status_code=404)
            return state.workflow_files[key]
        directory = request.query_params.get("dir", "")
        prefix = _strip_workflow_root(directory)
        files = []
        for key in sorted(state.workflow_files):
            if not prefix or key.startswith(prefix.rstrip("/") + "/") or key == prefix:
                files.append(f"workflows/front-end/{key}")
        return {"files": files}

    @app.get("/userdata/{file}")
    async def userdata_path(file: str) -> dict[str, Any]:
        require_comfy()
        key = _strip_workflow_root(unquote(file))
        if key not in state.workflow_files:
            raise HTTPException(status_code=404)
        return state.workflow_files[key]

    @app.post("/prompt")
    async def submit_prompt(request: Request) -> dict[str, Any]:
        require_comfy()
        if state.reject_prompts:
            raise HTTPException(status_code=400, detail="prompt rejected")
        payload = await request.json()
        graph = payload.get("prompt")
        client_id = payload.get("client_id")
        if not isinstance(graph, dict) or not isinstance(client_id, str):
            raise HTTPException(status_code=422)
        prompt_id = str(uuid.uuid4())
        state.prompts[prompt_id] = {"graph": copy.deepcopy(graph), "client_id": client_id}
        state.queued_prompt_ids.add(prompt_id)
        state.submitted.append(
            {
                "prompt_id": prompt_id,
                "client_id": client_id,
                "prompt": str(graph.get("1", {}).get("inputs", {}).get("text", "")),
            }
        )
        asyncio.create_task(state.execute_prompt(prompt_id), name=f"fake-prompt-{prompt_id}")
        return {"prompt_id": prompt_id, "number": len(state.submitted)}

    @app.websocket("/ws")
    async def websocket_events(websocket: WebSocket) -> None:
        client_id = websocket.query_params.get("clientId") or ""
        await websocket.accept()
        if state.disconnect_websocket:
            await websocket.close(code=1011)
            return
        state.websocket_clients.setdefault(client_id, []).append(websocket)
        for event in state.event_log.get(client_id, []):
            await websocket.send_json(event)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            try:
                state.websocket_clients.get(client_id, []).remove(websocket)
            except ValueError:
                pass

    @app.get("/history/{prompt_id}")
    async def history(prompt_id: str) -> dict[str, Any]:
        require_comfy()
        if prompt_id not in state.histories:
            raise HTTPException(status_code=404)
        return {prompt_id: state.histories[prompt_id]}

    @app.get("/queue")
    async def queue_state() -> dict[str, Any]:
        require_comfy()
        return {
            "queue_running": [[0, prompt_id, state.prompts.get(prompt_id, {}).get("graph", {})] for prompt_id in sorted(state.running_prompt_ids)],
            "queue_pending": [[0, prompt_id, state.prompts.get(prompt_id, {}).get("graph", {})] for prompt_id in sorted(state.queued_prompt_ids)],
        }

    @app.post("/queue")
    async def delete_queued(request: Request) -> dict[str, Any]:
        require_comfy()
        payload = await request.json()
        for prompt_id in payload.get("delete", []):
            state.queued_prompt_ids.discard(str(prompt_id))
            state.cancelled_prompt_ids.add(str(prompt_id))
        return {"ok": True}

    @app.post("/interrupt")
    async def interrupt() -> Response:
        require_comfy()
        state.cancelled_prompt_ids.update(state.running_prompt_ids)
        return Response(status_code=204)

    @app.post("/upload/image")
    async def upload_image(image: UploadFile = File(...)) -> dict[str, str]:
        require_comfy()
        content = await image.read()
        if not content:
            raise HTTPException(status_code=400)
        name = image.filename or f"upload-{uuid.uuid4()}.png"
        state.uploaded.append(name)
        state.output_files[(name, "", "input")] = content
        return {"name": name, "subfolder": "", "type": "input"}

    @app.get("/view")
    async def view(filename: str, subfolder: str = "", type: str = "output") -> Response:
        require_comfy()
        if state.fail_retrieval:
            raise HTTPException(status_code=500, detail="retrieval failed")
        key = (filename, subfolder, type)
        if key not in state.output_files:
            raise HTTPException(status_code=404)
        return Response(state.output_files[key], media_type="image/png")

    @app.get("/api/tags")
    async def ollama_tags() -> dict[str, Any]:
        if not state.ollama_available:
            raise HTTPException(status_code=503)
        return {"models": [{"name": name} for name in state.models]}

    @app.post("/api/generate")
    async def ollama_generate(request: Request) -> dict[str, Any]:
        if not state.ollama_available:
            raise HTTPException(status_code=503)
        payload = await request.json()
        state.ollama_calls.append(copy.deepcopy(payload))
        instruction = str(payload.get("prompt", ""))
        direction = instruction.split("Creative direction:\n", 1)[-1].strip()
        current = ""
        if "Current prompt:\n" in instruction:
            current = instruction.split("Current prompt:\n", 1)[1].split("\n\nCreative direction:", 1)[0].strip()
        composed = f"{current}, {direction}".strip(" ,") or "composed image prompt"
        return {"response": json.dumps({"prompt": composed}), "done": True}

    return app


def _strip_workflow_root(value: str) -> str:
    normalized = str(value).replace("\\", "/").strip("/")
    root = "workflows/front-end"
    if normalized == root:
        return ""
    if normalized.startswith(root + "/"):
        return normalized[len(root) + 1 :]
    return normalized


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
