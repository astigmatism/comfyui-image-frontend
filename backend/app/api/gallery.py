from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..dependencies import AuthContext, get_container, get_db, require_ready_csrf
from ..schemas import (
    GalleryDeleteResult,
    GallerySelection,
    GalleryTransfer,
    GalleryTransferResult,
)
from ..services.gallery import GalleryService

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


@router.post("/favorite", response_model=GallerySelection)
def favorite_selection(
    payload: GallerySelection,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GallerySelection:
    container = get_container(request)
    return GalleryService(container.generations, container.collections).favorite(
        session, owner_id=context.user.id, payload=payload
    )


@router.post("/download")
def download_selection(
    payload: GallerySelection,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
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
def transfer_selection(
    payload: GalleryTransfer,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
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
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> GalleryDeleteResult:
    container = get_container(request)
    return await GalleryService(container.generations, container.collections).delete(
        session, owner_id=context.user.id, payload=payload
    )
