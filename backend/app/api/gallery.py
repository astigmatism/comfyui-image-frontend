from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..dependencies import (
    AuthContext,
    database_handler,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..schemas import (
    GalleryDeleteResult,
    GallerySelection,
    GallerySelectionScope,
    GalleryTransfer,
    GalleryTransferResult,
    GalleryViewItems,
    GenerationPage,
    PromptChanges,
    PromptGroupLookup,
    PromptGroupMembership,
)
from ..services.gallery import GalleryService
from ..services.prompt_groups import changes_for_group, lookup_groups, member_page

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


@router.get("/items", response_model=GalleryViewItems)
@database_handler
def view_items(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    collection_id: str | None = None,
    favorites_only: bool = False,
) -> GalleryViewItems:
    container = get_container(request)
    return GalleryService(container.generations, container.collections).view_items(
        session,
        context.user.id,
        GallerySelectionScope(collection_id=collection_id or None, favorites_only=favorites_only),
    )


@router.post("/prompt-groups/lookup", response_model=list[PromptGroupMembership])
@database_handler
def prompt_group_lookup(
    payload: PromptGroupLookup,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> list[PromptGroupMembership]:
    return lookup_groups(session, context.user.id, payload.collection_id, payload.generation_ids)


@router.get("/prompt-groups/{generation_id}/members", response_model=GenerationPage)
@database_handler
def prompt_group_members(
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
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
    session.close()
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
