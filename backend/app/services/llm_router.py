"""LLM Router client contract, version 1 (``docs/llm-router-contract.md``).

Discovery, model selection and error classification for the Prompt Assistant. The pure functions
are a port of the router maintainer's reference client ``docs/clients/router_watch.py`` at
llm-router ``d5edba8`` (``resolve``, ``pick_service``, ``model_for``, ``error_code`` and
``classify_error``); :class:`RouterWatch` ports its subscriber loop to ``httpx.AsyncClient``.

Nothing here infers anything from a model name: availability, NSFW status, capability score,
modalities, features, reasoning efforts and slots all come from the router's capabilities
document. Only service IDs (``daytime``, ``nighttime``) are ever returned for sending.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WAIT = "wait"  # router draining or in maintenance: hold the request, keep the model
UNAVAILABLE = "unavailable"  # no preferred or fallback service is usable right now

FALLBACK = "fallback"  # this service cannot serve now; choose again (excluding it)
RETRY = "retry"  # transient; retry the same service with backoff
FAIL = "fail"  # the request itself is wrong; do not retry or switch

CLIENT_NAME = "comfyui-image-frontend"
CLIENT_NAME_HEADER = "X-Client-Name"
CAPABILITIES_PATH = "/v1/router/capabilities"
EVENTS_PATH = "/v1/router/events"

SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_METADATA_SCHEMA_VERSION = 2

# Contract section 4: poll every 30 s while the event stream is down, reconnect with backoff
# from the stream's retry: hint (3 s) up to 30 s, and treat 60 s without bytes as dead.
POLL_SECONDS = 30.0
RECONNECT_INITIAL_SECONDS = 3.0
RECONNECT_MAX_SECONDS = 30.0
STREAM_IDLE_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 15.0

FALLBACK_CODES = frozenset({"SERVICE_OFFLINE", "BACKEND_UNAVAILABLE", "MODEL_NOT_FOUND"})
WAIT_CODES = frozenset({"BACKEND_DRAINING", "MAINTENANCE_MODE"})

FEATURES: Mapping[str, Callable[[Mapping[str, Any]], bool]] = {
    "vision": lambda model: "image" in _string_list(model.get("input_modalities")),
    "tools": lambda model: "tools" in _string_list(model.get("capabilities")),
    "reasoning": lambda model: "thinking" in _string_list(model.get("capabilities")),
}

# Reasoning efforts in increasing order (contract section 6) and the router's accepted aliases.
EFFORT_ORDER = ("low", "medium", "xhigh")
EFFORT_ALIASES = {"none": "off", "minimal": "low", "high": "xhigh", "max": "xhigh"}

ChangeListener = Callable[[dict[str, Any]], Awaitable[None] | None]
Sleeper = Callable[[float], Awaitable[None]]


class RouterDocumentError(ValueError):
    """The router answered with something that is not a capabilities document."""


class RouterEventsUnavailable(RuntimeError):
    """The event stream answered with an error status instead of an SSE stream."""

    def __init__(self, status: int, code: str | None) -> None:
        super().__init__(f"router events returned HTTP {status} ({code or 'no code'})")
        self.status = status
        self.code = code


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _models(doc: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    models = doc.get("models") if isinstance(doc, Mapping) else None
    return (
        [model for model in models if isinstance(model, Mapping)]
        if isinstance(models, list)
        else []
    )


def accepting_requests(doc: Mapping[str, Any] | None) -> bool:
    router = doc.get("router") if isinstance(doc, Mapping) else None
    return bool(router.get("accepting_requests", True)) if isinstance(router, Mapping) else True


def _identities(model: Mapping[str, Any]) -> set[str]:
    names = {model.get("id"), model.get("service"), *_string_list(model.get("aliases"))}
    return {name for name in names if isinstance(name, str) and name}


def _has_features(model: Mapping[str, Any], require: Iterable[str]) -> bool:
    return all(FEATURES[feature](model) for feature in require)


def _check_features(require: Iterable[str]) -> tuple[str, ...]:
    features = tuple(require)
    unknown = [feature for feature in features if feature not in FEATURES]
    if unknown:
        raise ValueError("unknown required feature: " + ", ".join(unknown))
    return features


def resolve(
    doc: Mapping[str, Any] | None,
    preferred: str,
    fallbacks: Sequence[str] = (),
    *,
    require: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> str:
    """Selection by name: the first usable service of ``preferred`` then ``fallbacks``.

    Returns the service ID to send, :data:`WAIT`, or :data:`UNAVAILABLE`. With no document yet
    (router unreachable at startup) the first non-excluded name is returned and the request's
    error classification decides what happens next, exactly like the reference client. Unlike
    the reference ``resolve``, a candidate must also have every feature in ``require``.
    """

    features = _check_features(require)
    excluded = set(exclude)
    candidates = [name for name in (preferred, *fallbacks) if name and name not in excluded]
    if doc is None:
        return candidates[0] if candidates else UNAVAILABLE
    if not accepting_requests(doc):
        return WAIT
    for service in candidates:
        model = next((item for item in _models(doc) if service in _identities(item)), None)
        if model is not None and model.get("available") and _has_features(model, features):
            return service
    return UNAVAILABLE


def _rank(model: Mapping[str, Any]) -> tuple[bool, float, int]:
    score = model.get("capability_score")
    value = float(score) if isinstance(score, int | float) and not isinstance(score, bool) else None
    context = model.get("context_window")
    return (
        value is not None,
        value if value is not None else 0.0,
        context if isinstance(context, int) and not isinstance(context, bool) else 0,
    )


def pick_service(
    doc: Mapping[str, Any] | None,
    *,
    nsfw: bool | None = None,
    require: Iterable[str] = (),
    exclude: Iterable[str] = (),
    fallback_any: bool = False,
) -> str:
    """Selection by capability: the most capable usable service matching the filters.

    ``nsfw`` True keeps only models declared NSFW (abliterated), False only models declared not
    NSFW, None either. ``require`` lists features the request needs (``vision``, ``tools``,
    ``reasoning``); ``exclude`` holds service IDs that just failed. ``fallback_any`` uses the
    most capable usable model when none matches ``nsfw``. Models rank by ``capability_score``,
    then ``context_window``. Returns the model's ``service``, :data:`WAIT` or :data:`UNAVAILABLE`.
    """

    if doc is None:
        return UNAVAILABLE
    if not accepting_requests(doc):
        return WAIT
    features = _check_features(require)
    excluded = set(exclude)
    usable = [
        model
        for model in _models(doc)
        if model.get("available")
        and isinstance(model.get("service"), str)
        and model.get("service") not in excluded
        and model.get("id") not in excluded
        and _has_features(model, features)
    ]
    matching = [model for model in usable if nsfw is None or model.get("nsfw") is nsfw]
    candidates = matching or (usable if fallback_any else [])
    return str(max(candidates, key=_rank)["service"]) if candidates else UNAVAILABLE


def model_for(doc: Mapping[str, Any] | None, service: str | None) -> dict[str, Any] | None:
    """Limits and capabilities of the model a service ID targets right now, or None."""

    if not isinstance(doc, Mapping) or not service:
        return None
    ids = doc.get("ids")
    canonical = ids.get(service) if isinstance(ids, Mapping) else None
    models = _models(doc)
    found = next((model for model in models if canonical and model.get("id") == canonical), None)
    if found is None:
        found = next((model for model in models if service in _identities(model)), None)
    return dict(found) if found is not None else None


def error_code(body: Any) -> str | None:
    """The router's error code from an error body, or None (legacy string errors)."""

    try:
        if isinstance(body, bytes | str):
            body = json.loads(body)
        error = body.get("error") if isinstance(body, Mapping) else None
        code = error.get("code") if isinstance(error, Mapping) else None
        return code if isinstance(code, str) and code else None
    except (ValueError, AttributeError):
        return None


