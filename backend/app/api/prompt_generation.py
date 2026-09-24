from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from ..blocking import run_blocking
from ..dependencies import AuthContext, get_container, require_ready_csrf, require_ready_user
from ..schemas import GenerationPreparationCreate, PromptGenerationCreate
from .generations import require_generation_protocol

router = APIRouter(prefix="/api", tags=["prompt-generation"])


@router.post("/prompt-generations", status_code=202)
async def generate_prompt(
    payload: PromptGenerationCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> dict[str, Any]:
    return await get_container(request).prompt_generation.accept(
        context.user.id, payload, require_generation_protocol(request)
    )


@router.get("/prompt-generations/{identity}")
async def get_prompt(
    identity: UUID, request: Request, context: Annotated[AuthContext, Depends(require_ready_user)]
) -> dict[str, Any]:
    return await run_blocking(
        get_container(request).prompt_generation.get, context.user.id, str(identity)
    )


@router.post("/generation-preparations", status_code=202)
async def prepare_images(
    payload: GenerationPreparationCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> dict[str, Any]:
    return await get_container(request).prompt_generation.accept(
        context.user.id, payload, require_generation_protocol(request)
    )


@router.get("/generation-preparations/{identity}")
async def get_preparation(
    identity: UUID, request: Request, context: Annotated[AuthContext, Depends(require_ready_user)]
) -> dict[str, Any]:
    return await run_blocking(
        get_container(request).prompt_generation.get,
        context.user.id,
        str(identity),
        preparation=True,
    )
