from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from inspect import Parameter, signature
from typing import Annotated, Any, cast, get_type_hints

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from .blocking import run_blocking
from .container import AppContainer
from .errors import AppError
from .models import Session as UserSession
from .models import User, UserRole
from .security import keyed_hash, secure_compare


@dataclass(frozen=True)
class AuthUser:
    id: str
    username: str
    role: UserRole
    must_change_password: bool
    created_at: datetime


@dataclass(frozen=True)
class AuthSession:
    id_hash: str
    csrf_token: str


@dataclass(frozen=True)
class AuthContext:
    user: AuthUser
    session: AuthSession
    raw_token: str

    @classmethod
    def from_models(cls, user: User, session: UserSession, raw_token: str) -> AuthContext:
        return cls(
            AuthUser(user.id, user.username, user.role, user.must_change_password, user.created_at),
            AuthSession(session.id_hash, session.csrf_token),
            raw_token,
        )


def get_container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


def get_db(request: Request) -> Iterator[Session]:
    container = get_container(request)
    with container.db.session_factory() as session:
        yield session


def database_handler(operation: Callable[..., Any]) -> Callable[..., Any]:
    """Run a synchronous route with a session owned entirely by one worker.

    FastAPI resolves authentication and other dependencies before this wrapper.
    Only materialized response data (or a prepared streaming response) escapes.
    The public signature hides the internal session and injects Request when needed.
    """
    original = signature(operation)
    hints = get_type_hints(operation, include_extras=True)
    parameters = [
        parameter.replace(annotation=hints.get(name, parameter.annotation))
        for name, parameter in original.parameters.items()
        if name != "session"
    ]
    needs_request = "request" not in original.parameters
    if needs_request:
        parameters.append(Parameter("request", Parameter.KEYWORD_ONLY, annotation=Request))

    @wraps(operation)
    async def execute(**kwargs: Any) -> Any:
        request = kwargs["request"]
        container = get_container(request)
        if needs_request:
            kwargs.pop("request")

        def transaction() -> Any:
            with container.db.session_factory() as session:
                return operation(session=session, **kwargs)

        return await run_blocking(transaction)

    result_type = hints.get("return", original.return_annotation)
    execute.__signature__ = original.replace(  # type: ignore[attr-defined]
        parameters=parameters, return_annotation=None if result_type is type(None) else result_type
    )
    return execute


async def optional_auth(request: Request) -> AuthContext | None:
    container = get_container(request)
    raw = request.cookies.get(container.settings.session_cookie_name)
    if not raw:
        return None

    def resolve() -> AuthContext | None:
        with container.db.session_factory() as session:
            return resolve_auth_context(request, session)

    return await run_blocking(resolve)


def resolve_auth_context(request: Request, session: Session) -> AuthContext | None:
    container = get_container(request)
    raw = request.cookies.get(container.settings.session_cookie_name)
    resolved = container.auth.resolve_session(session, raw)
    if resolved is None or raw is None:
        return None
    return AuthContext.from_models(*resolved, raw)


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