def classify_error(status: int | None, body: Any = None) -> str:
    """FALLBACK, WAIT, RETRY or FAIL for an HTTP status (None = network error) and body.

    The code is read first, then the status (contract section 10). Unknown 5xx codes are
    transient and unknown 4xx codes are request errors.
    """

    code = error_code(body)
    if code in WAIT_CODES:
        return WAIT
    if code in FALLBACK_CODES:
        return FALLBACK
    if status is None or status in (408, 429) or status >= 500:
        return RETRY
    return FAIL


def supported_efforts(model: Mapping[str, Any] | None) -> set[str] | None:
    """Reasoning efforts the model publishes in ``metadata.reasoning.efforts``, or None.

    The router publishes ``efforts`` as an object keyed by effort name (for example
    ``{"default": "default", "off": "none", "xhigh": "xhigh"}``); a plain list is accepted too.
    """

    metadata = model.get("metadata") if isinstance(model, Mapping) else None
    reasoning = metadata.get("reasoning") if isinstance(metadata, Mapping) else None
    efforts = reasoning.get("efforts") if isinstance(reasoning, Mapping) else None
    if isinstance(efforts, Mapping):
        names = [name for name in efforts if isinstance(name, str)]
    elif isinstance(efforts, list):
        names = _string_list(efforts)
    else:
        return None
    return {EFFORT_ALIASES.get(name, name) for name in names}


