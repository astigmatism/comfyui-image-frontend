from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlencode, urlparse, urlunparse

import httpx
import websockets

from ..config import Settings
from ..domain.publication import parse_json_object, validate_userdata_path
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
        ("/v2/userdata", "v2_query"),
        ("/userdata", "query"),
    )
    GET_ROUTE = "/userdata/{path}"

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.base_url = settings.comfyui_base_url
        headers = {"Comfy-User": settings.comfyui_user} if settings.comfyui_user else None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
            headers=headers,
        )
        self._capabilities: ComfyCapabilities | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _request_limited(
        self,
        method: str,
        url: str,
        *,
        maximum_bytes: int,
        context: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Read an HTTP response incrementally and stop once its byte budget is exceeded."""

        async with self._client.stream(method, url, **kwargs) as response:
            _enforce_declared_response_size(response, maximum_bytes, context)
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > maximum_bytes:
                    raise AppError(
                        "response_too_large", f"{context} exceeded the configured size limit."
                    )
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=bytes(content),
                request=response.request,
            )

    async def probe(self) -> ComfyCapabilities:
        object_response = await self._request_limited(
            "GET",
            "/object_info",
            maximum_bytes=self.settings.comfyui_object_info_max_bytes,
            context="ComfyUI object information",
        )
        object_response.raise_for_status()
        object_info = _response_json_object(
            object_response,
            maximum_bytes=self.settings.comfyui_object_info_max_bytes,
            context="ComfyUI object information",
        )
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
            response = await self._request_limited(
                "GET",
                "/system_stats",
                maximum_bytes=self.settings.comfyui_listing_max_bytes,
                context="ComfyUI system statistics",
            )
            if response.is_success:
                system = _response_json_object(
                    response,
                    maximum_bytes=self.settings.comfyui_listing_max_bytes,
                    context="ComfyUI system statistics",
                )
        except (AppError, httpx.HTTPError, ValueError):
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
                    response = await self._request_limited(
                        "GET",
                        template,
                        params={"path": directory},
                        maximum_bytes=self.settings.comfyui_listing_max_bytes,
                        context="ComfyUI userdata listing",
                    )
                elif mode == "query":
                    response = await self._request_limited(
                        "GET",
                        template,
                        params={"dir": directory, "recurse": "true", "full_info": "true"},
                        maximum_bytes=self.settings.comfyui_listing_max_bytes,
                        context="ComfyUI userdata listing",
                    )
                if response.is_success:
                    _parse_file_list(
                        _response_json(
                            response,
                            maximum_bytes=self.settings.comfyui_listing_max_bytes,
                            context="ComfyUI userdata listing",
                        )
                    )
                    return f"{mode}:{template}"
            except (AppError, httpx.HTTPError, ValueError, TypeError):
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
        try:
            response = await self._request_limited(
                "GET",
                self.GET_ROUTE.format(path=_quote_userdata_segment(probe_path)),
                maximum_bytes=self.settings.comfyui_listing_max_bytes,
                context="ComfyUI userdata retrieval probe",
            )
            if response.status_code in {200, 404}:
                return f"path:{self.GET_ROUTE}"
        except httpx.HTTPError:
            pass
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
            response = await self._request_limited(
                "GET",
                template,
                params={"path": directory},
                maximum_bytes=self.settings.comfyui_listing_max_bytes,
                context="ComfyUI userdata listing",
            )
        elif mode == "query":
            response = await self._request_limited(
                "GET",
                template,
                params={"dir": directory, "recurse": "true", "full_info": "true"},
                maximum_bytes=self.settings.comfyui_listing_max_bytes,
                context="ComfyUI userdata listing",
            )
        else:
            raise AppError("comfyui_capability_missing", "Unsupported userdata listing route mode.")
        response.raise_for_status()
        files = _parse_file_list(
            _response_json(
                response,
                maximum_bytes=self.settings.comfyui_listing_max_bytes,
                context="ComfyUI userdata listing",
            )
        )
        safe: list[str] = []
        for value in files:
            full_path = value if value.startswith(directory + "/") else f"{directory}/{value}"
            # Listing paths remain untrusted until candidate-by-candidate validation in the
            # registry. Keeping malformed entries here lets one bad candidate produce a precise
            # diagnostic without suppressing unrelated valid publications.
            safe.append(full_path)
        return sorted(set(safe))

    async def get_userdata_file(self, path: str, *, maximum_bytes: int) -> bytes:
        capabilities = self._capabilities or await self.probe()
        full_path = validate_userdata_path(path, context="userdata artifact path")
        mode, template = capabilities.workflow_get_route.split(":", 1)
        if mode != "path":
            raise AppError(
                "comfyui_capability_missing",
                "ComfyUI userdata retrieval does not preserve encoded path segments.",
                status_code=503,
            )
        response = await self._request_limited(
            "GET",
            template.format(path=_quote_userdata_segment(full_path)),
            maximum_bytes=maximum_bytes,
            context="ComfyUI userdata artifact",
        )
        response.raise_for_status()
        _enforce_response_size(response, maximum_bytes, "ComfyUI userdata artifact")
        return response.content

    async def get_workflow_file(self, path: str) -> dict[str, Any]:
        """Compatibility helper for internal callers; discovery hashes get_userdata_file bytes."""

        full_path = path
        directory = self.settings.comfyui_workflow_directory
        if not full_path.startswith(directory + "/"):
            full_path = f"{directory}/{path}"
        raw = await self.get_userdata_file(
            full_path, maximum_bytes=self.settings.comfyui_api_max_bytes
        )
        return parse_json_object(
            raw,
            context="ComfyUI workflow artifact",
            maximum_bytes=self.settings.comfyui_api_max_bytes,
        )

    async def object_info(self) -> dict[str, Any]:
        if self._capabilities:
            return self._capabilities.object_info
        return (await self.probe()).object_info

    async def submit_prompt(
        self,
        graph: Mapping[str, Any],
        client_id: str,
        *,
        extra_data: Mapping[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {"prompt": graph, "client_id": client_id}
        if extra_data is not None:
            payload["extra_data"] = extra_data
        try:
            response = await self._request_limited(
                "POST",
                "/prompt",
                json=payload,
                maximum_bytes=1024 * 1024,
                context="ComfyUI prompt response",
            )
        except httpx.ConnectError:
            # A connection failure occurs before ComfyUI accepts the request and remains
            # eligible for the worker's existing outage/requeue policy.
            raise
        except httpx.TransportError as exc:
            # Once request transmission may have started, retrying without a prompt_id could
            # duplicate a job that ComfyUI actually accepted. Preserve that uncertainty.
            raise AppError(
                "comfyui_submission_uncertain",
                "ComfyUI did not confirm whether the workflow request was accepted.",
                status_code=503,
                details={"transport": type(exc).__name__},
            ) from exc
        if 400 <= response.status_code < 500:
            raise AppError(
                "comfyui_prompt_rejected",
                "ComfyUI rejected the compiled workflow request.",
                status_code=503,
                details={"status": response.status_code, "response": _safe_json(response)},
            )
        if not response.is_success:
            raise AppError(
                "comfyui_submission_uncertain",
                "ComfyUI did not confirm whether the workflow request was accepted.",
                status_code=503,
                details={"status": response.status_code},
            )
        try:
            response_payload = _response_json(
                response, maximum_bytes=1024 * 1024, context="ComfyUI prompt response"
            )
        except AppError as exc:
            raise AppError(
                "comfyui_submission_uncertain",
                "ComfyUI returned an invalid response after workflow submission.",
                status_code=503,
                details={"status": response.status_code},
            ) from exc
        prompt_id = (
            response_payload.get("prompt_id") if isinstance(response_payload, dict) else None
        )
        if not isinstance(prompt_id, str) or not prompt_id:
            raise AppError(
                "comfyui_submission_uncertain",
                "ComfyUI did not confirm the workflow request with a prompt identifier.",
                status_code=503,
                details={"status": response.status_code},
            )
        return prompt_id

    async def upload_image(
        self,
        content: bytes,
        filename: str,
        *,
        kind: str,
        mime_type: str = "image/png",
        subfolder: str | None = None,
    ) -> str:
        safe_name = PurePosixPath(filename).name
        files = {"image": (safe_name, content, mime_type)}
        data = {"type": "input", "overwrite": "false"}
        if subfolder:
            data["subfolder"] = subfolder
        elif kind == "mask":
            data["subfolder"] = "frontend-masks"
        response = await self._request_limited(
            "POST",
            "/upload/image",
            files=files,
            data=data,
            maximum_bytes=1024 * 1024,
            context="ComfyUI upload response",
        )
        response.raise_for_status()
        payload = _response_json_object(
            response, maximum_bytes=1024 * 1024, context="ComfyUI upload response"
        )
        raw_name = payload.get("name")
        raw_subfolder = payload.get("subfolder", "")
        if (
            not isinstance(raw_name, str)
            or not raw_name
            or raw_name != PurePosixPath(raw_name).name
            or "\\" in raw_name
            or raw_name in {".", ".."}
            or not isinstance(raw_subfolder, str)
            or "\\" in raw_subfolder
            or raw_subfolder.startswith("/")
            or (
                any(part in {"", ".", ".."} for part in raw_subfolder.split("/"))
                and raw_subfolder != ""
            )
            or payload.get("type") != "input"
        ):
            raise AppError("upload_failed", "ComfyUI returned an invalid upload reference.")
        returned_subfolder = raw_subfolder.strip("/")
        if subfolder is not None and returned_subfolder != subfolder:
            raise AppError("upload_failed", "ComfyUI returned an unexpected upload namespace.")
        return f"{returned_subfolder}/{raw_name}" if returned_subfolder else raw_name

    async def events(self, client_id: str) -> AsyncIterator[dict[str, Any]]:
        ws_url = self.settings.comfyui_ws_url or _derive_ws_url(self.base_url)
        separator = "&" if "?" in ws_url else "?"
        url = f"{ws_url}{separator}{urlencode({'clientId': client_id})}"
        additional_headers = (
            {"Comfy-User": self.settings.comfyui_user} if self.settings.comfyui_user else None
        )
        async with websockets.connect(
            url,
            open_timeout=8,
            close_timeout=3,
            max_size=8 * 1024 * 1024,
            additional_headers=additional_headers,
        ) as ws:
            async for message in ws:
                if isinstance(message, bytes):
                    # Binary sampler previews are deliberately ignored; only contract-declared
                    # retrievable artifacts become application history.
                    continue
                try:
                    payload = _loads_strict_json(message, context="ComfyUI websocket event")
                except AppError:
                    continue
                if isinstance(payload, dict):
                    yield payload

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        safe_id = quote(prompt_id, safe="")
        response = await self._request_limited(
            "GET",
            f"/history/{safe_id}",
            maximum_bytes=self.settings.comfyui_history_max_bytes,
            context="ComfyUI history",
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = _response_json(
            response,
            maximum_bytes=self.settings.comfyui_history_max_bytes,
            context="ComfyUI history",
        )
        if not isinstance(payload, dict):
            return None
        if prompt_id in payload and isinstance(payload[prompt_id], dict):
            return dict(payload[prompt_id])
        if any(key in payload for key in ("outputs", "status", "prompt")):
            return payload
        return None

    async def queue(self) -> dict[str, Any]:
        response = await self._request_limited(
            "GET",
            "/queue",
            maximum_bytes=self.settings.comfyui_listing_max_bytes,
            context="ComfyUI queue",
        )
        response.raise_for_status()
        return _response_json_object(
            response,
            maximum_bytes=self.settings.comfyui_listing_max_bytes,
            context="ComfyUI queue",
        )

    async def cancel(self, prompt_id: str, *, running: bool) -> None:
        del running  # A caller hint can never authorize ComfyUI's global interrupt endpoint.
        queue_state: dict[str, Any] = {}
        try:
            queue_state = await self.queue()
        except httpx.HTTPError:
            # A targeted queue deletion remains safe, but without an authoritative queue
            # snapshot we must never use ComfyUI's global interrupt endpoint.
            queue_state = {}
        running_ids, pending_ids = _queue_prompt_ids(queue_state)

        if prompt_id in pending_ids:
            await self._delete_queued_prompt(prompt_id)
            return

        if prompt_id in running_ids:
            raw_running = queue_state.get("queue_running")
            if not isinstance(raw_running, list) or len(raw_running) != 1:
                raise AppError(
                    "cancellation_targeting_unavailable",
                    (
                        "ComfyUI did not prove the target is its sole running prompt and does "
                        "not expose a safe targeted interrupt."
                    ),
                    status_code=409,
                )
            response = await self._request_limited(
                "POST",
                "/interrupt",
                json={"prompt_id": prompt_id},
                maximum_bytes=self.settings.comfyui_listing_max_bytes,
                context="ComfyUI interrupt response",
            )
            if response.status_code not in {200, 204}:
                response.raise_for_status()
            return

        # Deleting an unknown/pending identifier is safe and also closes a dispatch race.
        await self._delete_queued_prompt(prompt_id)

    async def _delete_queued_prompt(self, prompt_id: str) -> None:
        response = await self._request_limited(
            "POST",
            "/queue",
            json={"delete": [prompt_id]},
            maximum_bytes=self.settings.comfyui_listing_max_bytes,
            context="ComfyUI queue response",
        )
        if response.status_code not in {200, 204}:
            response.raise_for_status()

    async def retrieve_artifact(self, reference: Mapping[str, Any]) -> bytes:
        filename = reference.get("filename")
        if (
            not isinstance(filename, str)
            or not filename
            or len(filename) > 500
            or "/" in filename
            or "\\" in filename
            or PurePosixPath(filename).name != filename
        ):
            raise AppError("output_unclassified", "ComfyUI artifact reference has no filename.")
        raw_subfolder = reference.get("subfolder", "")
        if not isinstance(raw_subfolder, str):
            raise AppError("output_unclassified", "ComfyUI artifact path is unsafe.")
        subfolder = raw_subfolder
        subfolder_path = PurePosixPath(subfolder)
        if len(subfolder) > 500 or (
            subfolder
            and (
                subfolder.startswith("/")
                or "\\" in subfolder
                or "//" in subfolder
                or any(part in {".", ".."} for part in subfolder_path.parts)
                or str(subfolder_path) != subfolder
            )
        ):
            raise AppError("output_unclassified", "ComfyUI artifact path is unsafe.")
        storage_type = reference.get("type", "output")
        if storage_type not in {"input", "output", "temp"}:
            raise AppError("output_unclassified", "ComfyUI artifact storage type is unsafe.")
        response = await self._request_limited(
            "GET",
            "/view",
            params={"filename": filename, "subfolder": subfolder, "type": storage_type},
            maximum_bytes=self.settings.comfyui_output_max_bytes,
            context="ComfyUI output artifact",
        )
        response.raise_for_status()
        _enforce_response_size(
            response, self.settings.comfyui_output_max_bytes, "ComfyUI output artifact"
        )
        return response.content

    async def health(self) -> tuple[bool, str | None]:
        try:
            response = await self._request_limited(
                "GET",
                "/object_info",
                timeout=5,
                maximum_bytes=self.settings.comfyui_object_info_max_bytes,
                context="ComfyUI health response",
            )
            if response.is_success:
                return True, None
            return False, f"ComfyUI returned HTTP {response.status_code}."
        except (AppError, httpx.HTTPError):
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
            item_type = item.get("type", "file")
            if isinstance(value, str) and item_type not in {"directory", "folder"}:
                result.append(value)
    return result


def _quote_userdata_segment(value: str) -> str:
    """Encode a ComfyUI userdata path as the route's single path segment."""
    return quote(value, safe="")


def _derive_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = (parsed.path.rstrip("/") + "/ws") or "/ws"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def _safe_json(response: httpx.Response) -> Any:
    if len(response.content) > 1024 * 1024:
        return None
    try:
        value = _loads_strict_json(response.content, context="ComfyUI error response")
        if isinstance(value, dict):
            return {key: value[key] for key in value if key in {"error", "node_errors"}}
        return None
    except AppError:
        return None


def _enforce_response_size(response: httpx.Response, maximum_bytes: int, context: str) -> None:
    _enforce_declared_response_size(response, maximum_bytes, context)
    if len(response.content) > maximum_bytes:
        raise AppError("response_too_large", f"{context} exceeded the configured size limit.")


def _enforce_declared_response_size(
    response: httpx.Response, maximum_bytes: int, context: str
) -> None:
    raw_length = response.headers.get("content-length")
    if raw_length:
        try:
            if int(raw_length) > maximum_bytes:
                raise AppError(
                    "response_too_large", f"{context} exceeded the configured size limit."
                )
        except ValueError:
            pass


def _response_json(response: httpx.Response, *, maximum_bytes: int, context: str) -> Any:
    _enforce_response_size(response, maximum_bytes, context)
    return _loads_strict_json(response.content, context=context)


def _loads_strict_json(raw: bytes | str, *, context: str) -> Any:
    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite number: {value}")
        return parsed

    try:
        return json.loads(
            raw,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number: {value}")
            ),
            parse_float=parse_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AppError("invalid_comfyui_response", f"{context} was not valid JSON.") from exc


def _response_json_object(
    response: httpx.Response, *, maximum_bytes: int, context: str
) -> dict[str, Any]:
    value = _response_json(response, maximum_bytes=maximum_bytes, context=context)
    if not isinstance(value, dict):
        raise AppError("invalid_comfyui_response", f"{context} was not a JSON object.")
    return value


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
