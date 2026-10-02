from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..blocking import run_blocking
from ..dependencies import (
    AuthContext,
    database_handler,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..models import PromptRerunRun
from ..schemas import (
    GalleryDeleteResult,
    GalleryFilters,
    GallerySelection,
    GallerySelectionScope,
    GalleryTransfer,
    GalleryTransferResult,
    GalleryViewItems,
    GenerationPage,
    PromptChanges,
    PromptGroupLookup,
    PromptGroupMembership,
    PromptRerunCreate,
    PromptRerunPreview,
    PromptRerunResult,
)
from ..services import prompt_rerun
from ..services.gallery import GalleryService
from ..services.prompt_groups import changes_for_group, lookup_groups, member_page
from ..services.user_state import lock_user_state, notify_user
from .gallery_filters import gallery_filters
from .generations import require_generation_protocol

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


@router.get("/items", response_model=GalleryViewItems)
@database_handler
def view_items(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    filters: Annotated[GalleryFilters, Depends(gallery_filters)],
    collection_id: str | None = None,
) -> GalleryViewItems:
    container = get_container(request)
    return GalleryService(container.generations, container.collections).view_items(
        session,
        context.user.id,
        GallerySelectionScope(
            collection_id=collection_id or None,
            **filters.model_dump(),
        ),
    )


@router.post("/prompt-groups/lookup", response_model=list[PromptGroupMembership])
@database_handler
def prompt_group_lookup(
    payload: PromptGroupLookup,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> list[PromptGroupMembership]:
    return lookup_groups(
        session, context.user.id, payload.collection_id, payload.generation_ids, filters=payload
    )


@router.get("/prompt-groups/{generation_id}/members", response_model=GenerationPage)
@database_handler
def prompt_group_members(
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    filters: Annotated[GalleryFilters, Depends(gallery_filters)],
    collection_id: str | None = None,
    cursor: str | None = None,
    selection: bool = False,
) -> GenerationPage:
    return member_page(
        session,
        get_container(request).generations,
        context.user.id,
        collection_id or None,
        generation_id,
        cursor=cursor,
        selection=selection,
        filters=filters,
    )


@router.get("/prompt-groups/{generation_id}/changes", response_model=PromptChanges)
@database_handler
def prompt_group_changes(
    generation_id: str,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    collection_id: str | None = None,
) -> PromptChanges:
    return changes_for_group(session, context.user.id, collection_id or None, generation_id)


@router.post("/favorite", response_model=GallerySelection)
@database_handler
def favorite_selection(
    payload: GallerySelection,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GallerySelection:
    container = get_container(request)
    return GalleryService(container.generations, container.collections).favorite(
        session, owner_id=context.user.id, payload=payload
    )


@router.post("/download")
@database_handler
def download_selection(
    payload: GallerySelection,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> FileResponse:
    container = get_container(request)
    path = GalleryService(container.generations, container.collections).download(
        session, owner_id=context.user.id, payload=payload
    )
    return FileResponse(
        path,
        media_type="application/zip",
        filename="gallery-selection.zip",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.post("/transfer", response_model=GalleryTransferResult)
@database_handler
def transfer_selection(
    payload: GalleryTransfer,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GalleryTransferResult:
    container = get_container(request)
    return GalleryService(container.generations, container.collections).transfer(
        session, owner_id=context.user.id, payload=payload
    )


@router.post("/prompt-rerun/preview", response_model=PromptRerunPreview)
@database_handler
def prompt_rerun_preview(
    payload: GallerySelection,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> PromptRerunPreview:
    container = get_container(request)
    return prompt_rerun.preview(
        session, container.generations, container.collections, context.user.id, payload
    )


@router.post("/prompt-rerun", response_model=PromptRerunResult, status_code=201)
async def create_prompt_rerun(
    payload: PromptRerunCreate,
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> PromptRerunResult:
    key = require_generation_protocol(request)
    container = get_container(request)
    result = await prompt_rerun.accept(
        container.generations,
        container.collections,
        context.user.id,
        payload,
        key,
        container.prompt_generation,
    )
    if result.run:
        response.status_code = 202
    return result


@router.get("/prompt-rerun")
@database_handler
def list_prompt_reruns(
    collection_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> list[dict[str, Any]]:
    get_container(request).collections.get_owned(session, context.user.id, collection_id)
    return [
        prompt_rerun.run_summary(session, run)
        for run in session.scalars(
            select(PromptRerunRun)
            .where(
                PromptRerunRun.owner_id == context.user.id,
                PromptRerunRun.collection_id == collection_id,
            )
            .order_by(PromptRerunRun.created_at)
        )
    ]


@router.get("/prompt-rerun/{identity}")
@database_handler
def get_prompt_rerun(
    identity: UUID,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> dict[str, Any]:
    return prompt_rerun.run_summary(
        session, prompt_rerun.require_run(session, context.user.id, str(identity))
    )


@router.post("/prompt-rerun/{identity}/stop")
async def stop_prompt_rerun(
    identity: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> dict[str, Any]:
    container = get_container(request)

    def transaction() -> dict[str, Any]:
        with container.db.session_factory() as session:
            lock_user_state(session)
            run = prompt_rerun.require_run(session, context.user.id, str(identity))
            if prompt_rerun.run_summary(session, run)["status"] == "processing":
                prompt_rerun.stop_in_session(session, run, "Stopped before image acceptance.")
            session.flush()
            result = prompt_rerun.run_summary(session, run)
            session.commit()
            return result

    result = await run_blocking(transaction)
    await notify_user(container.broker, context.user.id, "prompt_rerun.updated")
    return result


@router.post("/delete", response_model=GalleryDeleteResult)
async def delete_selection(
    payload: GallerySelection,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GalleryDeleteResult:
    container = get_container(request)
    return await GalleryService(container.generations, container.collections).delete(
        owner_id=context.user.id, payload=payload
    )
