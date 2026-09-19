from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import (
    AuthContext,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..errors import AppError
from ..models import Upload, UserPreference
from ..schemas import PreferenceResponse, PreferenceUpdate
from ..services.user_state import lock_user_state, notify_user

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


@router.get("", response_model=PreferenceResponse)
def get_preferences(
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> PreferenceResponse:
    return preference_response(session.get(UserPreference, context.user.id))


def preference_response(preference: UserPreference | None) -> PreferenceResponse:
    return PreferenceResponse(
        revision=preference.revision if preference else 0,
        settings_initialized=preference.settings_initialized if preference else False,
        settings=preference.settings_json if preference else {},
        gallery_scale=preference.gallery_scale if preference else 45,
        source_ratings=preference.source_ratings_json if preference else {},
        source_colors=preference.source_colors_json if preference else {},
        checkpoint_tiers=preference.checkpoint_tiers_json if preference else {},
    )


@router.put("", response_model=PreferenceResponse)
async def update_preferences(
    payload: PreferenceUpdate,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> PreferenceResponse:
    lock_user_state(session)
    preference = session.get(UserPreference, context.user.id)
    if preference is None:
        preference = UserPreference(
            user_id=context.user.id,
            gallery_scale=45,
            source_ratings_json={},
            source_colors_json={},
            checkpoint_tiers_json={},
        )
        session.add(preference)
        session.flush()
    if payload.import_if_empty and preference.settings_initialized:
        return preference_response(preference)
    if payload.expected_revision is not None and preference.revision != payload.expected_revision:
        raise AppError("settings_conflict", "Settings changed on another device.", status_code=409)
    if payload.settings is not None:
        if payload.expected_revision is None:
            raise AppError("revision_required", "A settings revision is required.", status_code=409)
        settings = payload.settings.model_dump(mode="json")

        def verify_assets(value: object) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("asset_id"), str):
                    asset = session.get(Upload, value["asset_id"])
                    if asset is None or asset.owner_id != context.user.id:
                        raise AppError(
                            "upload_invalid", "A saved image is unavailable.", status_code=422
                        )
                for item in value.values():
                    verify_assets(item)
            elif isinstance(value, list):
                for item in value:
                    verify_assets(item)

        verify_assets(settings)
        preference.settings_json = settings
        preference.settings_initialized = True
    if payload.gallery_scale is not None:
        preference.gallery_scale = payload.gallery_scale
    if payload.source_ratings is not None:
        preference.source_ratings_json = dict(payload.source_ratings)
    if payload.source_colors is not None:
        preference.source_colors_json = dict(payload.source_colors)
    if payload.checkpoint_tiers is not None:
        preference.checkpoint_tiers_json = {
            source_key: {
                parameter_id: {tier: list(choices) for tier, choices in tiers.items()}
                for parameter_id, tiers in selectors.items()
            }
            for source_key, selectors in payload.checkpoint_tiers.items()
        }
    preference.revision += 1
    session.commit()
    await notify_user(get_container(request).broker, context.user.id, "preferences.updated")
    return preference_response(preference)
