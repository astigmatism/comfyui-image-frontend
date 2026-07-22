from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .api import (
    admin,
    auth,
    events,
    favorites,
    generations,
    preferences,
    prompt_assistant,
    speech_to_text,
    uploads,
    workflows,
)
from .config import Settings, get_settings
from .container import AppContainer
from .db import run_migrations
from .errors import AppError

logger = logging.getLogger(__name__)
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


class JsonFormatter(logging.Formatter):
    @staticmethod
    def converter(timestamp: float | None) -> time.struct_time:
        return time.gmtime(timestamp)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "generation_id",
            "actor_user_id",
            "target_id",
            "action",
            "shutdown_duration_seconds",
            "consecutive_failures",
            "backoff_seconds",
            "exception_class",
            "event_type",
            "restart_count",
            "method",
            "route",
            "status_code",
            "duration_ms",
            "client_disconnected",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = (
                record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            )
            traceback_frames: list[dict[str, Any]] = []
            traceback = record.exc_info[2]
            while traceback is not None:
                code = traceback.tb_frame.f_code
                traceback_frames.append(
                    {
                        "file": code.co_filename,
                        "line": traceback.tb_lineno,
                        "function": code.co_name,
                    }
                )
                traceback = traceback.tb_next
            payload["traceback"] = traceback_frames
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _normalized_request_route(scope: Scope) -> str:
    """Return a route template without query strings or caller-provided path content."""

    route = scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    if str(scope.get("path", "")).startswith("/api"):
        return "/api/<unmatched>"
    return "/<static>"


def _safe_request_id(raw_request_id: str | None) -> str:
    if raw_request_id is not None and _SAFE_REQUEST_ID.fullmatch(raw_request_id):
        return raw_request_id
    return str(uuid.uuid4())


class RequestContextMiddleware:
    """Attach safe request diagnostics and log through the final response body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _safe_request_id(Headers(scope=scope).get("x-request-id"))
        scope.setdefault("state", {})["request_id"] = request_id
        started_at = time.monotonic()
        status_code = 500
        client_disconnected = False

        async def receive_with_disconnect() -> Message:
            nonlocal client_disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                client_disconnected = True
            return message

        async def send_with_headers(message: Message) -> None:
            nonlocal client_disconnected, status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                duration_ms = (time.monotonic() - started_at) * 1000
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["Server-Timing"] = f"app_ttfb;dur={duration_ms:.1f}"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                headers["X-Frame-Options"] = "DENY"
                headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; img-src 'self' blob: data:; "
                    "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                    "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
                )
            try:
                await send(message)
            except OSError:
                client_disconnected = True
                raise

        try:
            await self.app(scope, receive_with_disconnect, send_with_headers)
        except asyncio.CancelledError:
            status_code = 499
            client_disconnected = True
            raise
        finally:
            logger.info(
                "http_request_completed",
                extra={
                    "request_id": request_id,
                    "method": str(scope.get("method", "")),
                    "route": _normalized_request_route(scope),
                    "status_code": status_code,
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                    "client_disconnected": client_disconnected,
                },
            )


def create_app(
    settings: Settings | None = None,
    *,
    comfy_transport: httpx.AsyncBaseTransport | None = None,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    speech_to_text_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    container = AppContainer(
        settings,
        comfy_transport=comfy_transport,
        ollama_transport=ollama_transport,
        speech_to_text_transport=speech_to_text_transport,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await run_migrations(settings)
        # Alembic applies its CLI-oriented logging configuration while migrations run. Restore
        # the application's structured logger before startup continues and shutdown begins.
        configure_logging(settings.log_level)
        with container.db.session_factory() as session:
            container.auth.ensure_bootstrap_admin(session)
        try:
            await container.generation_eta.start()
            container.start_workflow_discovery()
            if settings.enable_background_worker:
                await container.worker.start()
            yield
        finally:
            await container.close()

    app = FastAPI(
        title=settings.app_title,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(RequestContextMiddleware)
    app.state.container = container

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "fields": exc.fields,
                    "details": exc.details,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields: dict[str, str] = {}
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", [])[1:]) or "request"
            fields[location] = str(error.get("msg", "Invalid value."))
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_failed",
                    "message": "The request could not be validated.",
                    "fields": fields,
                    "details": {},
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = "Resource was not found." if exc.status_code == 404 else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "not_found" if exc.status_code == 404 else "http_error",
                    "message": message,
                    "fields": {},
                    "details": {},
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_request_error",
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected server error occurred.",
                    "fields": {},
                    "details": {},
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.get("/api/health", tags=["operations"])
    def health() -> JSONResponse:
        database_healthy = container.db.healthcheck()
        worker = container.worker.health_snapshot()
        healthy = database_healthy and bool(worker["ready"])
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ok" if healthy else "degraded",
                "database": database_healthy,
                "worker": worker,
            },
        )

    app.include_router(auth.router)
    app.include_router(workflows.router)
    app.include_router(uploads.router)
    app.include_router(prompt_assistant.router)
    app.include_router(speech_to_text.router)
    app.include_router(preferences.router)
    app.include_router(generations.router)
    app.include_router(favorites.router)
    app.include_router(events.router)
    app.include_router(admin.router)

    frontend_dist = Path(settings.frontend_dist)
    if frontend_dist.is_dir() and (frontend_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:

        @app.get("/", include_in_schema=False)
        def frontend_not_built() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "frontend_not_built",
                        "message": (
                            "Frontend assets are not built. Run the documented build command."
                        ),
                    }
                },
            )

    return app
