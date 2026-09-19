from __future__ import annotations

from datetime import UTC, datetime
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
from ..models import ServiceHealth
from ..schemas import PromptAssistantStatus, PromptComposeRequest, PromptComposeResponse
from ..services.prompt_assistant import compose_prompt

router = APIRouter(prefix="/api/prompt-assistant", tags=["prompt-assistant"])


@router.get("/status", response_model=PromptAssistantStatus)
def status(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> PromptAssistantStatus:
    container = get_container(request)
    if not container.settings.ollama_base_url:
        return PromptAssistantStatus(
            available=False,
            message="Prompt Assistant is not configured.",
        )

    health = session.get(ServiceHealth, "ollama")
    if health is None:
        return PromptAssistantStatus(
            available=False,
            message="Prompt Assistant availability is still being checked.",
        )

    checked_at = health.checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    stale_after_seconds = max(
        30.0,
        float(container.settings.external_health_interval_seconds) * 3,
    )
    if (datetime.now(UTC) - checked_at).total_seconds() > stale_after_seconds:
        return PromptAssistantStatus(
            available=False,
            message="Prompt Assistant health information is stale; availability is being checked.",
        )

    return PromptAssistantStatus(
        available=health.available,
        message=(
            None
            if health.available
            else health.message
            or "Prompt Assistant is temporarily unavailable; manual prompting still works."
        ),
    )


@router.post("/compose", response_model=PromptComposeResponse)
async def compose(
    payload: PromptComposeRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> PromptComposeResponse:
    return await compose_prompt(get_container(request), session, context.user.id, payload)
