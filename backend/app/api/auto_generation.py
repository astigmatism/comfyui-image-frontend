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
from ..models import AutoGeneration
from ..schemas import (
    AutoGenerationApply,
    AutoGenerationLimit,
    AutoGenerationResponse,
    AutoGenerationRetry,
    AutoGenerationUpdate,
)
from ..services.auto_generation import response

router = APIRouter(prefix="/api/auto-generation", tags=["auto generation"])


@router.get("", response_model=AutoGenerationResponse)
def get_state(
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> AutoGenerationResponse:
    return response(session.get(AutoGeneration, context.user.id), session)


@router.put("", response_model=AutoGenerationResponse)
async def set_state(
    payload: AutoGenerationUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> AutoGenerationResponse:
    return await get_container(request).automation.change(
        context.user.id,
        payload.expected_revision,
        enabled=payload.enabled,
        snapshot=payload.snapshot,
    )


@router.post("/apply", response_model=AutoGenerationResponse)
async def apply(
    payload: AutoGenerationApply,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> AutoGenerationResponse:
    return await get_container(request).automation.change(
        context.user.id, payload.expected_revision, snapshot=payload.snapshot
    )


@router.post("/retry", response_model=AutoGenerationResponse)
async def retry(
    payload: AutoGenerationRetry,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> AutoGenerationResponse:
    return await get_container(request).automation.change(
        context.user.id, payload.expected_revision, retry=True
    )


@router.post("/limit", response_model=AutoGenerationResponse)
async def limit(
    payload: AutoGenerationLimit,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> AutoGenerationResponse:
    return await get_container(request).automation.change(
        context.user.id, payload.expected_revision, reset_limit=True, limit=payload.max_generations
    )
