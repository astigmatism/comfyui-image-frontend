from __future__ import annotations

import copy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import AuthContext, get_container, get_db, require_ready_user
from ..schemas import ServiceStatus, WorkflowDetail, WorkflowSummary
from ..models import ServiceHealth

router = APIRouter(prefix="/api", tags=["workflows"])


@router.get("/workflows", response_model=list[WorkflowSummary])
def list_workflows(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> list[WorkflowSummary]:
    container = get_container(request)
    return [_summary(profile) for profile in container.registry.list_current(session)]


@router.get("/workflows/{profile_id}", response_model=WorkflowDetail)
def get_workflow(
    profile_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> WorkflowDetail:
    container = get_container(request)
    profile = container.registry.get_current(session, profile_id)
    summary = _summary(profile)
    return WorkflowDetail(**summary.model_dump(), contract=_public_contract(profile.resolved_contract_json))


@router.get("/services", response_model=list[ServiceStatus])
def service_status(
    session: Annotated[Session, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> list[ServiceStatus]:
    result: list[ServiceStatus] = []
    for name in ("comfyui", "ollama"):
        health = session.get(ServiceHealth, name)
        result.append(
            ServiceStatus(
                service=name,
                available=bool(health and health.available),
                message=health.message if health else "Service health has not been checked yet.",
                checked_at=health.checked_at if health else None,
            )
        )
    return result


def _summary(profile: Any) -> WorkflowSummary:
    states = profile.resolved_contract_json.get("capability_states", {})
    safe_states = {
        str(key): {
            "available": bool(value.get("available", False)),
            "reason": value.get("reason"),
        }
        for key, value in states.items()
        if isinstance(value, dict)
    }
    return WorkflowSummary(
        profile_id=profile.id,
        workflow_id=profile.workflow_id,
        workflow_version=profile.workflow_version,
        ui_graph_sha256=profile.ui_graph_sha256,
        api_graph_sha256=profile.api_graph_sha256,
        contract_sha256=profile.contract_sha256,
        display_name=profile.display_name,
        contract_schema_version=profile.contract_schema_version,
        adapter_version=profile.adapter_version,
        capabilities=safe_states,
    )


def _public_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Return semantic presentation data only—never selectors, patches, or operator controls."""
    result: dict[str, Any] = {
        key: copy.deepcopy(contract[key])
        for key in ("kind", "contract_schema_version", "presentation", "policies")
        if key in contract
    }
    workflow = contract.get("workflow", {})
    result["workflow"] = {
        key: workflow.get(key)
        for key in ("id", "display_name", "version", "description", "family")
        if key in workflow
    }
    controls: list[dict[str, Any]] = []
    public_control_ids: set[str] = set()
    for raw in contract.get("controls", []):
        if not isinstance(raw, dict) or raw.get("tier") == "operator":
            continue
        control_id = raw.get("id")
        if isinstance(control_id, str):
            public_control_ids.add(control_id)
        control = {
            key: copy.deepcopy(value)
            for key, value in raw.items()
            if key
            not in {
                "bindings",
                "sensitive",
                "provenance",
                "internal",
            }
        }
        controls.append(control)
    result["controls"] = controls
    result["presets"] = [
        {
            key: (
                {
                    control_id: copy.deepcopy(value)
                    for control_id, value in preset.get("values", {}).items()
                    if control_id in public_control_ids
                }
                if key == "values" and isinstance(preset.get("values"), dict)
                else copy.deepcopy(preset.get(key))
            )
            for key in ("id", "label", "description", "values")
            if key in preset
        }
        for preset in contract.get("presets", [])
        if isinstance(preset, dict)
    ]
    result["stages"] = [
        {
            key: copy.deepcopy(stage.get(key))
            for key in ("id", "label", "sequence", "emits_output_ids", "cancellable_after_emission")
            if key in stage
        }
        for stage in contract.get("stages", [])
        if isinstance(stage, dict)
    ]
    result["outputs"] = [
        {
            key: copy.deepcopy(output.get(key))
            for key in (
                "id",
                "role",
                "kind",
                "canonical_on_success",
                "usable_on_cancel",
                "presentation",
                "progression",
                "batch_semantics",
            )
            if key in output
        }
        for output in contract.get("outputs", [])
        if isinstance(output, dict)
    ]
    result["progression"] = copy.deepcopy(contract.get("progression", {}))
    states = contract.get("capability_states", {})
    result["capability_states"] = {
        key: {"available": bool(value.get("available")), "reason": value.get("reason")}
        for key, value in states.items()
        if isinstance(value, dict)
    }
    return result
