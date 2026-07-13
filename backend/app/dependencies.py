from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Iterator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from .errors import AppError
from .models import Session as UserSession, User, UserRole
from .security import keyed_hash, secure_compare


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: UserSession
    raw_token: str


def get_container(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.container


def get_db(request: Request) -> Iterator[Session]:
    container = get_container(request)
    with container.db.session_factory() as session:
        yield session


def optional_auth(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> AuthContext | None:
    container = get_container(request)
    raw = request.cookies.get(container.settings.session_cookie_name)
    resolved = container.auth.resolve_session(session, raw)
    if resolved is None or raw is None:
        return None
    user, stored = resolved
    return AuthContext(user=user, session=stored, raw_token=raw)


def require_auth(context: Annotated[AuthContext | None, Depends(optional_auth)]) -> AuthContext:
    if context is None:
        raise AppError("authentication_required", "Sign in is required.", status_code=401)
    return context


def require_ready_user(context: Annotated[AuthContext, Depends(require_auth)]) -> AuthContext:
    if context.user.must_change_password:
        raise AppError(
            "password_change_required",
            "You must change the temporary password before continuing.",
            status_code=403,
        )
    return context


def require_admin(context: Annotated[AuthContext, Depends(require_ready_user)]) -> AuthContext:
    if context.user.role != UserRole.ADMIN:
        raise AppError("forbidden", "Administrator access is required.", status_code=403)
    return context


def require_csrf(
    context: Annotated[AuthContext, Depends(require_auth)],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> AuthContext:
    if not x_csrf_token or not secure_compare(context.session.csrf_token, x_csrf_token):
        raise AppError("csrf_failed", "Request verification failed.", status_code=403)
    return context


def require_ready_csrf(
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> AuthContext:
    if context.user.must_change_password:
        raise AppError(
            "password_change_required",
            "You must change the temporary password before continuing.",
            status_code=403,
        )
    return context


def current_session_hash(context: AuthContext, request: Request) -> str:
    return keyed_hash(context.raw_token, get_container(request).settings)