def supports_thinking(model: Mapping[str, Any] | None) -> bool | None:
    """Whether the model can think: True, False, or None when nothing is published."""

    if not isinstance(model, Mapping):
        return None
    if FEATURES["reasoning"](model):
        return True
    metadata = model.get("metadata")
    reasoning = metadata.get("reasoning") if isinstance(metadata, Mapping) else None
    supported = reasoning.get("supported") if isinstance(reasoning, Mapping) else None
    if supported is True:
        return True
    if supported is False or isinstance(model.get("capabilities"), list):
        return False
    return None


def thinking_value(model: Mapping[str, Any] | None, requested: Any) -> bool | str:
    """The ``think`` value to send to ``model`` for a requested effort, boolean or ``False``.

    An effort is sent only when the model lists it in ``metadata.reasoning.efforts``; otherwise
    the closest listed effort is used, or ``True`` (the model's default effort) when it lists
    none. A model that cannot think receives ``False``. Without any model facts (named selection
    before the first capabilities document) only ``True`` is sent, never an unlisted effort.
    """

    if requested is False or requested is None:
        return False
    thinking = supports_thinking(model)
    if thinking is False:
        return False
    if requested is True or not isinstance(requested, str) or thinking is None:
        return True
    effort: str = EFFORT_ALIASES.get(requested, requested)
    if effort == "off":
        return False
    efforts = supported_efforts(model)
    if efforts is None:
        return True
    if effort in efforts:
        return effort
    listed = [name for name in EFFORT_ORDER if name in efforts]
    if not listed:
        return True
    target = EFFORT_ORDER.index(effort) if effort in EFFORT_ORDER else len(EFFORT_ORDER) - 1
    return min(
        listed, key=lambda name: (abs(EFFORT_ORDER.index(name) - target), -EFFORT_ORDER.index(name))
    )


def validate_document(doc: Any) -> dict[str, Any]:
    """Accept a capabilities document (ignoring unknown fields) or raise RouterDocumentError."""

    if not isinstance(doc, dict):
        raise RouterDocumentError("capabilities document is not an object")
    if not isinstance(doc.get("router"), dict) or not isinstance(doc.get("models"), list):
        raise RouterDocumentError("capabilities document lacks router or models")
    revision = doc.get("revision")
    if not isinstance(revision, str) or not revision:
        raise RouterDocumentError("capabilities document lacks a revision")
    return doc


