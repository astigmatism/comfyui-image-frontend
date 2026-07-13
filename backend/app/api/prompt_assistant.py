from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import AuthContext, get_container, get_db, require_ready_csrf, require_ready_user
from ..errors import AppError
from ..models import PromptAssistantRun
from ..schemas import PromptAssistantStatus, PromptComposeRequest, PromptComposeResponse

router = APIRouter(prefix="/api/prompt-assistant", tags=["prompt-assistant"])


@router.get("/status", response_model=PromptAssistantStatus)
async def status(
    request: Request,
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> PromptAssistantStatus:
    available, message = await get_container(request).ollama.status()
    return PromptAssistantStatus(available=available, message=message)


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
    try:
        result = await container.ollama.compose(
            mode=payload.mode,
            prompt=payload.prompt,
            direction=payload.creative_direction,
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
