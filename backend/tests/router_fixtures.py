"""LLM Router capabilities documents for tests (docs/llm-router-contract.md section 4).

The shapes mirror the router's ``GET /v1/router/capabilities`` at llm-router ``d5edba8``:
``models[].metadata.reasoning.efforts`` is an object keyed by effort name, ``ids`` maps every
accepted ID to its canonical model, and a solo configuration lists Nighttime under
``offline_services``. Canonical IDs are deliberately unlike service IDs so a test notices any
code that sends or compares them.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import httpx

CAPABILITIES_PATH = "/v1/router/capabilities"
EVENTS_PATH = "/v1/router/events"

DAYTIME_PAIRED_ID = "fake-27b-q6-tensor-next"
NIGHTTIME_ID = "fake-27b-abliterated-q6"
DAYTIME_SOLO_ID = "fake-flash-next-solo-mtp3"

ALL_EFFORTS = {
    "default": "default",
    "off": "none",
    "low": "low",
    "medium": "medium",
    "xhigh": "xhigh",
}


def router_model(
    service: str,
    *,
    canonical: str | None = None,
    nsfw: bool | None = False,
    score: float | None = 60.0,
    context_window: int = 131_072,
    vision: bool = True,
    thinking: bool = True,
    tools: bool = True,
    efforts: dict[str, str] | list[str] | None = None,
    available: bool = True,
    slots: int = 1,
    aliases: Sequence[str] | None = None,
) -> dict[str, Any]:
    """One ``models[]`` entry. ``efforts=None`` publishes every effort."""

    capabilities = ["completion"]
    if thinking:
        capabilities.append("thinking")
    if tools:
        capabilities.append("tools")
    if vision:
        capabilities.append("vision")
    published = copy.deepcopy(ALL_EFFORTS if efforts is None else efforts)
    reasoning: dict[str, Any] = (
        {"supported": True, "efforts": published, "default": "default"}
        if thinking
        else {"supported": False, "efforts": {}}
    )
    return {
        "id": canonical or f"fake-{service}-model",
        "service": service,
        "display_name": f"Fake {service.capitalize()}",
        "aliases": list(aliases if aliases is not None else [service]),
        "available": available,
        "slots": slots,
        "context_window": context_window,
        "input_modalities": ["text", "image"] if vision else ["text"],
        "capabilities": capabilities,
        "nsfw": nsfw,
        "capability_score": score,
        "metadata": {
            "schema_version": 2,
            "context_window": context_window,
            "context_safety_reserve": 1024,
            "nsfw": nsfw,
            "reasoning": reasoning,
            "capability_score": {"value": score, "version": 1, "basis": "computed"},
        },
    }


def paired_models(*, vision: bool = True, unavailable: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Daytime and Nighttime together; Daytime scores higher, as in production."""

    down = set(unavailable)
    return [
        router_model(
            "daytime",
            canonical=DAYTIME_PAIRED_ID,
            nsfw=False,
            score=68.3,
            context_window=163_840,
            vision=vision,
            available="daytime" not in down,
            aliases=["local-active", "daytime"],
        ),
        router_model(
            "nighttime",
            canonical=NIGHTTIME_ID,
            nsfw=True,
            score=64.9,
            context_window=98_304,
            vision=vision,
            available="nighttime" not in down,
            aliases=["nighttime"],
        ),
    ]


def solo_models(*, vision: bool = True, unavailable: Iterable[str] = ()) -> list[dict[str, Any]]:
    """An exclusive configuration: Daytime on every GPU, Nighttime stopped."""

    return [
        router_model(
            "daytime",
            canonical=DAYTIME_SOLO_ID,
            nsfw=False,
            score=77.8,
            context_window=131_072,
            vision=vision,
            available="daytime" not in set(unavailable),
            aliases=["local-active", "daytime"],
        )
    ]


NIGHTTIME_OFFLINE = {
    "model": NIGHTTIME_ID,
    "aliases": ["nighttime"],
    "display_name": "Fake Nighttime",
    "role": "everyday",
    "reason": "exclusive_configuration",
}


def _revision(document: dict[str, Any]) -> str:
    content = {key: value for key, value in document.items() if key not in {"revision", "load"}}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def router_document(
    models: Sequence[dict[str, Any]],
    *,
    configuration_id: str = "fake-paired",
    exclusive: bool = False,
    draining: bool = False,
    maintenance: bool = False,
    offline_services: Sequence[dict[str, Any]] = (),
    schema_version: int = 1,
) -> dict[str, Any]:
    accepting = not (draining or maintenance)
    entries = [copy.deepcopy(model) for model in models]
    if not accepting:
        for entry in entries:
            entry["available"] = False
    ids: dict[str, str] = {}
    for entry in entries:
        ids[entry["id"]] = entry["id"]
        for alias in entry["aliases"]:
            ids[alias] = entry["id"]
    document: dict[str, Any] = {
        "object": "router.capabilities",
        "schema_version": schema_version,
        "observed_at": "2026-10-07T00:00:00.000Z",
        "complete": True,
        "warnings": [],
        "router": {
            "name": "llm-router",
            "version": "0.1.0",
            "accepting_requests": accepting,
            "draining": draining,
            "drain_reason": "configuration_switch" if draining else None,
            "maintenance": maintenance,
        },
        "configuration": {
            "id": configuration_id,
            "exclusive": exclusive,
            "runtime_revision": "fake-runtime",
            "published_at": "2026-10-07T00:00:00.000Z",
        },
        "default_model": entries[0]["id"] if entries else None,
        "models": entries,
        "offline_services": [copy.deepcopy(entry) for entry in offline_services],
        "ids": ids,
        "some_future_field": {"clients": "must ignore unknown fields"},
    }
    document["revision"] = _revision(document)
    return document


def paired_document(**options: Any) -> dict[str, Any]:
    vision = options.pop("vision", True)
    unavailable = options.pop("unavailable", ())
    return router_document(paired_models(vision=vision, unavailable=unavailable), **options)


def solo_document(**options: Any) -> dict[str, Any]:
    vision = options.pop("vision", True)
    unavailable = options.pop("unavailable", ())
    options.setdefault("configuration_id", "fake-solo")
    options.setdefault("exclusive", True)
    options.setdefault("offline_services", [NIGHTTIME_OFFLINE])
    return router_document(solo_models(vision=vision, unavailable=unavailable), **options)


def capabilities_response(
    request: httpx.Request, document: dict[str, Any] | None = None
) -> httpx.Response:
    """Answer ``GET /v1/router/capabilities`` with an ETag and 304 support."""

    doc = document if document is not None else paired_document()
    etag = f'"{doc["revision"]}"'
    if request.headers.get("if-none-match") == etag:
        return httpx.Response(304, headers={"ETag": etag})
    return httpx.Response(200, json=doc, headers={"ETag": etag, "Cache-Control": "no-cache"})


def with_router(
    handler: Callable[[httpx.Request], httpx.Response],
    document: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Wrap a MockTransport handler so it also serves the capabilities document."""

    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            doc = document() if callable(document) else document
            return capabilities_response(request, doc)
        return handler(request)

    return wrapped


def sse_event(event: str, data: Any, *, identity: str | None = None) -> str:
    lines = [f"event: {event}"]
    if identity:
        lines.append(f"id: {identity}")
    lines.extend(f"data: {line}" for line in json.dumps(data).splitlines())
    return "\n".join(lines) + "\n\n"


def router_error(code: str, status: int, message: str | None = None) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": code, "message": message or f"fake {code}"}}
    )
