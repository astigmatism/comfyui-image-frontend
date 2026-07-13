from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..dependencies import AuthContext, get_container, get_db, require_ready_user
from ..models import GenerationEvent
from ..services.events import event_payload

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
async def events(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    last_event_id_query: Annotated[int | None, Query(alias="last_event_id", ge=0)] = None,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    container = get_container(request)
    try:
        header_id = int(last_event_id_header) if last_event_id_header else 0
    except ValueError:
        header_id = 0
    after_id = max(last_event_id_query or 0, header_id)
    replay = list(
        session.scalars(
            select(GenerationEvent)
            .where(
                GenerationEvent.owner_id == context.user.id,
                GenerationEvent.id > after_id,
            )
            .order_by(GenerationEvent.id)
            .limit(1000)
        )
    )
    raw_token = context.raw_token

    async def stream() -> AsyncIterator[str]:
        for event in replay:
            yield _sse(event_payload(event))
        async with container.broker.subscribe(context.user.id) as queue:
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

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(item: dict) -> str:  # type: ignore[type-arg]
    event_id = item.get("id")
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {item.get('type', 'message')}")
    lines.append(f"data: {json.dumps(item, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"
