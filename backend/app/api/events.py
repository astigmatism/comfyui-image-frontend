from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..container import AppContainer
from ..dependencies import (
    AuthContext,
    get_container,
    require_auth,
    require_ready_user,
)
from ..models import GenerationEvent
from ..services.events import event_payload

router = APIRouter(prefix="/api", tags=["events"])
logger = logging.getLogger(__name__)
_KEEPALIVE_SECONDS = 15.0


@dataclass(frozen=True)
class _StreamContext:
    owner_id: str
    raw_token: str


async def _run_blocking[T](operation: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Finish a thread-owned database operation before propagating cancellation."""

    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


@router.get("/events")
async def events(
    request: Request,
    last_event_id_query: Annotated[int | None, Query(alias="last_event_id", ge=0)] = None,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    container = get_container(request)
    try:
        header_id = int(last_event_id_header) if last_event_id_header else 0
    except ValueError:
        header_id = 0
    after_id = max(last_event_id_query or 0, header_id)
    raw_token = request.cookies.get(container.settings.session_cookie_name)
    context = await _run_blocking(_load_stream_context, container, raw_token)

    return StreamingResponse(
        _event_stream(
            request,
            container,
            context.owner_id,
            context.raw_token,
            None,
            after_id=after_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _load_stream_context(
    container: AppContainer,
    raw_token: str | None,
) -> _StreamContext:
    # Resolve authentication in a thread-confined local scope so an idle SSE client retains only
    # immutable primitives, never a Session or detached ORM instance.
    with container.db.session_factory() as session:
        resolved = container.auth.resolve_session(session, raw_token)
        auth_context = (
            AuthContext(user=resolved[0], session=resolved[1], raw_token=raw_token)
            if resolved is not None and raw_token is not None
            else None
        )
        context = require_ready_user(require_auth(auth_context))
        return _StreamContext(
            owner_id=context.user.id,
            raw_token=context.raw_token,
        )


def _load_replay(
    container: AppContainer,
    owner_id: str,
    after_id: int,
) -> tuple[dict[str, Any], ...]:
    """Materialize replay rows without retaining a request-scoped database session."""

    with container.db.session_factory() as session:
        return tuple(
            event_payload(event)
            for event in session.scalars(
                select(GenerationEvent)
                .where(
                    GenerationEvent.owner_id == owner_id,
                    GenerationEvent.id > after_id,
                )
                .order_by(GenerationEvent.id)
                .limit(1000)
            )
        )


async def _event_stream(
    request: Request,
    container: AppContainer,
    owner_id: str,
    raw_token: str,
    replay: Sequence[dict[str, Any]] | None,
    *,
    after_id: int = 0,
) -> AsyncIterator[str]:
    try:
        async with container.broker.subscribe(owner_id) as queue:
            # Subscribe before querying durable replay so an event committed during the query is
            # either in replay, queued live, or both. Durable IDs at or below the replay high-water
            # mark are the only queued items discarded, preserving replay-first ordering and all
            # ephemeral events.
            resolved_replay = (
                replay
                if replay is not None
                else await _run_blocking(_load_replay, container, owner_id, after_id)
            )
            replay_high_water = after_id
            for event in resolved_replay:
                event_id = _durable_event_id(event)
                if event_id is not None:
                    replay_high_water = max(replay_high_water, event_id)
                yield _sse(event)

            loop = asyncio.get_running_loop()
            next_revalidation = loop.time() + _KEEPALIVE_SECONDS
            while True:
                if await request.is_disconnected():
                    return
                timeout = max(0.0, next_revalidation - loop.time())
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout)
                except TimeoutError:
                    if not await asyncio.to_thread(
                        _session_is_valid,
                        container,
                        owner_id,
                        raw_token,
                    ):
                        return
                    next_revalidation = loop.time() + _KEEPALIVE_SECONDS
                    yield ": keep-alive\n\n"
                    continue
                item_id = _durable_event_id(item)
                if item_id is not None and item_id <= replay_high_water:
                    continue
                if loop.time() >= next_revalidation:
                    if not await asyncio.to_thread(
                        _session_is_valid,
                        container,
                        owner_id,
                        raw_token,
                    ):
                        return
                    next_revalidation = loop.time() + _KEEPALIVE_SECONDS
                yield _sse(item)
    except asyncio.CancelledError:
        # Cancellation is the expected Uvicorn shutdown path. The subscription context has
        # already removed its queue; re-raise so task cancellation is never swallowed.
        logger.info("event_stream_cancelled", extra={"actor_user_id": owner_id})
        raise


def _durable_event_id(item: dict[str, Any]) -> int | None:
    event_id = item.get("id")
    return event_id if type(event_id) is int and event_id >= 0 else None


def _session_is_valid(container: AppContainer, owner_id: str, raw_token: str) -> bool:
    with container.db.session_factory() as session:
        resolved = container.auth.resolve_session(session, raw_token)
        return bool(
            resolved is not None
            and resolved[0].id == owner_id
            and not resolved[0].must_change_password
        )


def _sse(item: dict) -> str:  # type: ignore[type-arg]
    event_id = item.get("id")
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {item.get('type', 'message')}")
    lines.append(f"data: {json.dumps(item, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"
