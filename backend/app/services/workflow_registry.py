from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..domain.contract import ValidatedProfile, validate_profile
from ..errors import AppError
from ..models import ServiceHealth, WorkflowDiagnostic, WorkflowProfile, WorkflowState
from .comfyui import ComfyUIAdapter

logger = logging.getLogger(__name__)


class WorkflowRegistry:
    def __init__(self, session_factory: sessionmaker[Session], adapter: ComfyUIAdapter):
        self.session_factory = session_factory
        self.adapter = adapter

    async def refresh(self) -> list[WorkflowDiagnostic]:
        now = datetime.now(UTC)
        try:
            capabilities = await self.adapter.probe()
            files = await self.adapter.list_workflow_files()
        except (AppError, httpx.HTTPError) as exc:
            message = exc.message if isinstance(exc, AppError) else "ComfyUI is unreachable."
            with self.session_factory() as session:
                self._set_health(session, False, message, {})
                diagnostic = WorkflowDiagnostic(
                    basename="*",
                    accepted=False,
                    code="comfyui_unavailable",
                    message=message,
                    checked_at=now,
                    details_json={},
                )
                session.add(diagnostic)
                session.commit()
                return [diagnostic]

        candidates: dict[str, dict[str, str]] = defaultdict(dict)
        for path in files:
            if path.endswith(".workflow.json"):
                candidates[path[: -len(".workflow.json")]]["ui"] = path
            elif path.endswith(".api.json"):
                candidates[path[: -len(".api.json")]]["api"] = path

        validated: list[ValidatedProfile] = []
        diagnostics: list[WorkflowDiagnostic] = []
        for basename in sorted(candidates):
            pair = candidates[basename]
            if set(pair) != {"ui", "api"}:
                missing = ".api.json" if "api" not in pair else ".workflow.json"
                diagnostics.append(
                    WorkflowDiagnostic(
                        basename=basename,
                        accepted=False,
                        code="incomplete_pair",
                        message=f"Missing required {missing} mate.",
                        checked_at=now,
                        details_json={},
                    )
                )
                continue
            try:
                ui_document = await self.adapter.get_workflow_file(pair["ui"])
                api_document = await self.adapter.get_workflow_file(pair["api"])
                profile = validate_profile(
                    basename=basename,
                    ui_document=ui_document,
                    api_document=api_document,
                    object_info=capabilities.object_info,
                    runtime_capabilities={
                        "assets": capabilities.assets,
                        "capabilities": capabilities.capabilities,
                        "system": capabilities.system,
                    },
                )
                validated.append(profile)
                diagnostics.append(
                    WorkflowDiagnostic(
                        basename=basename,
                        accepted=True,
                        workflow_id=profile.workflow_id,
                        workflow_version=profile.workflow_version,
                        code="accepted",
                        message="Workflow pair passed strict static and runtime validation.",
                        checked_at=now,
                        details_json={
                            "ui_graph_sha256": profile.ui_hash,
                            "api_graph_sha256": profile.api_hash,
                            "contract_sha256": profile.contract_hash,
                        },
                    )
                )
            except httpx.HTTPError as exc:
                route_mode = capabilities.workflow_get_route.partition(":")[0] or "unknown"
                http_status = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                logger.warning(
                    "workflow_fetch_failed basename=%s route_mode=%s http_status=%s",
                    basename,
                    route_mode,
                    http_status if http_status is not None else "unknown",
                )
                code = "workflow_fetch_failed"
                message = (
                    "ComfyUI could not return this workflow source file. "
                    "Verify its presence and the configured user-data route."
                )
                details: dict[str, Any] = {"route_mode": route_mode}
                if http_status is not None:
                    details["http_status"] = http_status
                diagnostics.append(
                    WorkflowDiagnostic(
                        basename=basename,
                        accepted=False,
                        code=code,
                        message=message,
                        checked_at=now,
                        details_json=details,
                    )
                )
            except AppError as exc:
                diagnostics.append(
                    WorkflowDiagnostic(
                        basename=basename,
                        accepted=False,
                        code=exc.code,
                        message=exc.message,
                        checked_at=now,
                        details_json=exc.details,
                    )
                )
            except ValueError:
                diagnostics.append(
                    WorkflowDiagnostic(
                        basename=basename,
                        accepted=False,
                        code="contract_invalid",
                        message="Workflow validation failed.",
                        checked_at=now,
                        details_json={},
                    )
                )

        with self.session_factory() as session:
            session.execute(delete(WorkflowDiagnostic))
            session.execute(update(WorkflowProfile).values(is_current=False, state=WorkflowState.STALE))
            for profile in validated:
                existing = session.scalar(
                    select(WorkflowProfile).where(
                        WorkflowProfile.identity_key == profile.identity_key
                    )
                )
                session.execute(
                    update(WorkflowProfile)
                    .where(
                        WorkflowProfile.basename == profile.basename,
                        WorkflowProfile.identity_key != profile.identity_key,
                    )
                    .values(is_current=False, state=WorkflowState.STALE)
                )
                if existing is None:
                    existing = WorkflowProfile(
                        identity_key=profile.identity_key,
                        basename=profile.basename,
                        workflow_id=profile.workflow_id,
                        display_name=profile.display_name,
                        workflow_version=profile.workflow_version,
                        contract_schema_version=profile.contract_schema_version,
                        adapter_version=profile.adapter_version,
                        ui_graph_sha256=profile.ui_hash,
                        api_graph_sha256=profile.api_hash,
                        contract_sha256=profile.contract_hash,
                        source_ui_json=profile.ui_document,
                        source_api_json=profile.api_document,
                        manifest_json=profile.manifest,
                        resolved_contract_json=profile.resolved_contract,
                        runtime_snapshot_json=profile.runtime_snapshot,
                        state=WorkflowState.VALID,
                        is_current=True,
                        validated_at=now,
                        last_seen_at=now,
                    )
                    session.add(existing)
                else:
                    existing.display_name = profile.display_name
                    existing.source_ui_json = profile.ui_document
                    existing.source_api_json = profile.api_document
                    existing.manifest_json = profile.manifest
                    existing.resolved_contract_json = profile.resolved_contract
                    existing.runtime_snapshot_json = profile.runtime_snapshot
                    existing.state = WorkflowState.VALID
                    existing.is_current = True
                    existing.validated_at = now
                    existing.last_seen_at = now
            session.add_all(diagnostics)
            self._set_health(
                session,
                True,
                None,
                {
                    "workflow_userdata": True,
                    "registered_profiles": len(validated),
                    "rejected_profiles": len(diagnostics) - len(validated),
                },
            )
            session.commit()
            # Return attached data as detached rows; all scalar fields are loaded.
            return diagnostics

    def list_current(self, session: Session) -> list[WorkflowProfile]:
        return list(
            session.scalars(
                select(WorkflowProfile)
                .where(
                    WorkflowProfile.is_current.is_(True),
                    WorkflowProfile.state == WorkflowState.VALID,
                )
                .order_by(WorkflowProfile.display_name, WorkflowProfile.workflow_id)
            )
        )

    def get_current(self, session: Session, profile_id: str) -> WorkflowProfile:
        profile = session.scalar(
            select(WorkflowProfile).where(
                WorkflowProfile.id == profile_id,
                WorkflowProfile.is_current.is_(True),
                WorkflowProfile.state == WorkflowState.VALID,
            )
        )
        if profile is None:
            raise AppError("workflow_unavailable", "Generation source is not currently available.", status_code=409)
        return profile

    def find_exact(
        self,
        session: Session,
        *,
        workflow_id: str,
        workflow_version: str,
        ui_hash: str,
        api_hash: str,
        contract_hash: str,
    ) -> WorkflowProfile | None:
        return session.scalar(
            select(WorkflowProfile).where(
                WorkflowProfile.workflow_id == workflow_id,
                WorkflowProfile.workflow_version == workflow_version,
                WorkflowProfile.ui_graph_sha256 == ui_hash,
                WorkflowProfile.api_graph_sha256 == api_hash,
                WorkflowProfile.contract_sha256 == contract_hash,
                WorkflowProfile.is_current.is_(True),
                WorkflowProfile.state == WorkflowState.VALID,
            )
        )

    @staticmethod
    def diagnostics(session: Session) -> list[WorkflowDiagnostic]:
        return list(
            session.scalars(
                select(WorkflowDiagnostic).order_by(
                    WorkflowDiagnostic.basename, WorkflowDiagnostic.accepted.desc()
                )
            )
        )

    @staticmethod
    def _set_health(
        session: Session,
        available: bool,
        message: str | None,
        capabilities: dict[str, Any],
    ) -> None:
        health = session.get(ServiceHealth, "comfyui")
        if health is None:
            health = ServiceHealth(service="comfyui")
            session.add(health)
        health.available = available
        health.message = message
        health.capabilities_json = capabilities
        health.checked_at = datetime.now(UTC)
