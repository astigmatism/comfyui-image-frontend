from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import AuthContext, get_container, get_db, require_ready_csrf
from ..schemas import (
    GalleryDeleteResult,
    GallerySelection,
    GalleryTransfer,
    GalleryTransferResult,
)
from ..services.gallery import GalleryService

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


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
