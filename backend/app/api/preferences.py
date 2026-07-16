from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..dependencies import AuthContext, get_db, require_ready_csrf, require_ready_user
from ..models import UserPreference
from ..schemas import PreferenceResponse, PreferenceUpdate

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


@router.get("", response_model=PreferenceResponse)
def get_preferences(
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> PreferenceResponse:
    preference = session.get(UserPreference, context.user.id)
    return PreferenceResponse(
        gallery_scale=preference.gallery_scale if preference else 45,
        source_ratings=preference.source_ratings_json if preference else {},
    )


@router.put("", response_model=PreferenceResponse)
def update_preferences(
    payload: PreferenceUpdate,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> PreferenceResponse:
    preference = session.get(UserPreference, context.user.id)
    if preference is None:
        preference = UserPreference(
            user_id=context.user.id,
            gallery_scale=45,
            source_ratings_json={},
        )
        session.add(preference)
    if payload.gallery_scale is not None:
        preference.gallery_scale = payload.gallery_scale
    if payload.source_ratings is not None:
        preference.source_ratings_json = dict(payload.source_ratings)
    session.commit()
    return PreferenceResponse(
        gallery_scale=preference.gallery_scale,
        source_ratings=preference.source_ratings_json,
    )
