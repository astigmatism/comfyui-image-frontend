from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from ..dependencies import (
    AuthContext,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..schemas import FavoritePage, FavoriteSummary

router = APIRouter(prefix="/api", tags=["favorites"])


@router.get("/favorites", response_model=FavoritePage)
def list_favorites(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=60)] = 40,
) -> FavoritePage:
    service = get_container(request).generations
    return service.list_favorites(
        session,
        owner_id=context.user.id,
        cursor=cursor,
        limit=limit,
    )


@router.put("/generations/{generation_id}/favorite", response_model=FavoriteSummary)
def add_favorite(
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> FavoriteSummary:
    service = get_container(request).generations
    return service.add_favorite(
        session,
        owner_id=context.user.id,
        generation_id=generation_id,
    )


@router.delete("/generations/{generation_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> None:
    service = get_container(request).generations
    service.remove_favorite(
        session,
        owner_id=context.user.id,
        generation_id=generation_id,
    )
