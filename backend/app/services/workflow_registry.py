from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..domain.publication import (
    ValidatedPublication,
    display_name_for_source,
    source_id_for_manifest_path,
    source_key_for,
    validate_publication,
    validate_publication_manifest,
    validate_userdata_path,
)
from ..errors import AppError, ContractError
from ..models import ServiceHealth, WorkflowDiagnostic, WorkflowProfile, WorkflowState
from .comfyui import ComfyUIAdapter

logger = logging.getLogger(__name__)
PUBLIC_DEPENDENCY_MESSAGE = "Required ComfyUI node classes are unavailable for this source."


async def _run_blocking[T](operation: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Finish a started thread operation before propagating task cancellation."""

    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


class WorkflowRegistry:
    """Durable, atomic catalog of deliberately published ComfyUI sources."""

    def __init__(self, session_factory: sessionmaker[Session], adapter: ComfyUIAdapter):
        self.session_factory = session_factory
        self.adapter = adapter
        self._refresh_lock = asyncio.Lock()

    def mark_startup_loading(self) -> None:
        """Make cached sources visible but non-dispatchable before network discovery."""

        with self.session_factory() as session:
            cached_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(WorkflowProfile)
                    .where(WorkflowProfile.is_current.is_(True))
                )
                or 0
            )
            health = session.get(ServiceHealth, "comfyui")
            if health is None:
                health = ServiceHealth(service="comfyui")
                session.add(health)
            prior_capabilities = (
                dict(health.capabilities_json) if isinstance(health.capabilities_json, dict) else {}
            )
            prior_capabilities.update(
                {
                    "instance_id": self.adapter.settings.comfyui_instance_id,
                    "catalog_state": "loading",
                    "cached_sources": cached_count,
                }
            )
            health.available = False
            health.message = "ComfyUI source discovery is still loading."
            health.capabilities_json = prior_capabilities
            health.checked_at = datetime.now(UTC)
            session.commit()

    def record_background_refresh_failure(self) -> None:
        self._record_transport_failure(
            datetime.now(UTC),
            code="startup_refresh_failed",
            message="ComfyUI source discovery failed during application startup.",
        )

    async def refresh(self) -> list[WorkflowDiagnostic]:
        async with self._refresh_lock:
            return await self._refresh_unlocked()

    async def _refresh_unlocked(self) -> list[WorkflowDiagnostic]:
        now = datetime.now(UTC)
        try:
            capabilities = await self.adapter.probe()
        except (AppError, httpx.HTTPError, OSError) as exc:
            return await _run_blocking(
                self._record_transport_failure,
                now,
                code="server_unreachable",
                message=(exc.message if isinstance(exc, AppError) else "ComfyUI is unreachable."),
            )
        try:
            listed_files = await self.adapter.list_workflow_files()
        except (AppError, httpx.HTTPError, OSError) as exc:
            return await _run_blocking(
                self._record_transport_failure,
                now,
                code="listing_failed",
                message=(
                    exc.message
                    if isinstance(exc, AppError)
                    else "ComfyUI workflow publication listing failed."
                ),
            )

        manifest_paths: list[str] = []
        listing_diagnostics: list[WorkflowDiagnostic] = []
        for raw_path in listed_files:
            if not raw_path.endswith(".interface.json"):
                continue
            try:
                manifest_paths.append(
                    validate_userdata_path(raw_path, context="manifest listing path")
                )
            except ContractError as exc:
                listing_diagnostics.append(
                    self._diagnostic(
                        now=now,
                        basename="invalid-manifest-path",
                        accepted=False,
                        code=exc.code,
                        message=exc.message,
                    )
                )

        candidate_keys: set[str] = set()
        validated: list[ValidatedPublication] = []
        diagnostics = listing_diagnostics
        for manifest_path in sorted(set(manifest_paths)):
            source_id = source_id_for_manifest_path(manifest_path)
            source_key = source_key_for(self.adapter.settings.comfyui_instance_id, source_id)
            candidate_keys.add(source_key)
            basename = display_name_for_source(source_id)
            manifest_bytes = await self._fetch_candidate_artifact(
                manifest_path,
                self.adapter.settings.comfyui_manifest_max_bytes,
                basename=basename,
                code="manifest_fetch_failed",
                now=now,
                diagnostics=diagnostics,
            )
            if manifest_bytes is None:
                continue
            try:
                manifest_envelope = await _run_blocking(
                    validate_publication_manifest,
                    manifest_path=manifest_path,
                    manifest_bytes=manifest_bytes,
                    manifest_max_bytes=self.adapter.settings.comfyui_manifest_max_bytes,
                )
            except AppError as exc:
                diagnostics.append(
                    self._diagnostic(
                        now=now,
                        basename=basename,
                        accepted=False,
                        code=exc.code,
                        message=exc.message,
                        details={"source_key": source_key},
                    )
                )
                continue
            workflow_path = manifest_envelope.workflow_path
            api_path = manifest_envelope.api_path
            workflow_bytes = await self._fetch_candidate_artifact(
                workflow_path,
                self.adapter.settings.comfyui_workflow_max_bytes,
                basename=basename,
                code="workflow_fetch_failed",
                now=now,
                diagnostics=diagnostics,
            )
            if workflow_bytes is None:
                continue
            api_bytes = await self._fetch_candidate_artifact(
                api_path,
                self.adapter.settings.comfyui_api_max_bytes,
                basename=basename,
                code="api_fetch_failed",
                now=now,
                diagnostics=diagnostics,
            )
            if api_bytes is None:
                continue
            try:
                publication = await _run_blocking(
                    validate_publication,
                    instance_id=self.adapter.settings.comfyui_instance_id,
                    manifest_path=manifest_path,
                    manifest_bytes=manifest_bytes,
                    workflow_bytes=workflow_bytes,
                    api_bytes=api_bytes,
                    object_info=capabilities.object_info,
                    manifest_max_bytes=self.adapter.settings.comfyui_manifest_max_bytes,
                    workflow_max_bytes=self.adapter.settings.comfyui_workflow_max_bytes,
                    api_max_bytes=self.adapter.settings.comfyui_api_max_bytes,
                )
            except AppError as exc:
                diagnostics.append(
                    self._diagnostic(
                        now=now,
                        basename=basename,
                        accepted=False,
                        code=exc.code,
                        message=exc.message,
                        details={"source_key": source_key},
                    )
                )
                continue
            if publication.missing_dependencies:
                diagnostics.append(
                    self._diagnostic(
                        now=now,
                        basename=publication.display_name,
                        accepted=False,
                        workflow_id=publication.source_key,
                        workflow_version=publication.publication_id,
                        code="dependency_missing",
                        message="One or more required ComfyUI node classes are unavailable.",
                        details={
                            "source_key": publication.source_key,
                            "missing_class_types": list(publication.missing_dependencies),
                            "catalog_entry": self._catalog_entry(publication, available=False),
                        },
                    )
                )
                continue
            validated.append(publication)
            diagnostics.append(
                self._diagnostic(
                    now=now,
                    basename=publication.display_name,
                    accepted=True,
                    workflow_id=publication.source_key,
                    workflow_version=publication.publication_id,
                    code=publication.readiness,
                    message=(
                        "Published source is ready with nonfatal warnings."
                        if publication.warnings
                        else "Published source is ready."
                    ),
                    details={
                        "source_key": publication.source_key,
                        "publication_id": publication.publication_id,
                        "workflow_sha256": publication.workflow_sha256,
                        "observed_workflow_sha256": publication.observed_workflow_sha256,
                        "editable_workflow_drifted": publication.editable_workflow_drifted,
                        "api_sha256": publication.api_sha256,
                        "manifest_sha256": publication.manifest_sha256,
                        "warnings": list(publication.warnings),
                    },
                )
            )

        return await _run_blocking(
            self._commit_refresh,
            now=now,
            candidate_keys=candidate_keys,
            validated=validated,
            diagnostics=diagnostics,
            object_info=capabilities.object_info,
        )

    def _commit_refresh(
        self,
        *,
        now: datetime,
        candidate_keys: set[str],
        validated: list[ValidatedPublication],
        diagnostics: list[WorkflowDiagnostic],
        object_info: dict[str, Any],
    ) -> list[WorkflowDiagnostic]:
        with self.session_factory() as session:
            session.execute(delete(WorkflowDiagnostic))
            session.add_all(diagnostics)
            # A successful authoritative listing retires old embedded-contract sources and sources
            # whose manifest disappeared. A listed but rejected candidate retains its last accepted
            # immutable revision.
            session.execute(
                update(WorkflowProfile)
                .where(
                    WorkflowProfile.is_current.is_(True),
                    WorkflowProfile.instance_id.is_(None),
                )
                .values(is_current=False, state=WorkflowState.STALE)
            )
            current_rows = list(
                session.scalars(
                    select(WorkflowProfile).where(
                        WorkflowProfile.is_current.is_(True),
                        WorkflowProfile.instance_id == self.adapter.settings.comfyui_instance_id,
                    )
                )
            )
            for row in current_rows:
                if row.source_key and row.source_key not in candidate_keys:
                    row.is_current = False
                    row.state = WorkflowState.STALE
            for publication in validated:
                self._publish_revision(session, publication, now)
            dependency_unavailable_source_keys = self._current_dependency_failures(
                session, object_info
            )
            current_source_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(WorkflowProfile)
                    .where(
                        WorkflowProfile.is_current.is_(True),
                        WorkflowProfile.state == WorkflowState.VALID,
                        WorkflowProfile.source_key.is_not(None),
                    )
                )
                or 0
            )
            ready_sources = current_source_count - len(dependency_unavailable_source_keys)
            self._set_health(
                session,
                available=True,
                message=None,
                capabilities={
                    "instance_id": self.adapter.settings.comfyui_instance_id,
                    "workflow_userdata": True,
                    "catalog_state": "ready",
                    "ready_sources": ready_sources,
                    "rejected_candidates": len(diagnostics) - len(validated),
                    "dependency_unavailable_source_keys": sorted(
                        dependency_unavailable_source_keys
                    ),
                },
            )
            session.commit()
            return diagnostics

    async def _fetch_candidate_artifact(
        self,
        path: str,
        maximum_bytes: int,
        *,
        basename: str,
        code: str,
        now: datetime,
        diagnostics: list[WorkflowDiagnostic],
    ) -> bytes | None:
        try:
            return await self.adapter.get_userdata_file(path, maximum_bytes=maximum_bytes)
        except (AppError, httpx.HTTPError, OSError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            logger.warning(
                "%s basename=%s route_mode=encoded_segment http_status=%s",
                code,
                basename,
                status if status is not None else "unknown",
            )
            details: dict[str, Any] = {"route_mode": "encoded_segment"}
            if status is not None:
                details["http_status"] = status
            diagnostics.append(
                self._diagnostic(
                    now=now,
                    basename=basename,
                    accepted=False,
                    code=code,
                    message="ComfyUI could not return one required publication artifact.",
                    details=details,
                )
            )
            return None

    def _record_transport_failure(
        self, now: datetime, *, code: str, message: str
    ) -> list[WorkflowDiagnostic]:
        with self.session_factory() as session:
            cached_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(WorkflowProfile)
                    .where(WorkflowProfile.is_current.is_(True))
                )
                or 0
            )
            session.execute(delete(WorkflowDiagnostic))
            diagnostic = self._diagnostic(
                now=now,
                basename="*",
                accepted=False,
                code=code,
                message=message,
                details={"cached_sources": cached_count},
            )
            session.add(diagnostic)
            self._set_health(
                session,
                available=False,
                message=message,
                capabilities={
                    "instance_id": self.adapter.settings.comfyui_instance_id,
                    "catalog_state": "cached_offline" if cached_count else "unavailable",
                    "cached_sources": cached_count,
                },
            )
            session.commit()
            return [diagnostic]

    @staticmethod
    def _catalog_entry(publication: ValidatedPublication, *, available: bool) -> dict[str, Any]:
        dependency_message = PUBLIC_DEPENDENCY_MESSAGE if publication.missing_dependencies else None
        return {
            "source_key": publication.source_key,
            "display_name": publication.display_name,
            "instance_id": publication.instance_id,
            "readiness": publication.readiness,
            "available": available,
            "cached": False,
            "message": dependency_message if publication.missing_dependencies else None,
            "warnings": [
                *publication.warnings,
                *([dependency_message] if dependency_message else []),
            ],
            "revision": {
                "publication_id": publication.publication_id,
                "workflow_sha256": publication.workflow_sha256,
                "api_sha256": publication.api_sha256,
                "manifest_sha256": publication.manifest_sha256,
            },
            "interface": publication.public_interface,
        }

    @staticmethod
    def _publish_revision(
        session: Session, publication: ValidatedPublication, now: datetime
    ) -> WorkflowProfile:
        existing = session.scalar(
            select(WorkflowProfile).where(WorkflowProfile.identity_key == publication.identity_key)
        )
        session.execute(
            update(WorkflowProfile)
            .where(
                WorkflowProfile.source_key == publication.source_key,
                WorkflowProfile.identity_key != publication.identity_key,
                WorkflowProfile.is_current.is_(True),
            )
            .values(is_current=False, state=WorkflowState.STALE)
        )
        if existing is None:
            existing = WorkflowProfile(
                identity_key=publication.identity_key,
                basename=publication.display_name[:255],
                workflow_id=publication.source_key,
                display_name=publication.display_name,
                workflow_version=publication.publication_id,
                contract_schema_version=publication.contract_schema,
                adapter_version=publication.publication_schema,
                ui_graph_sha256=publication.workflow_sha256,
                api_graph_sha256=publication.api_sha256,
                contract_sha256=publication.manifest_sha256,
                source_ui_json=copy_dict(publication.workflow_document),
                source_api_json=copy_dict(publication.api_document),
                manifest_json=copy_dict(publication.manifest),
                resolved_contract_json=copy_dict(publication.private_contract),
                runtime_snapshot_json={
                    "dependencies": list(publication.dependencies),
                    "node_count": publication.node_count,
                    "stored_editable_workflow_sha256": publication.observed_workflow_sha256,
                    "stored_editable_workflow_matches_publication": (
                        not publication.editable_workflow_drifted
                    ),
                },
                instance_id=publication.instance_id,
                source_key=publication.source_key,
                source_id=publication.source_id,
                publication_id=publication.publication_id,
                publication_schema=publication.publication_schema,
                manifest_sha256=publication.manifest_sha256,
                published_at=publication.published_at,
                warnings_json=list(publication.warnings),
                readiness=publication.readiness,
                state=WorkflowState.VALID,
                is_current=True,
                validated_at=now,
                last_seen_at=now,
            )
            session.add(existing)
        else:
            # Accepted revisions are immutable. Rediscovery updates only validation/liveness
            # metadata; frozen graph and manifest snapshots remain the accepted revision.
            existing.is_current = True
            existing.state = WorkflowState.VALID
            existing.readiness = publication.readiness
            existing.warnings_json = list(publication.warnings)
            existing.last_seen_at = now
        return existing

    def list_current(self, session: Session) -> list[WorkflowProfile]:
        return list(
            session.scalars(
                select(WorkflowProfile)
                .where(
                    WorkflowProfile.is_current.is_(True),
                    WorkflowProfile.state == WorkflowState.VALID,
                    WorkflowProfile.source_key.is_not(None),
                )
                .order_by(WorkflowProfile.display_name, WorkflowProfile.source_key)
            )
        )

    def unavailable_catalog_entries(self, session: Session) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for diagnostic in self.diagnostics(session):
            raw = diagnostic.details_json.get("catalog_entry")
            if isinstance(raw, dict):
                entries.append(raw)
        return entries

    def get_current(self, session: Session, source_key: str) -> WorkflowProfile:
        profile = session.scalar(
            select(WorkflowProfile).where(
                WorkflowProfile.source_key == source_key,
                WorkflowProfile.is_current.is_(True),
                WorkflowProfile.state == WorkflowState.VALID,
            )
        )
        if profile is None:
            raise AppError(
                "source_unavailable",
                "Generation source is not currently available.",
                status_code=409,
            )
        if source_key in self.dependency_unavailable_source_keys(session):
            raise AppError(
                "source_dependency_missing",
                "This generation source requires ComfyUI node classes that are unavailable.",
                status_code=409,
            )
        return profile

    def get_current_by_profile(self, session: Session, profile_id: str) -> WorkflowProfile:
        profile = session.scalar(
            select(WorkflowProfile).where(
                WorkflowProfile.id == profile_id,
                WorkflowProfile.is_current.is_(True),
                WorkflowProfile.state == WorkflowState.VALID,
                WorkflowProfile.source_key.is_not(None),
            )
        )
        if profile is None:
            raise AppError(
                "source_unavailable",
                "Generation source is not currently available.",
                status_code=409,
            )
        if profile.source_key and profile.source_key in self.dependency_unavailable_source_keys(
            session
        ):
            raise AppError(
                "source_dependency_missing",
                "This generation source requires ComfyUI node classes that are unavailable.",
                status_code=409,
            )
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
        profile = session.scalar(
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
        if (
            profile is not None
            and profile.source_key
            and profile.source_key in self.dependency_unavailable_source_keys(session)
        ):
            return None
        return profile

    @staticmethod
    def dependency_unavailable_source_keys(session: Session) -> set[str]:
        health = session.get(ServiceHealth, "comfyui")
        if health is None or not isinstance(health.capabilities_json, dict):
            return set()
        raw = health.capabilities_json.get("dependency_unavailable_source_keys", [])
        if not isinstance(raw, list):
            return set()
        return {value for value in raw if isinstance(value, str)}

    @staticmethod
    def _current_dependency_failures(session: Session, object_info: dict[str, Any]) -> set[str]:
        result: set[str] = set()
        available = set(object_info)
        for profile in session.scalars(
            select(WorkflowProfile).where(
                WorkflowProfile.is_current.is_(True),
                WorkflowProfile.state == WorkflowState.VALID,
                WorkflowProfile.source_key.is_not(None),
            )
        ):
            runtime = profile.runtime_snapshot_json
            raw_dependencies = runtime.get("dependencies", []) if isinstance(runtime, dict) else []
            if not isinstance(raw_dependencies, list):
                continue
            dependencies = {value for value in raw_dependencies if isinstance(value, str)}
            if dependencies - available and profile.source_key:
                result.add(profile.source_key)
        return result

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
    def _diagnostic(
        *,
        now: datetime,
        basename: str,
        accepted: bool,
        code: str,
        message: str,
        workflow_id: str | None = None,
        workflow_version: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> WorkflowDiagnostic:
        return WorkflowDiagnostic(
            basename=basename[:255],
            accepted=accepted,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            code=code,
            message=message,
            details_json=details or {},
            checked_at=now,
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


def copy_dict(value: dict[str, Any]) -> dict[str, Any]:
    # JSON round-tripped publication objects contain only mutable JSON containers.
    import copy

    return copy.deepcopy(value)
