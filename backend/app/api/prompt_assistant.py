from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..blocking import run_blocking
from ..dependencies import (
    AuthContext,
    database_handler,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..models import ServiceHealth
from ..schemas import (
    ExpectationCheckCreate,
    ExpectationCheckLatest,
    ExpectationCheckPublic,
    PromptAssistantRouterStatus,
    PromptAssistantStatus,
    PromptComposeRequest,
    PromptComposeResponse,
)
from ..services.prompt_assistant import compose_prompt
from .generations import require_generation_protocol

router = APIRouter(prefix="/api/prompt-assistant", tags=["prompt-assistant"])


@router.get("/status", response_model=PromptAssistantStatus)
@database_handler
def status(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> PromptAssistantStatus:
    container = get_container(request)
    if not container.settings.ollama_base_url:
        return PromptAssistantStatus(
            available=False,
            message="Prompt Assistant is not configured.",
        )

    health = session.get(ServiceHealth, "ollama")
    if health is None:
        return PromptAssistantStatus(
            available=False,
            message="Prompt Assistant availability is still being checked.",
        )

    checked_at = health.checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    stale_after_seconds = max(
        30.0,
        float(container.settings.external_health_interval_seconds) * 3,
    )
    if (datetime.now(UTC) - checked_at).total_seconds() > stale_after_seconds:
        return PromptAssistantStatus(
            available=False,
            message="Prompt Assistant health information is stale; availability is being checked.",
        )

    capabilities = health.capabilities_json or {}
    return PromptAssistantStatus(
        available=health.available,
        message=(
            None
            if health.available
            else health.message
            or "Prompt Assistant is temporarily unavailable; manual prompting still works."
        ),
        vision_available=bool(health.available and capabilities.get("vision") is True),
        router=_router_status(capabilities.get("router")),
    )


def _router_status(value: object) -> PromptAssistantRouterStatus | None:
    """The health monitor's record of the chosen router service, if it is well formed."""

    if not isinstance(value, dict):
        return None
    fields = PromptAssistantRouterStatus.model_fields
    try:
        return PromptAssistantRouterStatus.model_validate(
            {key: item for key, item in value.items() if key in fields}
        )
    except ValueError:
        return None


@router.post("/compose", response_model=PromptComposeResponse)
async def compose(
    payload: PromptComposeRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> PromptComposeResponse:
    return await compose_prompt(get_container(request), context.user.id, payload)


@router.post("/checks", response_model=ExpectationCheckPublic, status_code=202)
async def create_expectation_check(
    payload: ExpectationCheckCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> ExpectationCheckPublic:
    """Start verifying Creative Direction against expectations with vision."""

    key = require_generation_protocol(request)
    return await get_container(request).expectation_checks.accept(context.user.id, payload, key)


@router.get("/checks/latest", response_model=ExpectationCheckLatest)
async def latest_expectation_check(
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> ExpectationCheckLatest:
    checks = get_container(request).expectation_checks
    return ExpectationCheckLatest(check=await run_blocking(checks.latest, context.user.id))


@router.get("/checks/{identity}", response_model=ExpectationCheckPublic)
async def get_expectation_check(
    identity: str,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> ExpectationCheckPublic:
    checks = get_container(request).expectation_checks
    return await run_blocking(checks.get, context.user.id, identity)


@router.post("/checks/{identity}/stop", response_model=ExpectationCheckPublic)
async def stop_expectation_check(
    identity: str,
    request: Request,
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> ExpectationCheckPublic:
    return await get_container(request).expectation_checks.stop_check(context.user.id, identity)
