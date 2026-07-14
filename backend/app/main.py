from __future__ import annotations

import json
import logging
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
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import (
    admin,
    auth,
    events,
    favorites,
    generations,
    preferences,
    prompt_assistant,
    uploads,
    workflows,
)
from .config import Settings, get_settings
from .container import AppContainer
from .db import run_migrations
from .errors import AppError

logger = logging.getLogger(__name__)


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
        for key in ("request_id", "generation_id", "actor_user_id", "target_id", "action"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = (
                record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            )
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(
    settings: Settings | None = None,
    *,
    comfy_transport: httpx.AsyncBaseTransport | None = None,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    container = AppContainer(
        settings,
        comfy_transport=comfy_transport,
        ollama_transport=ollama_transport,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await run_migrations(settings)
        with container.db.session_factory() as session:
            container.auth.ensure_bootstrap_admin(session)
        await container.registry.refresh()
        if settings.enable_background_worker:
            await container.worker.start()
        try:
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
    app.state.container = container

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:128]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

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
        healthy = container.db.healthcheck()
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "degraded", "database": healthy},
        )

    app.include_router(auth.router)
    app.include_router(workflows.router)
    app.include_router(uploads.router)
    app.include_router(prompt_assistant.router)
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
