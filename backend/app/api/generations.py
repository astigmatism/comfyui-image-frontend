from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..blocking import run_blocking
from ..dependencies import (
    AuthContext,
    database_handler,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..errors import AppError
from ..file_response import StoredFileResponse as FileResponse
from ..models import Artifact, User
from ..schemas import (
    GenerationActivity,
    GenerationBatchCreate,
    GenerationBatchResult,
    GenerationCreate,
    GenerationDetail,
    GenerationMove,
    GenerationPage,
    GenerationSummary,
    RecallResponse,
    ValidationResult,
)
from ..services import submissions
from ..services.generation_activity import activity_snapshot

router = APIRouter(prefix="/api", tags=["generations"])


@router.post("/generations/validate", response_model=ValidationResult)
@database_handler
def validate_generation(
    payload: GenerationCreate,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> ValidationResult:
    return get_container(request).generations.validate(
        session, user=_load_user(session, context.user.id), request=payload
    )


@router.post(
    "/generations",
    response_model=GenerationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_generation(
    payload: GenerationCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GenerationSummary:
    key = require_generation_protocol(request)
    result = await submissions.accept(
        get_container(request).generations, context.user.id, payload, key
    )
    assert isinstance(result, GenerationSummary)
    return result


@router.post("/generations/batch", response_model=GenerationBatchResult, status_code=201)
async def create_generation_batch(
    payload: GenerationBatchCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GenerationBatchResult:
    key = require_generation_protocol(request)
    result = await submissions.accept(
        get_container(request).generations, context.user.id, payload, key
    )
    assert isinstance(result, GenerationBatchResult)
    return result


@router.get("/generation-activity", response_model=GenerationActivity)
@database_handler
def generation_activity(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> GenerationActivity:
    snapshot = activity_snapshot(session, context.user.id)
    return GenerationActivity.model_validate(
        {
            **snapshot.model_dump(),
            **get_container(request).activity_estimator.project(session, context.user.id),
        }
    )


@router.get("/generations", response_model=GenerationPage)
@database_handler
def list_generations(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=60)] = 24,
    collection_id: Annotated[str | None, Query()] = None,
) -> GenerationPage:
    return get_container(request).generations.list_page(
        session,
        owner_id=context.user.id,
        cursor=cursor,
        limit=limit,
        collection_id=collection_id or None,
        collection_scoped=collection_id is not None,
    )


@router.post("/generations/{generation_id}/move", response_model=GenerationSummary)
@database_handler
def move_generation(
    generation_id: str,
    payload: GenerationMove,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GenerationSummary:
    return get_container(request).generations.move(
        session,
        owner_id=context.user.id,
        generation_id=generation_id,
        payload=payload,
    )


@router.get("/generations/{generation_id}", response_model=GenerationDetail)
@database_handler
def get_generation(
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> GenerationDetail:
    service = get_container(request).generations
    generation = service.get_owned(session, context.user.id, generation_id)
    return service.detail(session, generation)


@router.get("/generations/{generation_id}/recall", response_model=RecallResponse)
@database_handler
def recall_generation(
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> RecallResponse:
    service = get_container(request).generations
    generation = service.get_owned(session, context.user.id, generation_id)
    return service.recall(session, generation)


@router.post(
    "/generations/{generation_id}/cancel",
    response_model=GenerationSummary,
    responses={
        status.HTTP_204_NO_CONTENT: {
            "description": "The queued generation was cancelled and deleted."
        }
    },
)
async def cancel_generation(
    generation_id: str,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GenerationSummary | Response:
    service = get_container(request).generations
    result = await service.cancel_owned(context.user.id, generation_id)
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@router.delete("/generations/{generation_id}", status_code=204)
async def delete_generation(
    generation_id: str,
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> None:
    service = get_container(request).generations
    deleted = await service.delete_owned(context.user.id, generation_id)
    if not deleted:
        response.status_code = status.HTTP_202_ACCEPTED


@router.get("/artifacts/{artifact_id}/content")
@database_handler
def artifact_content(
    artifact_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
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
    media_type = artifact.mime_type
    session.close()
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/artifacts/{artifact_id}/thumbnail")
@database_handler
def artifact_thumbnail(
    artifact_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
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
    session.close()
    return FileResponse(
        path,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


def require_generation_protocol(request: Request) -> str:
    if request.headers.get("X-CIF-Generation-Protocol") != "3":
        raise AppError(
            "client_reload_required",
            "Reload this page before generating. Auto generation is now managed by the server.",
            status_code=409,
        )

    try:
        return str(UUID(request.headers.get("Idempotency-Key", "")))
    except ValueError as exc:
        raise AppError(
            "idempotency_key_required",
            "A UUID submission ID is required. Reload this page.",
            status_code=400,
        ) from exc


@router.get("/generation-submissions/{key}")
async def submission_status(
    key: UUID, request: Request, context: Annotated[AuthContext, Depends(require_ready_user)]
) -> dict[str, Any]:
    return await run_blocking(
        submissions.lookup, get_container(request).generations, context.user.id, str(key)
    )


def _load_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise AppError("authentication_required", "Sign in is required.", status_code=401)
    return user
