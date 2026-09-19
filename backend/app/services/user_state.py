"""Short SQLite write transactions shared by controls, coordinator and dispatch."""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ..models import AppLock, AutoGeneration, UserPreference
from .event_broker import EventBroker


def lock_user_state(session: Session) -> None:
    statement = insert(AppLock).values(key="user_state", integer_value=1)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[AppLock.key],
            set_={"integer_value": AppLock.integer_value + 1},
        )
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
    )
