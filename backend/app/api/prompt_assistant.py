from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..dependencies import (
    AuthContext,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..errors import AppError
from ..models import PromptAssistantRun, ServiceHealth
from ..schemas import PromptAssistantStatus, PromptComposeRequest, PromptComposeResponse
from ..services.ollama import MAX_CREATE_EXCLUSIONS

router = APIRouter(prefix="/api/prompt-assistant", tags=["prompt-assistant"])
PROMPT_HISTORY_SCAN_LIMIT = 64


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
    if payload.mode == "refine" and not payload.prompt.strip():
        raise AppError(
            "prompt_required",
            "Refine mode requires a current prompt.",
            fields={"prompt": "Enter a prompt first."},
        )
    if not payload.creative_direction.strip() and payload.mode == "create":
        raise AppError(
            "direction_required",
            "Create mode requires a creative direction.",
            fields={"creative_direction": "Describe the intended image."},
        )
    container = get_container(request)
    excluded_prompts: list[str] = []
    if payload.mode == "create":
        with container.db.session_factory() as history_session:
            recent_runs = history_session.scalars(
                select(PromptAssistantRun)
                .where(
                    PromptAssistantRun.owner_id == context.user.id,
                    PromptAssistantRun.ollama_output.is_not(None),
                )
                .order_by(PromptAssistantRun.created_at.desc(), PromptAssistantRun.id.desc())
                .limit(PROMPT_HISTORY_SCAN_LIMIT)
            ).all()
        excluded_prompts = [
            run.ollama_output
            for run in recent_runs
            if run.mode == "create"
            and run.creative_direction == payload.creative_direction
            and run.ollama_output
        ][:MAX_CREATE_EXCLUSIONS]
    try:
        result = await container.ollama.compose(
            mode=payload.mode,
            prompt=payload.prompt,
            direction=payload.creative_direction,
            excluded_prompts=excluded_prompts,
        )
    except AppError as exc:
        session.add(
            PromptAssistantRun(
                owner_id=context.user.id,
                mode=payload.mode,
                prompt_before=payload.prompt,
                creative_direction=payload.creative_direction,
                template_version=container.settings.prompt_template_version,
                error_code=exc.code,
                error_message=exc.message,
            )
        )
        session.commit()
        raise
    run = PromptAssistantRun(
        owner_id=context.user.id,
        mode=payload.mode,
        prompt_before=payload.prompt,
        creative_direction=payload.creative_direction,
        model_name=result.model,
        template_version=container.settings.prompt_template_version,
        ollama_output=result.prompt,
        raw_response_json=result.raw_response,
        duration_ms=result.duration_ms,
    )
    session.add(run)
    session.commit()
    return PromptComposeResponse(
        composition_id=run.id,
        prompt=result.prompt,
        model=result.model,
        template_version=run.template_version,
    )