def document_summary(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """Metadata-only description of a document for logs: no prompts, no canonical IDs."""

    if not isinstance(doc, Mapping):
        return {"router_revision": None}
    configuration = doc.get("configuration")
    configuration = configuration if isinstance(configuration, Mapping) else {}
    return {
        "router_revision": doc.get("revision"),
        "router_configuration_id": configuration.get("id"),
        "router_exclusive": configuration.get("exclusive"),
        "router_accepting_requests": accepting_requests(doc),
        "router_services": sorted(
            str(model.get("service"))
            for model in _models(doc)
            if model.get("available") and isinstance(model.get("service"), str)
        ),
        "router_offline_services": sorted(
            alias
            for entry in (doc.get("offline_services") or [])
            if isinstance(entry, Mapping)
            for alias in _string_list(entry.get("aliases"))
        ),
    }


class RouterWatch:
    """Holds the current capabilities document and follows ``/v1/router/events``.

    One instance per process, shared by every request. :meth:`run_forever` reads the document
    at startup (tolerating an unreachable router), then holds one event-stream subscription and,
    while it is down, polls the capabilities endpoint with ``If-None-Match`` and reconnects with
    backoff. :meth:`refresh` is the request-scoped read used when no subscriber is running.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        sleeper: Sleeper | None = None,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._client = client
        self._sleeper = sleeper
        self.poll_seconds = poll_seconds
        self.doc: dict[str, Any] | None = None
        self._etag: str | None = None
        self._listeners: list[ChangeListener] = []
        self._retry_seconds = RECONNECT_INITIAL_SECONDS
        self._warned_versions: set[tuple[str, Any]] = set()
        self._last_error: str | None = None
        self.running = False
        self.connected = False
        self.last_fetch_at: float | None = None
        self.last_event_at: float | None = None

    @property
    def revision(self) -> str | None:
        return self.doc.get("revision") if self.doc else None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def add_listener(self, listener: ChangeListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: ChangeListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def _set(self, doc: Any) -> bool:
        accepted = validate_document(doc)
        previous = self.doc
        changed = previous is None or accepted["revision"] != previous.get("revision")
        self.doc = accepted
        if not changed:
            return False
        self._warn_on_schema_versions(accepted)
        logger.info(
            "llm_router_capabilities_changed",
            extra={
                "service": "llm_router",
                "operation": "discovery",
                "router_subscribed": self.connected,
                **document_summary(accepted),
            },
        )
        for listener in list(self._listeners):
            try:
                outcome = listener(accepted)
                if inspect.isawaitable(outcome):
                    await outcome
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("llm_router_change_listener_failed")
        return True

    def _warn_on_schema_versions(self, doc: Mapping[str, Any]) -> None:
        versions: list[tuple[str, Any, int]] = [
            ("capabilities", doc.get("schema_version"), SUPPORTED_SCHEMA_VERSION)
        ]
        for model in _models(doc):
            metadata = model.get("metadata")
            if isinstance(metadata, Mapping) and "schema_version" in metadata:
                versions.append(
                    (
                        "model_metadata",
                        metadata.get("schema_version"),
                        SUPPORTED_METADATA_SCHEMA_VERSION,
                    )
                )
        for kind, version, supported in versions:
            if version != supported and (kind, version) not in self._warned_versions:
                self._warned_versions.add((kind, version))
                logger.warning(
                    "llm_router_schema_version_unexpected",
                    extra={
                        "service": "llm_router",
                        "operation": "discovery",
                        "router_schema": kind,
                        "router_schema_version": version,
                        "router_supported_schema_version": supported,
                    },
                )

    async def fetch(self) -> dict[str, Any] | None:
        """GET the document, sending ``If-None-Match``; a 304 keeps the current copy."""

        headers = {"Accept": "application/json"}
        if self._etag and self.doc is not None:
            headers["If-None-Match"] = self._etag
        response = await self._client.get(
            CAPABILITIES_PATH, headers=headers, timeout=FETCH_TIMEOUT_SECONDS
        )
        self.last_fetch_at = time.monotonic()
        if response.status_code == 304 and self.doc is not None:
            return self.doc
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RouterDocumentError("capabilities document is not JSON") from exc
        await self._set(payload)
        self._etag = response.headers.get("ETag") or self._etag
        return self.doc

    async def refresh(self) -> dict[str, Any] | None:
        """:meth:`fetch`, tolerating an unreachable router; returns the current document."""

        try:
            await self.fetch()
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, RouterDocumentError) as exc:
            reason = _failure_reason(exc)
            if reason != self._last_error:
                logger.warning(
                    "llm_router_capabilities_unreachable",
                    extra={
                        "service": "llm_router",
                        "operation": "discovery",
                        "failure_kind": reason,
                        "router_revision": self.revision,
                    },
                )
            self._last_error = reason
        else:
            if self._last_error is not None:
                logger.info(
                    "llm_router_capabilities_reachable",
                    extra={"service": "llm_router", "operation": "discovery"},
                )
            self._last_error = None
        return self.doc

    async def _follow(self) -> bool:
        """Read ``/v1/router/events`` until it ends; True when a document arrived on it."""

        delivered = False
        timeout = httpx.Timeout(connect=5.0, read=STREAM_IDLE_SECONDS, write=30.0, pool=5.0)
        async with self._client.stream(
            "GET", EVENTS_PATH, headers={"Accept": "text/event-stream"}, timeout=timeout
        ) as stream:
            if stream.status_code != 200:
                body = await stream.aread()
                raise RouterEventsUnavailable(stream.status_code, error_code(body))
            self.connected = True
            logger.info(
                "llm_router_events_connected",
                extra={"service": "llm_router", "operation": "discovery"},
            )
            event: str | None = None
            data: list[str] = []
            async for line in stream.aiter_lines():
                self.last_event_at = time.monotonic()
                if line == "":
                    if event == "capabilities" and data:
                        try:
                            await self._set(json.loads("\n".join(data)))
                            delivered = True
                        except (ValueError, RouterDocumentError):
                            logger.warning(
                                "llm_router_event_invalid",
                                extra={"service": "llm_router", "operation": "discovery"},
                            )
                    event, data = None, []
                    continue
                if line.startswith(":"):
                    continue  # keepalive comment
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "event":
                    event = value.strip()
                elif field == "data":
                    data.append(value)
                elif field == "retry" and value.strip().isdigit():
                    self._retry_seconds = max(1.0, int(value.strip()) / 1000)
        return delivered

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Read at startup, then follow the event stream; poll and reconnect while it is down."""

        self.running = True
        delay = RECONNECT_INITIAL_SECONDS
        try:
            await self.refresh()
            while not (stop is not None and stop.is_set()):
                delivered = False
                failure: str | None = None
                try:
                    delivered = await self._follow()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failure = _failure_reason(exc)
                finally:
                    was_connected = self.connected
                    self.connected = False
                if was_connected or delivered:
                    delay = self._retry_seconds
                logger.info(
                    "llm_router_events_disconnected",
                    extra={
                        "service": "llm_router",
                        "operation": "discovery",
                        "failure_kind": failure or "stream_ended",
                        "backoff_seconds": delay,
                    },
                )
                # While disconnected, the capabilities endpoint is polled at least every 30 s.
                await self.refresh()
                if await self._wait(stop, delay):
                    return
                if not (was_connected or delivered):
                    delay = min(delay * 2, RECONNECT_MAX_SECONDS, self.poll_seconds)
        finally:
            self.running = False
            self.connected = False

    async def _wait(self, stop: asyncio.Event | None, seconds: float) -> bool:
        if self._sleeper is not None:
            await self._sleeper(seconds)
            return stop is not None and stop.is_set()
        if stop is None:
            await asyncio.sleep(seconds)
            return False
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True


def _failure_reason(exc: BaseException) -> str:
    if isinstance(exc, RouterEventsUnavailable):
        return exc.code or f"http_{exc.status}"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    return type(exc).__name__
