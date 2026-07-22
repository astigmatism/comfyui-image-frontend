from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..dependencies import AuthContext, get_container, get_db, require_admin, require_ready_csrf
from ..errors import AppError
from ..models import User, UserRole
from ..schemas import (
    AdminDiagnostic,
    CreateUserRequest,
    ResetPasswordRequest,
    UserPublic,
)

router = APIRouter(prefix="/api/admin", tags=["administration"])


def _admin_csrf(context: Annotated[AuthContext, Depends(require_ready_csrf)]) -> AuthContext:
    if context.user.role != UserRole.ADMIN:
        raise AppError("forbidden", "Administrator access is required.", status_code=403)
    return context


@router.get("/users", response_model=list[UserPublic])
def list_users(
    session: Annotated[Session, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_admin)],
) -> list[UserPublic]:
    users = list(session.scalars(select(User).order_by(User.username_normalized)))
    return [
        UserPublic(
            id=user.id,
            username=user.username,
            role=user.role.value,
            must_change_password=user.must_change_password,
            created_at=user.created_at,
        )
        for user in users
    ]


@router.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(_admin_csrf)],
) -> UserPublic:
    user = get_container(request).auth.create_user(
        session,
        actor=context.user,
        username=payload.username,
        temporary_password=payload.temporary_password,
    )
    return UserPublic(
        id=user.id,
        username=user.username,
        role=user.role.value,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
    )


@router.post("/users/{user_id}/reset-password", status_code=204)
def reset_password(
    user_id: str,
    payload: ResetPasswordRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(_admin_csrf)],
) -> None:
    target = session.get(User, user_id)
    if target is None:
        raise AppError("not_found", "User was not found.", status_code=404)
    get_container(request).auth.reset_password(
        session,
        actor=context.user,
        target=target,
        temporary_password=payload.temporary_password,
    )


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(_admin_csrf)],
) -> None:
    actor_id = context.user.id
    session.close()
    await get_container(request).user_deletion.delete_user(actor_id=actor_id, target_id=user_id)


@router.post("/workflows/refresh", response_model=list[AdminDiagnostic])
async def refresh_workflows(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(_admin_csrf)],
) -> list[AdminDiagnostic]:
    del context
    session.close()
    diagnostics = await get_container(request).registry.refresh()
    return [
        AdminDiagnostic(
            basename=item.basename,
            accepted=item.accepted,
            workflow_id=item.workflow_id,
            workflow_version=item.workflow_version,
            code=item.code,
            message=item.message,
            details=item.details_json,
            checked_at=item.checked_at,
        )
        for item in diagnostics
    ]


@router.get("/workflows/diagnostics", response_model=list[AdminDiagnostic])
def workflow_diagnostics(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_admin)],
) -> list[AdminDiagnostic]:
    return [
        AdminDiagnostic(
            basename=item.basename,
            accepted=item.accepted,
            workflow_id=item.workflow_id,
            workflow_version=item.workflow_version,
            code=item.code,
            message=item.message,
            details=item.details_json,
            checked_at=item.checked_at,
        )
        for item in get_container(request).registry.diagnostics(session)
    ]
