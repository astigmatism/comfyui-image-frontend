from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import FileResponse
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
from ..models import Artifact
from ..schemas import (
    GenerationCreate,
    GenerationDetail,
    GenerationPage,
    GenerationSummary,
    RecallResponse,
    ValidationResult,
)

router = APIRouter(prefix="/api", tags=["generations"])


@router.post("/generations/validate", response_model=ValidationResult)
def validate_generation(
    payload: GenerationCreate,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> ValidationResult:
    return get_container(request).generations.validate(session, user=context.user, request=payload)


@router.post(
    "/generations",
    response_model=GenerationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_generation(
    payload: GenerationCreate,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GenerationSummary:
    return await get_container(request).generations.accept(
        session, user=context.user, request=payload
    )


@router.get("/generations", response_model=GenerationPage)
def list_generations(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=60)] = 24,
) -> GenerationPage:
    return get_container(request).generations.list_page(
        session, owner_id=context.user.id, cursor=cursor, limit=limit
    )


@router.get("/generations/{generation_id}", response_model=GenerationDetail)
def get_generation(
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> GenerationDetail:
    service = get_container(request).generations
    generation = service.get_owned(session, context.user.id, generation_id)
    return service.detail(session, generation)


@router.get("/generations/{generation_id}/recall", response_model=RecallResponse)
def recall_generation(
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> RecallResponse:
    service = get_container(request).generations
    generation = service.get_owned(session, context.user.id, generation_id)
    return service.recall(session, generation)


@router.post("/generations/{generation_id}/cancel", response_model=GenerationSummary)
async def cancel_generation(
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GenerationSummary:
    service = get_container(request).generations
    generation = service.get_owned(session, context.user.id, generation_id)
    return await service.cancel(session, generation)


@router.delete("/generations/{generation_id}", status_code=204)
async def delete_generation(
    generation_id: str,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> None:
    service = get_container(request).generations
    generation = service.get_owned(session, context.user.id, generation_id)
    deleted = await service.request_delete(session, generation)
    if not deleted:
        response.status_code = status.HTTP_202_ACCEPTED


@router.get("/artifacts/{artifact_id}/content")
def artifact_content(
    artifact_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> FileResponse:
    artifact = session.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.owner_id == context.user.id,
        )
    )
    if artifact is None:
        raise AppError("not_found", "Artifact was not found.", status_code=404)
    path = get_container(request).assets.open(artifact.storage_path)
    return FileResponse(
        path,
        media_type=artifact.mime_type,
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/artifacts/{artifact_id}/thumbnail")
def artifact_thumbnail(
    artifact_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> FileResponse:
    artifact = session.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.owner_id == context.user.id,
        )
    )
    if artifact is None or not artifact.thumbnail_path:
        raise AppError("not_found", "Thumbnail was not found.", status_code=404)
    path = get_container(request).assets.open(artifact.thumbnail_path)
    return FileResponse(
        path,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
