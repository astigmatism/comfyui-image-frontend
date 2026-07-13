from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlencode, urlparse, urlunparse

import httpx
import websockets

from ..config import Settings
from ..errors import AppError


@dataclass(frozen=True)
class ComfyCapabilities:
    object_info: dict[str, Any]
    workflow_list_route: str
    workflow_get_route: str
    system: dict[str, Any]
    assets: list[str]
    capabilities: dict[str, bool]


class ComfyUIAdapter:
    """Capability-probed server-side boundary around ComfyUI API differences."""

    LIST_CANDIDATES = (
        ("/api/v2/userdata", "v2_query"),
        ("/userdata", "query"),
        ("/api/userdata", "query"),
        ("/userdata/{directory}", "path"),
        ("/api/userdata/{directory}", "path"),
    )
    GET_CANDIDATES = (
        ("/userdata/{path}", "path"),
        ("/api/userdata/{path}", "path"),
        ("/userdata", "query"),
        ("/api/userdata", "query"),
    )

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.base_url = settings.comfyui_base_url
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
        )
        self._capabilities: ComfyCapabilities | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def probe(self) -> ComfyCapabilities:
        object_response = await self._client.get("/object_info")
        object_response.raise_for_status()
        object_info = object_response.json()
        if not isinstance(object_info, dict):
            raise AppError(
                "comfyui_capability_missing",
                "ComfyUI returned an invalid runtime schema.",
                status_code=503,
            )
        list_route = await self._probe_list_route()
        get_route = await self._probe_get_route()
        system: dict[str, Any] = {}
        try:
            response = await self._client.get("/system_stats")
            if response.is_success and isinstance(response.json(), dict):
                system = response.json()
        except (httpx.HTTPError, ValueError):
            system = {}
        assets = sorted(_extract_assets(object_info))
        capabilities = {
            "workflow_userdata": True,
            "object_info": True,
            "prompt": True,
            "history": True,
            "queue": True,
            "interrupt": True,
            "upload_image": True,
            "websocket": True,
        }
        self._capabilities = ComfyCapabilities(
            object_info=object_info,
            workflow_list_route=list_route,
            workflow_get_route=get_route,
            system=system,
            assets=assets,
            capabilities=capabilities,
        )
        return self._capabilities

    async def _probe_list_route(self) -> str:
        directory = self.settings.comfyui_workflow_directory
        for template, mode in self.LIST_CANDIDATES:
            try:
                if mode == "v2_query":
                    response = await self._client.get(template, params={"path": directory})
                elif mode == "query":
                    response = await self._client.get(template, params={"dir": directory, "recurse": "true"})
                else:
                    response = await self._client.get(
                        template.format(directory=_quote_userdata_segment(directory)),
                        params={"recurse": "true"},
                    )
                if response.is_success:
                    _parse_file_list(response.json())
                    return f"{mode}:{template}"
            except (httpx.HTTPError, ValueError, TypeError):
                continue
        raise AppError(
            "comfyui_capability_missing",
            "ComfyUI does not expose a compatible workflow user-data listing API.",
            status_code=503,
        )

    async def _probe_get_route(self) -> str:
        # Retrieval route existence is verified with a deliberately missing safe path. A 404 is
        # acceptable; 405/501 means the route shape is not supported.
        probe_path = f"{self.settings.comfyui_workflow_directory}/.__frontend_probe_missing__.json"
        for template, mode in self.GET_CANDIDATES:
            try:
                if mode == "query":
                    response = await self._client.get(template, params={"path": probe_path})
                else:
                    response = await self._client.get(
                        template.format(path=_quote_userdata_segment(probe_path))
                    )
                if response.status_code in {200, 404}:
                    return f"{mode}:{template}"
            except httpx.HTTPError:
                continue
        raise AppError(
            "comfyui_capability_missing",
            "ComfyUI does not expose a compatible workflow user-data retrieval API.",
            status_code=503,
        )

    async def list_workflow_files(self) -> list[str]:
        capabilities = self._capabilities or await self.probe()
        mode, template = capabilities.workflow_list_route.split(":", 1)
        directory = self.settings.comfyui_workflow_directory
        if mode == "v2_query":
            response = await self._client.get(template, params={"path": directory})
        elif mode == "query":
            response = await self._client.get(template, params={"dir": directory, "recurse": "true"})
        else:
            response = await self._client.get(
                template.format(directory=_quote_userdata_segment(directory)),
                params={"recurse": "true"},
            )
        response.raise_for_status()
        files = _parse_file_list(response.json())
        safe: list[str] = []
        for value in files:
            normalized = _safe_relative_path(value)
            if normalized.startswith(directory + "/"):
                normalized = normalized[len(directory) + 1 :]
            safe.append(normalized)
        return sorted(set(safe))

    async def get_workflow_file(self, relative_path: str) -> dict[str, Any]:
        capabilities = self._capabilities or await self.probe()
        relative = _safe_relative_path(relative_path)
        full_path = f"{self.settings.comfyui_workflow_directory}/{relative}"
        mode, template = capabilities.workflow_get_route.split(":", 1)
        if mode == "query":
            response = await self._client.get(template, params={"path": full_path})
        else:
            response = await self._client.get(
                template.format(path=_quote_userdata_segment(full_path))
            )
        response.raise_for_status()
        try:
            value = response.json()
        except ValueError as exc:
            raise AppError(
                "workflow_malformed_json", f"Workflow file {relative} is not valid JSON."
            ) from exc
        if not isinstance(value, dict):
            raise AppError("workflow_malformed_json", f"Workflow file {relative} must be a JSON object.")
        return value

    async def object_info(self) -> dict[str, Any]:
        if self._capabilities:
            return self._capabilities.object_info
        return (await self.probe()).object_info

    async def submit_prompt(self, graph: Mapping[str, Any], client_id: str) -> str:
        response = await self._client.post("/prompt", json={"prompt": graph, "client_id": client_id})
        if response.status_code >= 400:
            raise AppError(
                "comfyui_prompt_rejected",
                "ComfyUI rejected the compiled workflow request.",
                status_code=503,
                details={"status": response.status_code, "response": _safe_json(response)},
            )
        payload = response.json()
        prompt_id = payload.get("prompt_id") if isinstance(payload, dict) else None
        if not isinstance(prompt_id, str) or not prompt_id:
            raise AppError(
                "comfyui_prompt_rejected",
                "ComfyUI did not return a prompt identifier.",
                status_code=503,
            )
        return prompt_id

    async def upload_image(self, content: bytes, filename: str, *, kind: str) -> str:
        safe_name = PurePosixPath(filename).name
        files = {"image": (safe_name, content, "image/png")}
        data = {"type": "input", "overwrite": "false"}
        if kind == "mask":
            data["subfolder"] = "frontend-masks"
        response = await self._client.post("/upload/image", files=files, data=data)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            raise AppError("upload_failed", "ComfyUI returned an invalid upload reference.")
        subfolder = str(payload.get("subfolder", "")).strip("/")
        name = PurePosixPath(payload["name"]).name
        return f"{subfolder}/{name}" if subfolder else name

    async def events(self, client_id: str) -> AsyncIterator[dict[str, Any]]:
        ws_url = self.settings.comfyui_ws_url or _derive_ws_url(self.base_url)
        separator = "&" if "?" in ws_url else "?"
        url = f"{ws_url}{separator}{urlencode({'clientId': client_id})}"
        async with websockets.connect(url, open_timeout=8, close_timeout=3, max_size=8 * 1024 * 1024) as ws:
            async for message in ws:
                if isinstance(message, bytes):
                    # Binary sampler previews are deliberately ignored; only contract-declared
                    # retrievable artifacts become application history.
                    continue
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        safe_id = quote(prompt_id, safe="")
        response = await self._client.get(f"/history/{safe_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        if prompt_id in payload and isinstance(payload[prompt_id], dict):
            return payload[prompt_id]
        return payload

    async def queue(self) -> dict[str, Any]:
        response = await self._client.get("/queue")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def cancel(self, prompt_id: str, *, running: bool) -> None:
        queue_state: dict[str, Any] = {}
        try:
            queue_state = await self.queue()
        except httpx.HTTPError:
            # The normal reconciliation loop will retry. Fall back only when a single
            # application prompt can be active, where ComfyUI's global interrupt is safe.
            queue_state = {}
        running_ids, pending_ids = _queue_prompt_ids(queue_state)

        if prompt_id in pending_ids:
            await self._delete_queued_prompt(prompt_id)
            return

        if prompt_id in running_ids:
            if len(running_ids) > 1:
                raise AppError(
                    "cancellation_targeting_unavailable",
                    "ComfyUI reported multiple running prompts and does not expose a safe targeted interrupt.",
                    status_code=409,
                )
            response = await self._client.post("/interrupt", json={"prompt_id": prompt_id})
            if response.status_code not in {200, 204}:
                response.raise_for_status()
            return

        # Deleting an unknown/pending identifier is safe and also closes a dispatch race.
        await self._delete_queued_prompt(prompt_id)
        if running and self.settings.comfyui_concurrency == 1:
            history = await self.history(prompt_id)
            if history is None:
                response = await self._client.post("/interrupt", json={"prompt_id": prompt_id})
                if response.status_code not in {200, 204}:
                    response.raise_for_status()

    async def _delete_queued_prompt(self, prompt_id: str) -> None:
        response = await self._client.post("/queue", json={"delete": [prompt_id]})
        if response.status_code not in {200, 204}:
            response.raise_for_status()

    async def retrieve_artifact(self, reference: Mapping[str, Any]) -> bytes:
        filename = reference.get("filename")
        if not isinstance(filename, str):
            raise AppError("output_unclassified", "ComfyUI artifact reference has no filename.")
        filename = PurePosixPath(filename).name
        subfolder = str(reference.get("subfolder", "")).strip("/")
        if ".." in PurePosixPath(subfolder).parts:
            raise AppError("output_unclassified", "ComfyUI artifact path is unsafe.")
        storage_type = str(reference.get("type", "output"))
        response = await self._client.get(
            "/view",
            params={"filename": filename, "subfolder": subfolder, "type": storage_type},
        )
        response.raise_for_status()
        return response.content

    async def health(self) -> tuple[bool, str | None]:
        try:
            response = await self._client.get("/object_info", timeout=5)
            if response.is_success:
                return True, None
            return False, f"ComfyUI returned HTTP {response.status_code}."
        except httpx.HTTPError:
            return False, "ComfyUI is unreachable."


def _queue_prompt_ids(payload: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    def collect(key: str) -> set[str]:
        result: set[str] = set()
        items = payload.get(key, [])
        if not isinstance(items, list):
            return result
        for item in items:
            if isinstance(item, (list, tuple)) and len(item) > 1:
                result.add(str(item[1]))
            elif isinstance(item, Mapping):
                candidate = item.get("prompt_id") or item.get("id")
                if candidate is not None:
                    result.add(str(candidate))
        return result

    return collect("queue_running"), collect("queue_pending")


def _parse_file_list(payload: Any) -> list[str]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("files", "items", "entries"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
        else:
            raise ValueError("unsupported file list response")
    else:
        raise ValueError("unsupported file list response")
    result: list[str] = []
    for item in items:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, Mapping):
            value = item.get("path") or item.get("name") or item.get("filename")
            if isinstance(value, str) and item.get("type", "file") != "directory":
                result.append(value)
    return result


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/").lstrip("/"))
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise AppError("unsafe_path", "Workflow path is unsafe.")
    return str(path)


def _quote_userdata_segment(value: str) -> str:
    """Encode a ComfyUI userdata path as the route's single path segment."""
    return quote(value, safe="")


def _derive_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = (parsed.path.rstrip("/") + "/ws") or "/ws"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def _safe_json(response: httpx.Response) -> Any:
    try:
        value = response.json()
        if isinstance(value, dict):
            return {key: value[key] for key in value if key in {"error", "node_errors"}}
        return None
    except ValueError:
        return None


def _extract_assets(object_info: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for node in object_info.values():
        if not isinstance(node, Mapping):
            continue
        raw_input = node.get("input", {})
        if not isinstance(raw_input, Mapping):
            continue
        for section in ("required", "optional"):
            fields = raw_input.get(section, {})
            if not isinstance(fields, Mapping):
                continue
            for spec in fields.values():
                if not isinstance(spec, list) or not spec:
                    continue
                choices = spec[0]
                if isinstance(choices, list):
                    for choice in choices:
                        if isinstance(choice, str) and ("/" in choice or "." in choice):
                            result.add(choice)
    return result
