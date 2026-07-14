from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from ..dependencies import (
    AuthContext,
    get_container,
    get_db,
    optional_auth,
    require_csrf,
)
from ..errors import AppError
from ..schemas import ChangePasswordRequest, LoginRequest, SessionInfo, UserPublic
from ..security import new_login_csrf, secure_compare, verify_login_csrf

router = APIRouter(prefix="/api/auth", tags=["authentication"])
LOGIN_CSRF_COOKIE = "cif_login_csrf"


def _user_public(context: AuthContext) -> UserPublic:
    user = context.user
    return UserPublic(
        id=user.id,
        username=user.username,
        role=user.role.value,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
    )


@router.get("/session", response_model=SessionInfo)
def session_info(
    request: Request,
    response: Response,
    context: Annotated[AuthContext | None, Depends(optional_auth)],
) -> SessionInfo:
    container = get_container(request)
    if context is not None:
        response.delete_cookie(LOGIN_CSRF_COOKIE, path="/")
        return SessionInfo(
            authenticated=True,
            user=_user_public(context),
            csrf_token=context.session.csrf_token,
            app_title=container.settings.app_title,
        )
    token = new_login_csrf(container.settings)
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        token,
        httponly=True,
        secure=container.settings.cookie_secure,
        samesite=container.settings.cookie_samesite,
        path="/",
        max_age=600,
    )
    return SessionInfo(
        authenticated=False,
        csrf_token=token,
        app_title=container.settings.app_title,
    )


@router.post("/login", response_model=SessionInfo)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> SessionInfo:
    container = get_container(request)
    cookie_token = request.cookies.get(LOGIN_CSRF_COOKIE)
    if (
        not x_csrf_token
        or not cookie_token
        or not secure_compare(x_csrf_token, cookie_token)
        or not verify_login_csrf(cookie_token, container.settings)
    ):
        raise AppError("csrf_failed", "Request verification failed.", status_code=403)
    client_ip = request.client.host if request.client else "unknown"
    user, raw_token, stored = container.auth.authenticate(
        session,
        username=payload.username,
        password=payload.password,
        client_ip=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    response.set_cookie(
        container.settings.session_cookie_name,
        raw_token,
        httponly=True,
        secure=container.settings.cookie_secure,
        samesite=container.settings.cookie_samesite,
        path="/",
        max_age=container.settings.session_ttl_hours * 3600,
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/")
    context = AuthContext(user=user, session=stored, raw_token=raw_token)
    return SessionInfo(
        authenticated=True,
        user=_user_public(context),
        csrf_token=stored.csrf_token,
        app_title=container.settings.app_title,
    )


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> None:
    container = get_container(request)
    container.auth.logout(session, context.raw_token)
    response.delete_cookie(container.settings.session_cookie_name, path="/")


@router.post("/password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> None:
    container = get_container(request)
    container.auth.change_password(
        session,
        user=context.user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        current_session_hash=context.session.id_hash,
    )
