"""Authenticated ComfyUI HTTP boundary for LoRA publication operations."""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import folder_paths
from aiohttp import web
from server import PromptServer

from .lora_management import DEFAULT_MAX_UPLOAD, MAX_JSON, LoraManagement, ManagementError

ROUTE = "/cif/lora-management"
PUBLISHER_ROUTE = "/cif/publisher/lock"


def _require_browser_publisher(request: web.Request) -> None:
    # Save & Publish runs in ComfyUI's browser editor and cannot hold the
    # backend-only management secret. Its lease route must at least reject
    # cross-origin browser requests; ComfyUI's own userdata write endpoints
    # remain the authority for who may publish a workflow.
    if request.headers.get("X-CIF-Publisher-Lease") != "1":
        raise ManagementError("Publisher lease request is invalid", 403)
    if request.headers.get("Sec-Fetch-Site") != "same-origin":
        raise ManagementError("Publisher lease requires a same-origin browser request", 403)
    origin = request.headers.get("Origin", "")
    try:
        parsed = urlsplit(origin)
        host = urlsplit("//" + request.host)
        same_host = parsed.hostname == host.hostname and parsed.port == host.port
    except ValueError as exc:
        raise ManagementError("Publisher lease origin is invalid", 403) from exc
    if (
        parsed.scheme not in ("http", "https")
        or not same_host
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ManagementError("Publisher lease origin is invalid", 403)


def _service(request: web.Request, *, publisher: bool = False) -> LoraManagement:
    secret = os.environ.get("CIF_LORA_MANAGEMENT_SECRET", "")
    if len(secret) < 32:
        raise ManagementError("LoRA management is disabled", 404)
    if publisher:
        _require_browser_publisher(request)
    else:
        supplied = request.headers.get("X-CIF-Management-Token", "")
        if not hmac.compare_digest(secret, supplied):
            raise ManagementError("Management authentication failed", 403)
    user = os.environ.get("CIF_LORA_MANAGEMENT_USER", "default")
    if request.headers.get("Comfy-User", "default") != user:
        raise ManagementError("Management user namespace differs", 403)
    root = os.environ.get("CIF_LORA_MANAGEMENT_ROOT", "")
    if not root or not Path(root).is_absolute():
        raise ManagementError("LoRA model root is not configured", 503)
    allowed = [Path(path).resolve() for path in folder_paths.get_folder_paths("loras")]
    model_root = Path(root).resolve()
    if model_root not in allowed:
        raise ManagementError("LoRA model root is not a ComfyUI LoRA folder", 503)
    try:
        limit = int(os.environ.get("CIF_LORA_MANAGEMENT_MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD)))
    except ValueError as exc:
        raise ManagementError("Invalid upload limit", 503) from exc
    if limit <= 0:
        raise ManagementError("Invalid upload limit", 503)
    try:
        return LoraManagement(
            Path(folder_paths.get_user_directory()) / user,
            model_root,
            model_writer=(
                os.environ.get("CIF_LORA_MANAGEMENT_MODEL_WRITER") == "1"
                and os.access(model_root, os.W_OK)
            ),
            max_upload=limit,
            queue=getattr(PromptServer.instance, "prompt_queue", None),
        )
    except ManagementError:
        raise


async def _body(request: web.Request) -> dict:
    if request.content_length is not None and request.content_length > MAX_JSON:
        raise ManagementError("Management request is too large", 413)
    raw = await request.content.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ManagementError("Management request is too large", 413)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ManagementError("Invalid JSON", 400) from exc
    if not isinstance(value, dict):
        raise ManagementError("JSON object required", 400)
    return value


def _error(exc: ManagementError) -> web.Response:
    return web.json_response({"code": exc.code, "message": str(exc)}, status=exc.status)


@PromptServer.instance.routes.post(f"{PUBLISHER_ROUTE}/acquire")
async def acquire_publisher_lock(request: web.Request) -> web.Response:
    try:
        service = _service(request, publisher=True)
        body = await _body(request)
        return web.json_response(service.acquire_publisher_lock(body.get("source_path")))
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.post(f"{PUBLISHER_ROUTE}/renew")
async def renew_publisher_lock(request: web.Request) -> web.Response:
    try:
        service = _service(request, publisher=True)
        body = await _body(request)
        return web.json_response(
            service.renew_publisher_lock(body.get("source_path"), body.get("token"))
        )
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.post(f"{PUBLISHER_ROUTE}/release")
async def release_publisher_lock(request: web.Request) -> web.Response:
    try:
        service = _service(request, publisher=True)
        body = await _body(request)
        return web.json_response(
            service.release_publisher_lock(body.get("source_path"), body.get("token"))
        )
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.get(f"{ROUTE}/capabilities")
async def capabilities(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        return web.json_response(
            {
                "enabled": True,
                "version": 1,
                "max_upload_bytes": service.max_upload,
                "model_writer": service.model_writer,
                "model_root_id": service.model_root_id,
            }
        )
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.get(f"{ROUTE}/bundle")
async def bundle(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        return web.json_response(service.bundle(request.query.get("source_path")))
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.put(f"{ROUTE}/operations/{{operation_id}}/file")
async def stage(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        result = await service.stage(
            request.match_info["operation_id"],
            request.content,
            content_length=request.content_length,
            filename=request.headers.get("X-CIF-Filename", ""),
        )
        return web.json_response(result)
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.post(f"{ROUTE}/operations/{{operation_id}}/prepare")
async def prepare(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        payload = await _body(request)
        return web.json_response(service.prepare(request.match_info["operation_id"], payload))
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.get(f"{ROUTE}/operations/{{operation_id}}/candidate")
async def candidate(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        return web.json_response(service.candidate(request.match_info["operation_id"]))
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.get(f"{ROUTE}/operations/{{operation_id}}")
async def status(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        return web.json_response(service.status(request.match_info["operation_id"]))
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.post(f"{ROUTE}/operations/{{operation_id}}/commit")
async def commit(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        return web.json_response(service.commit(request.match_info["operation_id"]))
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.post(f"{ROUTE}/operations/{{operation_id}}/quarantine")
async def quarantine(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        return web.json_response(service.quarantine(request.match_info["operation_id"]))
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.post(f"{ROUTE}/operations/{{operation_id}}/finalize")
async def finalize(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        return web.json_response(service.finalize(request.match_info["operation_id"]))
    except ManagementError as exc:
        return _error(exc)


@PromptServer.instance.routes.post(f"{ROUTE}/operations/{{operation_id}}/rollback")
async def rollback(request: web.Request) -> web.Response:
    try:
        service = _service(request)
        return web.json_response(service.rollback(request.match_info["operation_id"]))
    except ManagementError as exc:
        return _error(exc)
