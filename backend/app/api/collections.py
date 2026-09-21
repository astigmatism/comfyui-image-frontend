from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..dependencies import (
    AuthContext,
    database_handler,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..schemas import Collection, CollectionCreate, CollectionUpdate

router = APIRouter(prefix="/api/collections", tags=["collections"])


@router.get("", response_model=list[Collection])
@database_handler
def list_collections(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> list[Collection]:
    return get_container(request).collections.list(session, owner_id=context.user.id)


@router.post("", response_model=Collection, status_code=status.HTTP_201_CREATED)
@database_handler
def create_collection(
    payload: CollectionCreate,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> Collection:
    return get_container(request).collections.create(
        session,
        owner_id=context.user.id,
        payload=payload,
    )


@router.patch("/{collection_id}", response_model=Collection)
@database_handler
def update_collection(
    collection_id: str,
    payload: CollectionUpdate,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> Collection:
    return get_container(request).collections.update(
        session,
        owner_id=context.user.id,
        collection_id=collection_id,
        payload=payload,
    )


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: str,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> Response:
    deleted_immediately = await get_container(request).collections.delete(
        owner_id=context.user.id,
        collection_id=collection_id,
    )
    return Response(
        status_code=(
            status.HTTP_204_NO_CONTENT if deleted_immediately else status.HTTP_202_ACCEPTED
        )
    )


@router.put("/{collection_id}/favorite", response_model=Collection)
@database_handler
def add_collection_favorite(
    collection_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> Collection:
    return get_container(request).collections.add_favorite(
        session, owner_id=context.user.id, collection_id=collection_id
    )


@router.delete("/{collection_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
@database_handler
def remove_collection_favorite(
    collection_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> Response:
    get_container(request).collections.remove_favorite(
        session, owner_id=context.user.id, collection_id=collection_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
