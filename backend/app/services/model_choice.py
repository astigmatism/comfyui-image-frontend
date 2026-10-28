"""Reading an account's Creative Direction model choice.

The choice lives in the account's saved settings blob, which the control panel writes through the
preferences route with a revision. Nothing here talks to the LLM Router: the catalog the event
subscriber last received is compared with the choice where the choice is used, so this stays a
plain row read.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..domain.model_choice import normalize_assistant_model
from ..models import UserPreference


def pinned_assistant_service(session: Session, owner_id: str) -> str | None:
    """The model this account chose for Creative Direction, or ``None`` for the automatic choice."""

    preference = session.get(UserPreference, owner_id)
    if preference is None:
        return None
    settings = preference.settings_json or {}
    if not isinstance(settings, dict):
        return None
    return normalize_assistant_model(settings.get("assistant_model"))
