from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models import Generation, GenerationEvent
from .event_broker import EventBroker


def add_generation_event(
    session: Session,
    generation: Generation,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> GenerationEvent:
    event = GenerationEvent(
        generation_id=generation.id,
        owner_id=generation.owner_id,
        event_type=event_type,
        payload_json=payload or {},
        created_at=datetime.now(UTC),
    )
    session.add(event)
    session.flush()
    return event


def event_payload(event: GenerationEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.event_type,
        "generation_id": event.generation_id,
        "created_at": event.created_at.isoformat(),
        "payload": event.payload_json,
    }


async def publish_event(broker: EventBroker, event: GenerationEvent) -> None:
    await broker.publish(event.owner_id, event_payload(event))
