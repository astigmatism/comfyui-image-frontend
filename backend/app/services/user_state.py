"""Short SQLite write transactions shared by controls, coordinator and dispatch."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import AppLock, AutoGeneration, GenerationPreparation, UserPreference
from .event_broker import EventBroker


def lock_user_state(session: Session) -> None:
    statement = insert(AppLock).values(key="user_state", integer_value=1)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[AppLock.key],
            set_={"integer_value": AppLock.integer_value + 1},
        )
    )


def require_manual_generation(session: Session, owner_id: str) -> None:
    """Call under the user-state lock, after looking up an idempotency receipt."""
    auto = session.get(AutoGeneration, owner_id)
    if auto and auto.enabled:
        raise AppError(
            "auto_generation_enabled",
            "Turn off auto generation before generating images manually.",
            status_code=409,
        )


async def notify_user(broker: EventBroker, owner_id: str, event_type: str) -> None:
    await broker.publish(owner_id, {"id": None, "type": event_type, "payload": {}})


def references_asset(value: Any, asset_id: str) -> bool:
    if isinstance(value, dict):
        return value.get("asset_id") == asset_id or any(
            references_asset(item, asset_id) for item in value.values()
        )
    if isinstance(value, list):
        return any(references_asset(item, asset_id) for item in value)
    return False


def asset_is_saved(session: Session, owner_id: str, asset_id: str) -> bool:
    preferences = session.get(UserPreference, owner_id)
    automation = session.get(AutoGeneration, owner_id)
    return bool(
        (preferences and references_asset(preferences.settings_json, asset_id))
        or (automation and references_asset(automation.snapshot_json, asset_id))
        or any(
            references_asset(value, asset_id)
            for value in session.scalars(
                select(GenerationPreparation.request_json).where(
                    GenerationPreparation.owner_id == owner_id,
                    GenerationPreparation.status.in_(["preparing", "refining", "ready"]),
                )
            )
        )
    )
