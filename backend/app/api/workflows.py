from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import AuthContext, get_container, get_db, require_ready_user
from ..models import ServiceHealth
from ..schemas import ServiceStatus, SourceRevision, WorkflowDetail, WorkflowSummary

router = APIRouter(prefix="/api", tags=["generation-sources"])


@router.get("/workflows", response_model=list[WorkflowSummary])
def list_workflows(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> list[WorkflowSummary]:
    container = get_container(request)
    health = session.get(ServiceHealth, "comfyui")
    result = [_summary(profile, health) for profile in container.registry.list_current(session)]
    existing_keys = {item.source_key for item in result}
    for raw in container.registry.unavailable_catalog_entries(session):
        source_key = raw.get("source_key")
        if not isinstance(source_key, str) or source_key in existing_keys:
            continue
        revision = raw.get("revision")
        if not isinstance(revision, Mapping):
            continue
        result.append(
            WorkflowSummary(
                source_key=source_key,
                display_name=str(raw.get("display_name", "Unavailable source")),
                instance_id=str(raw.get("instance_id", "default")),
                readiness=str(raw.get("readiness", "unavailable")),
                available=False,
                cached=False,
                message=str(raw.get("message", "This published source is unavailable.")),
                warnings=[str(value) for value in raw.get("warnings", [])],
                revision=SourceRevision(
                    publication_id=str(revision.get("publication_id", "")),
                    workflow_sha256=str(revision.get("workflow_sha256", "")),
                    api_sha256=str(revision.get("api_sha256", "")),
                    manifest_sha256=str(revision.get("manifest_sha256", "")),
                ),
            )
        )
    return sorted(result, key=lambda item: (item.display_name.casefold(), item.source_key))


@router.get("/workflows/{source_key}", response_model=WorkflowDetail)
def get_workflow(
    source_key: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> WorkflowDetail:
    container = get_container(request)
    profile = container.registry.get_current(session, source_key)
    health = session.get(ServiceHealth, "comfyui")
    summary = _summary(profile, health)
    return WorkflowDetail(
        **summary.model_dump(), interface=_public_interface(profile.resolved_contract_json)
    )


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


def _summary(profile: Any, health: ServiceHealth | None) -> WorkflowSummary:
    online = bool(health and health.available)
    cached = not online
    catalog_state = (
        health.capabilities_json.get("catalog_state")
        if health and isinstance(health.capabilities_json, dict)
        else None
    )
    dependency_unavailable = bool(
        health
        and profile.source_key
        in health.capabilities_json.get("dependency_unavailable_source_keys", [])
    )
    if health is None or catalog_state == "loading":
        readiness = "loading"
    elif dependency_unavailable:
        readiness = "dependency_missing"
    elif online:
        readiness = str(profile.readiness or "ready")
    else:
        readiness = "cached_offline"
    publication_id = profile.publication_id or profile.workflow_version
    manifest_sha256 = profile.manifest_sha256 or profile.contract_sha256
    return WorkflowSummary(
        source_key=str(profile.source_key),
        display_name=profile.display_name,
        instance_id=profile.instance_id or "default",
        readiness=readiness,
        available=online and not dependency_unavailable and profile.state.value == "valid",
        cached=cached,
        message=(
            "Required ComfyUI node classes are unavailable for this source."
            if dependency_unavailable
            else (health.message if health and not online else None)
        ),
        warnings=[str(value) for value in (profile.warnings_json or [])],
        revision=SourceRevision(
            publication_id=publication_id,
            workflow_sha256=profile.ui_graph_sha256,
            api_sha256=profile.api_graph_sha256,
            manifest_sha256=manifest_sha256,
        ),
        profile_id=profile.id,
        workflow_id=profile.workflow_id,
        workflow_version=profile.workflow_version,
        ui_graph_sha256=profile.ui_graph_sha256,
        api_graph_sha256=profile.api_graph_sha256,
        contract_sha256=profile.contract_sha256,
        contract_schema_version=profile.contract_schema_version,
        adapter_version=profile.adapter_version,
    )


def _public_interface(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Construct an allowlist projection; private bindings are never copied then removed."""

    inputs: list[dict[str, Any]] = []
    for raw in contract.get("inputs", []):
        if not isinstance(raw, Mapping):
            continue
        public_input = {
            key: copy.deepcopy(raw[key])
            for key in (
                "id",
                "type",
                "label",
                "description",
                "semantic_role",
                "required",
                "advanced",
                "group",
                "order",
                "default",
                "default_mode",
                "minimum",
                "maximum",
                "step",
            )
            if key in raw
        }
        if public_input.get("type") == "seed":
            for key in ("minimum", "maximum", "step"):
                if key in public_input:
                    public_input[key] = str(public_input[key])
            public_input["default"] = (
                None
                if public_input.get("default_mode") == "random"
                else str(public_input.get("default"))
            )
        elif public_input.get("type") == "choice":
            public_input["choices"] = [
                {
                    key: copy.deepcopy(choice[key])
                    for key in ("value", "label", "default_strength")
                    if key in choice
                }
                for choice in raw.get("choices", [])
                if isinstance(choice, Mapping)
            ]
        inputs.append(public_input)
    outputs: list[dict[str, Any]] = []
    for raw in contract.get("outputs", []):
        if not isinstance(raw, Mapping):
            continue
        outputs.append(
            {
                key: copy.deepcopy(raw[key])
                for key in ("id", "role", "kind", "cardinality", "label", "description")
                if key in raw
            }
        )
    return {
        "schema": contract.get("schema"),
        "inputs": inputs,
        "outputs": outputs,
        "unmapped_outputs_policy": contract.get("unmapped_outputs_policy", "collect"),
    }
