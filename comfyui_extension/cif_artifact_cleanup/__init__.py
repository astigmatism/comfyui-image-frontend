"""ComfyUI companion route for frontend-authoritative artifact ownership.

Install this directory in each ComfyUI runtime's ``custom_nodes`` directory. The frontend calls
the route only after every referenced file has been copied into its own durable storage.
"""

from __future__ import annotations

import json
import os
from pathlib import PurePosixPath
from typing import Any

import folder_paths
from aiohttp import web
from server import PromptServer

_MAX_REQUEST_BYTES = 512 * 1024
_MAX_ARTIFACTS = 4096
_ROUTE = "/comfyui-image-frontend/artifacts/delete"


def _safe_locator(value: Any) -> tuple[str, str, str] | None:
    if not isinstance(value, dict):
        return None
    filename = value.get("filename")
    subfolder = value.get("subfolder", "")
    storage_type = value.get("type", "output")
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > 500
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or PurePosixPath(filename).name != filename
        or not isinstance(subfolder, str)
        or len(subfolder) > 500
        or subfolder.startswith("/")
        or "\\" in subfolder
        or "//" in subfolder
        or any(part in {".", ".."} for part in PurePosixPath(subfolder).parts)
        or (subfolder and str(PurePosixPath(subfolder)) != subfolder)
        or storage_type not in {"output", "temp"}
    ):
        return None
    return filename, subfolder, storage_type


def _storage_root(storage_type: str) -> str:
    if storage_type == "output":
        return folder_paths.get_output_directory()
    return folder_paths.get_temp_directory()


def _target_path(filename: str, subfolder: str, storage_type: str) -> tuple[str, str] | None:
    root = os.path.realpath(_storage_root(storage_type))
    target = os.path.realpath(os.path.join(root, *PurePosixPath(subfolder).parts, filename))
    try:
        contained = os.path.commonpath((root, target)) == root
    except ValueError:
        contained = False
    return (root, target) if contained and target != root else None


def _remove_empty_parents(path: str, root: str) -> None:
    current = os.path.dirname(path)
    while current != root and os.path.commonpath((root, current)) == root:
        try:
            os.rmdir(current)
        except OSError:
            break
        current = os.path.dirname(current)


@PromptServer.instance.routes.post(_ROUTE)
async def delete_frontend_artifacts(request: web.Request) -> web.Response:
    if request.content_length is not None and request.content_length > _MAX_REQUEST_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=_MAX_REQUEST_BYTES,
            actual_size=request.content_length,
        )
    body = await request.content.read(_MAX_REQUEST_BYTES + 1)
    if len(body) > _MAX_REQUEST_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=_MAX_REQUEST_BYTES,
            actual_size=len(body),
        )
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise web.HTTPBadRequest(text="Invalid JSON payload") from exc
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list) or len(artifacts) > _MAX_ARTIFACTS:
        raise web.HTTPBadRequest(text="Invalid artifact list")

    deleted = 0
    missing = 0
    for raw_reference in artifacts:
        locator = _safe_locator(raw_reference)
        if locator is None:
            raise web.HTTPBadRequest(text="Unsafe artifact locator")
        target = _target_path(*locator)
        if target is None:
            raise web.HTTPBadRequest(text="Unsafe artifact path")
        root, path = target
        try:
            os.remove(path)
        except FileNotFoundError:
            missing += 1
        except IsADirectoryError as exc:
            raise web.HTTPBadRequest(text="Artifact locator is not a file") from exc
        else:
            deleted += 1
            _remove_empty_parents(path, root)
    return web.json_response({"deleted": deleted, "missing": missing})


NODE_CLASS_MAPPINGS: dict[str, type[Any]] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
