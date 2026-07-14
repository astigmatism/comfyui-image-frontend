from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..container import AppContainer
from ..dependencies import (
    get_container,
    require_auth,
    require_ready_user,
    resolve_auth_context,
)
from ..models import GenerationEvent
from ..services.events import event_payload

router = APIRouter(prefix="/api", tags=["events"])
logger = logging.getLogger(__name__)


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
    # FastAPI keeps yield-based dependencies alive until a StreamingResponse finishes. Resolve
    # authentication and materialize replay rows in a short local scope so an idle SSE client
    # does not retain a database session for the lifetime of the connection.
    with container.db.session_factory() as session:
        context = require_ready_user(require_auth(resolve_auth_context(request, session)))
        owner_id = context.user.id
        raw_token = context.raw_token
        replay = [
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
        ]

    return StreamingResponse(
        _event_stream(request, container, owner_id, raw_token, replay),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_stream(
    request: Request,
    container: AppContainer,
    owner_id: str,
    raw_token: str,
    replay: Sequence[dict[str, Any]],
) -> AsyncIterator[str]:
    for event in replay:
        yield _sse(event)
    try:
        async with container.broker.subscribe(owner_id) as queue:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                    yield _sse(item)
                except TimeoutError:
                    with container.db.session_factory() as auth_session:
                        if container.auth.resolve_session(auth_session, raw_token) is None:
                            return
                    yield ": keep-alive\n\n"
    except asyncio.CancelledError:
        # Cancellation is the expected Uvicorn shutdown path. The subscription context has
        # already removed its queue; re-raise so task cancellation is never swallowed.
        logger.info("event_stream_cancelled", extra={"actor_user_id": owner_id})
        raise


def _sse(item: dict) -> str:  # type: ignore[type-arg]
    event_id = item.get("id")
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {item.get('type', 'message')}")
    lines.append(f"data: {json.dumps(item, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"
