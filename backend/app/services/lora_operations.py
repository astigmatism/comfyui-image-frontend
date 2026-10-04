"""Coordinate narrow, authenticated ComfyUI-owned LoRA library changes.

One operation changes every publication of a shared LoRA library together (or, for a
sync, every out-of-sync member). ComfyUI owns the bytes: each companion journals all
targets of the operation and commits or restores them as one unit.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import Request
from sqlalchemy import select

from ..blocking import run_blocking
from ..domain.lora_identity import lora_identity_v1
from ..domain.publication import (
    publication_kind,
    source_key_for,
    validate_publication,
)
from ..errors import AppError
from ..models import (
    ACTIVE_STATUSES,
    Generation,
    LoraOperation,
    LoraOperationTarget,
    UserPreference,
    WorkflowProfile,
)
from ..schemas import (
    AdminLoraItem,
    AdminLoraLibraries,
    AdminLoraLibrary,
    AdminLoraLibraryMember,
    LibraryMemberRevision,
    LoraOperationCreate,
    LoraOperationPublic,
    SourceRevision,
)
from .lora_images import prune_library_images
from .lora_library import Library, catalog_identities, libraries
from .user_state import lock_user_state

if TYPE_CHECKING:
    from ..container import AppContainer
    from .comfyui import ComfyUIAdapter

logger = logging.getLogger(__name__)
_NONTERMINAL = ("awaiting_upload", "running", "repair_required")
_RESPONSE_LIMIT = 1024 * 1024
_COMPANION_VERSION = 2
_MESSAGES = {
    "install": "LoRA installed in every library workflow.",
    "remove": "LoRA removed from every library workflow.",
    "edit": "LoRA details updated in every library workflow.",
    "sync": "Library workflows now share the same LoRAs.",
}


@dataclass
class _Target:
    """One publication of an operation, with its image-pool replicas once resolved."""

    source_id: str
    source_key: str
    expected: dict[str, str]
    candidate: dict[str, str] | None = None
    replicas: list[WorkflowProfile] = field(default_factory=list)


def _revision(profile: WorkflowProfile) -> dict[str, str]:
    return {
        "publication_id": str(profile.publication_id),
        "workflow_sha256": profile.ui_graph_sha256,
        "api_sha256": profile.api_graph_sha256,
        "manifest_sha256": str(profile.manifest_sha256),
    }


def _public(row: LoraOperation) -> LoraOperationPublic:
    return LoraOperationPublic(
        id=row.id,
        status=row.status,
        message=row.message,
        result=row.result_json,
        blockers=[str(item) for item in row.blockers_json or []],
    )


def _graph_references_file(graph: Any, filename: str) -> bool:
    """Inspect actual loader inputs and full stack catalogs, including zero strengths."""
    if not isinstance(graph, Mapping):
        return False
    for node in graph.values():
        if not isinstance(node, Mapping):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        if (
            node.get("class_type") in {"LoraLoader", "LoraLoaderModelOnly"}
            and inputs.get("lora_name") == filename
        ):
            return True
        if node.get("class_type") == "CIFLoraStack":
            raw = inputs.get("catalog_json")
            if not isinstance(raw, str):
                raise AppError(
                    "lora_active_graph_uncertain",
                    "An active generation has an unreadable LoRA catalog.",
                    status_code=409,
                )
            try:
                catalog = json.loads(raw)
            except ValueError as exc:
                raise AppError(
                    "lora_active_graph_uncertain",
                    "An active generation has an unreadable LoRA catalog.",
                    status_code=409,
                ) from exc
            if not isinstance(catalog, list):
                raise AppError(
                    "lora_active_graph_uncertain",
                    "An active generation has an unreadable LoRA catalog.",
                    status_code=409,
                )
            if any(
                isinstance(item, Mapping) and item.get("filename") == filename for item in catalog
            ):
                return True
        if "Power Lora Loader" in str(node.get("class_type", "")) and any(
            isinstance(value, Mapping) and value.get("on") is True and value.get("lora") == filename
            for value in inputs.values()
        ):
            return True
    return False


class LoraOperationService:
    def __init__(self, container: AppContainer) -> None:
        self.container = container
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    def _secret(self) -> str:
        configured = self.container.settings.lora_management_secret
        secret = configured.get_secret_value() if configured else ""
        if len(secret) < 32:
            raise AppError(
                "lora_management_unavailable",
                "LoRA administration is not enabled on this server.",
                status_code=503,
            )
        return secret

    async def _request(
        self,
        adapter: ComfyUIAdapter,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        content: AsyncIterator[bytes] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"X-CIF-Management-Token": self._secret(), **(extra_headers or {})}
        response_limit = _RESPONSE_LIMIT
        if path.endswith("/candidate"):
            response_limit = 4096 + sum(
                ((limit + 2) // 3) * 4
                for limit in (
                    self.container.settings.comfyui_workflow_max_bytes,
                    self.container.settings.comfyui_api_max_bytes,
                    self.container.settings.comfyui_manifest_max_bytes,
                )
            )
        try:
            response = await adapter._request_limited(
                method,
                "/cif/lora-management" + path,
                maximum_bytes=response_limit,
                context="LoRA management response",
                headers=headers,
                params=params,
                json=json_body,
                content=content,
                timeout=httpx.Timeout(120.0, connect=5.0),
            )
        except (httpx.HTTPError, OSError) as exc:
            raise AppError(
                "lora_companion_unavailable",
                "A ComfyUI LoRA management companion is unreachable.",
                status_code=503,
            ) from exc
        if not response.is_success:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            # Companion messages can contain private paths; only its stable code crosses to the UI.
            error = payload.get("error")
            code = str(
                payload.get("code")
                or (error.get("code") if isinstance(error, dict) else None)
                or "lora_companion_rejected"
            )
            if not code.replace("_", "").isalnum() or len(code) > 80:
                code = "lora_companion_rejected"
            blocker_messages = {
                "shared_publication": "This LoRA is still used by another published workflow.",
                "authoring_reference": "This LoRA is still used by a ComfyUI authoring workflow.",
                "native_queue_active": "Wait for ComfyUI's native queue to become idle.",
                "same_workflow_loader": "Another loader in this workflow still uses this LoRA.",
                "shared_catalog_file": "Another LoRA entry in this workflow uses the same file.",
                "publication_changed": "ComfyUI publication changed. Refresh sources and retry.",
                "library_file_mismatch": "Library workflows name different files for this LoRA.",
                "catalog_would_drop": "Sync would drop a LoRA from a workflow.",
                "catalog_unchanged": "This workflow already has the library LoRAs.",
            }
            raise AppError(
                code,
                blocker_messages.get(
                    code,
                    "ComfyUI rejected the LoRA change. Check its source and native queue.",
                ),
                status_code=409 if response.status_code < 500 else 503,
                details={"http_status": response.status_code},
            )
        return self._safe_response(response)

    @staticmethod
    def _safe_response(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError as exc:
            raise AppError(
                "lora_companion_invalid",
                "ComfyUI returned an invalid LoRA management response.",
                status_code=503,
            ) from exc
        if not isinstance(value, dict):
            raise AppError(
                "lora_companion_invalid",
                "ComfyUI returned an invalid LoRA management response.",
                status_code=503,
            )
        return value

    # ------------------------------------------------------------------ preflight

    def _profiles(self, source_key: str) -> tuple[str, list[WorkflowProfile]]:
        with self.container.db.session_factory() as session:
            profile = self.container.registry.get_current(
                session, source_key, require_dependencies=False
            )
            if publication_kind(profile.resolved_contract_json) != "image":
                raise AppError(
                    "source_kind_invalid", "Choose an image generation source.", status_code=422
                )
            source_id = str(profile.source_id)
            if not source_id:
                raise AppError(
                    "source_unavailable", "Source has no publication path.", status_code=409
                )
            pool_ids = set(self.container.image_pool.member_ids)
            replicas = [
                row
                for row in self.container.registry.current_replicas(session, source_id=source_id)
                if str(row.instance_id) in pool_ids
            ]
            by_instance = {str(row.instance_id): row for row in replicas}
            if set(by_instance) != pool_ids:
                raise AppError(
                    "lora_replicas_missing",
                    "Every library workflow must be published on every configured image worker.",
                    status_code=409,
                )
            expected = _revision(profile)
            if any(_revision(row) != expected for row in replicas):
                raise AppError(
                    "lora_replicas_diverged",
                    "The ComfyUI publication replicas disagree. Republish them first.",
                    status_code=409,
                )
            return source_id, replicas

    async def _capabilities(self) -> str:
        """Require the multi-source companion everywhere and return the model writer."""

        writer_ids: list[str] = []
        roots: set[str] = set()
        for instance_id in self.container.image_pool.member_ids:
            capabilities = await self._request(
                self.container.comfyui_instances.get(instance_id), "GET", "/capabilities"
            )
            if capabilities.get("enabled") is not True:
                raise AppError(
                    "lora_companion_unavailable",
                    "Every ComfyUI instance needs the current LoRA management companion.",
                    status_code=503,
                )
            version = capabilities.get("version")
            if (
                not isinstance(version, int)
                or version < _COMPANION_VERSION
                or capabilities.get("multi_source") is not True
            ):
                raise AppError(
                    "lora_companion_upgrade_required",
                    "Upgrade the ComfyUI LoRA companion to manage the shared library.",
                    status_code=503,
                )
            if capabilities.get("model_writer") is True:
                writer_ids.append(instance_id)
            root_id = capabilities.get("model_root_id")
            if not isinstance(root_id, str) or not root_id:
                raise AppError(
                    "lora_model_root_unavailable",
                    "ComfyUI did not identify its shared LoRA model library.",
                    status_code=503,
                )
            roots.add(root_id)
        if len(writer_ids) != 1 or len(roots) != 1:
            raise AppError(
                "lora_model_root_mismatch",
                "LoRA administration requires one model writer and a shared model library.",
                status_code=409,
            )
        return writer_ids[0]

    async def _preflight(
        self, targets: list[_Target], *, check_expected: bool = True
    ) -> tuple[list[_Target], dict[tuple[str, str], dict[str, Any]], str]:
        """Resolve replicas, verify bytes and inventories; returns bundles by (instance, path)."""

        self._secret()
        writer_id = await self._capabilities()
        bundles: dict[tuple[str, str], dict[str, Any]] = {}
        object_infos: dict[str, Mapping[str, Any]] = {}
        resolved: list[_Target] = []
        for target in targets:
            source_id, replicas = await run_blocking(self._profiles, target.source_key)
            if source_id != target.source_id or (
                check_expected and _revision(replicas[0]) != target.expected
            ):
                raise AppError(
                    "library_changed",
                    "A library workflow was republished. Review the library and retry.",
                    status_code=409,
                )
            for profile in replicas:
                instance_id = str(profile.instance_id)
                adapter = self.container.comfyui_instances.get(instance_id)
                bundle = await self._request(
                    adapter, "GET", "/bundle", params={"source_path": source_id}
                )
                if bundle.get("revision") != _revision(profile):
                    raise AppError(
                        "source_republished",
                        "ComfyUI publication changed outside this app. Refresh sources and retry.",
                        status_code=409,
                    )
                if instance_id not in object_infos:
                    object_infos[instance_id] = (await adapter.probe()).object_info
                choices = self._lora_choices(object_infos[instance_id])
                if "CIFLoraStack" not in object_infos[instance_id] or choices is None:
                    raise AppError(
                        "lora_runtime_unavailable",
                        "A ComfyUI replica lacks the LoRA stack node or native loader.",
                        status_code=503,
                    )
                files = bundle.get("files")
                if not isinstance(files, list) or any(
                    not isinstance(item, Mapping)
                    or not isinstance(item.get("id"), str)
                    or not isinstance(item.get("filename"), str)
                    or item["filename"] not in choices
                    for item in files
                ):
                    raise AppError(
                        "lora_inventory_unavailable",
                        "A ComfyUI replica is missing a published LoRA file.",
                        status_code=503,
                    )
                bundles[(instance_id, source_id)] = bundle
            resolved.append(
                _Target(
                    source_id=source_id,
                    source_key=target.source_key,
                    expected=target.expected,
                    candidate=target.candidate,
                    replicas=replicas,
                )
            )
        return resolved, bundles, writer_id

    @staticmethod
    def _lora_choices(object_info: Mapping[str, Any]) -> set[str] | None:
        loader = object_info.get("LoraLoaderModelOnly", {})
        spec = (
            loader.get("input", {}).get("required", {}).get("lora_name", [])
            if isinstance(loader, Mapping)
            else []
        )
        if not isinstance(spec, list) or not spec or not isinstance(spec[0], list):
            return None
        return {item for item in spec[0] if isinstance(item, str)}

    # ------------------------------------------------------------------ library view

    def _current_libraries(self) -> dict[str, Library]:
        with self.container.db.session_factory() as session:
            return libraries(session, self.container.image_pool.primary_id)

    def _active_operation(self) -> LoraOperation | None:
        with self.container.db.session_factory() as session:
            return session.scalar(
                select(LoraOperation)
                .where(LoraOperation.status.in_(_NONTERMINAL))
                .order_by(LoraOperation.created_at)
                .limit(1)
            )

    async def library(self) -> AdminLoraLibraries:
        current = await run_blocking(self._current_libraries)
        active = await run_blocking(self._active_operation)
        try:
            self._secret()
            disabled: str | None = None
        except AppError as exc:
            disabled = exc.message
        views: list[AdminLoraLibrary] = []
        for library in sorted(current.values(), key=lambda item: item.label.lower()):
            reason = disabled
            if reason is None and active is not None:
                reason = (
                    "A LoRA change needs repair before another change can start."
                    if active.status == "repair_required"
                    else "Another LoRA change is in progress."
                )
            if reason is None and library.conflicts:
                reason = "Resolve the library conflicts before changing LoRAs."
            if reason is None:
                try:
                    await self._preflight(self._member_targets(library))
                except AppError as exc:
                    reason = exc.message
            identities = catalog_identities(library.canonical)
            views.append(
                AdminLoraLibrary(
                    key=library.key,
                    label=library.label,
                    members=[
                        AdminLoraLibraryMember(
                            source_key=member.source_key,
                            display_name=member.profile.display_name,
                            revision=SourceRevision(**member.revision),
                            in_sync=member.catalog == library.canonical,
                            missing_count=len(
                                {item["id"] for item in library.canonical}
                                - {item["id"] for item in member.catalog}
                            ),
                            item_count=len(member.catalog),
                        )
                        for member in library.members
                    ],
                    items=[
                        AdminLoraItem(
                            **{
                                key: item[key]
                                for key in ("id", "label", "description", "trigger_word")
                                if key in item
                            },
                            lora_identity=identities.get(item["id"]),
                        )
                        for item in library.canonical
                    ],
                    in_sync=library.in_sync,
                    conflicts=library.conflicts,
                    eligible=reason is None and library.in_sync,
                    reason=reason
                    or (None if library.in_sync else "Sync the library before other changes."),
                    can_sync=reason is None and not library.in_sync,
                    expected_library=[
                        LibraryMemberRevision(
                            source_key=member.source_key, revision=SourceRevision(**member.revision)
                        )
                        for member in library.members
                    ],
                )
            )
        return AdminLoraLibraries(
            libraries=views, active_operation=active.id if active is not None else None
        )

    @staticmethod
    def _member_targets(library: Library, *, only_out_of_sync: bool = False) -> list[_Target]:
        members = library.out_of_sync() if only_out_of_sync else library.members
        return sorted(
            (
                _Target(
                    source_id=member.source_id,
                    source_key=member.source_key,
                    expected=member.revision,
                )
                for member in members
            ),
            key=lambda target: target.source_id,
        )

    # ------------------------------------------------------------------ records

    def status(self, operation_id: str, actor_id: str) -> LoraOperationPublic:
        with self.container.db.session_factory() as session:
            row = session.get(LoraOperation, operation_id)
            if row is None or row.actor_id != actor_id:
                raise AppError("not_found", "LoRA operation was not found.", status_code=404)
            return _public(row)

    def _load(self, operation_id: str) -> tuple[LoraOperation, list[_Target]]:
        with self.container.db.session_factory() as session:
            row = session.get(LoraOperation, operation_id)
            assert row is not None
            rows = list(
                session.scalars(
                    select(LoraOperationTarget)
                    .where(LoraOperationTarget.operation_id == operation_id)
                    .order_by(LoraOperationTarget.position)
                )
            )
            if rows:
                targets = [
                    _Target(
                        source_id=item.source_id,
                        source_key=item.source_key,
                        expected=dict(item.expected_revision_json),
                        candidate=(
                            dict(item.candidate_revision_json)
                            if item.candidate_revision_json
                            else None
                        ),
                    )
                    for item in rows
                ]
            else:  # an operation recorded before the shared library
                candidate = (row.internal_json or {}).get("candidate_revision")
                targets = [
                    _Target(
                        source_id=row.source_id,
                        source_key=row.source_key,
                        expected=dict(row.expected_revision_json),
                        candidate=dict(candidate) if isinstance(candidate, Mapping) else None,
                    )
                ]
            return row, targets

    def _row(self, operation_id: str) -> LoraOperation:
        with self.container.db.session_factory() as session:
            row = session.get(LoraOperation, operation_id)
            assert row is not None
            return row

    def _update(
        self,
        operation_id: str,
        *,
        status: str | None = None,
        message: str | None = None,
        result: dict[str, Any] | None = None,
        blockers: list[str] | None = None,
        internal: dict[str, Any] | None = None,
        candidates: Mapping[str, Mapping[str, str]] | None = None,
    ) -> LoraOperationPublic:
        with self.container.db.session_factory() as session:
            lock_user_state(session)
            row = session.get(LoraOperation, operation_id)
            assert row is not None
            if status is not None:
                row.status = status
            row.message = message
            if result is not None:
                row.result_json = result
            if blockers is not None:
                row.blockers_json = blockers
            if internal is not None:
                row.internal_json = {**(row.internal_json or {}), **internal}
            if candidates:
                for target in session.scalars(
                    select(LoraOperationTarget).where(
                        LoraOperationTarget.operation_id == operation_id
                    )
                ):
                    if target.source_id in candidates:
                        target.candidate_revision_json = dict(candidates[target.source_id])
            row.updated_at = datetime.now(UTC)
            session.commit()
            return _public(row)

    # ------------------------------------------------------------------ create / cancel

    async def cancel(self, operation_id: str, actor_id: str) -> LoraOperationPublic:
        """Release an abandoned upload, including a stage whose reply was lost."""
        async with self._lock:

            def claim() -> None:
                with self.container.db.session_factory() as session:
                    lock_user_state(session)
                    row = session.get(LoraOperation, operation_id)
                    if row is None or row.actor_id != actor_id:
                        raise AppError(
                            "not_found", "LoRA operation was not found.", status_code=404
                        )
                    if row.action != "install" or row.status != "awaiting_upload":
                        raise AppError(
                            "lora_operation_state",
                            "Only a pending upload can be cancelled.",
                            status_code=409,
                        )
                    row.status = "running"
                    session.commit()

            await run_blocking(claim)
            instance_ids = list(self.container.image_pool.member_ids)
            try:
                writer_id = await self._writer_id()
                restored = await self._rollback(operation_id, instance_ids, writer_id)
            except Exception:
                restored = False
            return await run_blocking(
                self._update,
                operation_id,
                status="failed" if restored else "repair_required",
                message=(
                    "Pending LoRA installation cancelled."
                    if restored
                    else "The pending upload could not be cleared from ComfyUI."
                ),
            )

    async def _writer_id(self) -> str:
        instance_ids = list(self.container.image_pool.member_ids)
        capabilities = [
            await self._request(
                self.container.comfyui_instances.get(instance_id), "GET", "/capabilities"
            )
            for instance_id in instance_ids
        ]
        return next(
            instance_id
            for instance_id, item in zip(instance_ids, capabilities, strict=True)
            if item.get("model_writer") is True
        )

    async def create(self, payload: LoraOperationCreate, actor_id: str) -> LoraOperationPublic:
        request = payload.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        def previous() -> LoraOperationPublic | None:
            with self.container.db.session_factory() as session:
                existing = session.scalar(
                    select(LoraOperation).where(
                        LoraOperation.actor_id == actor_id,
                        LoraOperation.idempotency_key == payload.idempotency_key,
                    )
                )
                if existing is None:
                    return None
                if existing.request_digest != digest:
                    raise AppError(
                        "idempotency_conflict",
                        "This operation key belongs to another request.",
                        status_code=409,
                    )
                return _public(existing)

        receipt = await run_blocking(previous)
        if receipt is not None:
            return receipt
        library = (await run_blocking(self._current_libraries)).get(payload.library)
        if library is None:
            raise AppError(
                "lora_library_unavailable",
                "That LoRA library has no published workflows.",
                status_code=404,
            )
        expected = {
            (member.source_key, json.dumps(member.revision.model_dump(), sort_keys=True))
            for member in payload.expected_library
        }
        current = {
            (member.source_key, json.dumps(member.revision, sort_keys=True))
            for member in library.members
        }
        if expected != current:
            raise AppError(
                "library_changed",
                "The library's workflows changed. Review the library and retry.",
                status_code=409,
            )
        if library.conflicts:
            raise AppError(
                "lora_library_conflict",
                "Resolve the library conflicts before changing LoRAs.",
                status_code=409,
            )
        if payload.kind == "sync":
            if library.in_sync:
                raise AppError(
                    "lora_library_in_sync",
                    "Every library workflow already has the same LoRAs.",
                    status_code=409,
                )
        elif not library.in_sync:
            raise AppError(
                "lora_library_out_of_sync",
                "Sync the library before installing, editing, or removing LoRAs.",
                status_code=409,
            )
        current_item = library.item(payload.lora_id) if payload.lora_id else None
        if payload.kind in {"remove", "edit"} and current_item is None:
            raise AppError("lora_not_found", "LoRA is no longer in the library.", status_code=409)
        if (
            payload.kind == "edit"
            and current_item is not None
            and current_item.get("label") == payload.display_name
            and (current_item.get("trigger_word") or "") == payload.trigger_word
        ):
            raise AppError(
                "lora_edit_unchanged",
                "This LoRA already has that title and trigger word.",
                status_code=409,
            )
        if payload.kind == "install" and len(library.canonical) >= 100:
            raise AppError("lora_catalog_full", "The LoRA library is full.", status_code=409)
        targets = self._member_targets(library, only_out_of_sync=payload.kind == "sync")
        await self._preflight(targets)
        await self._expire_uploads()
        internal: dict[str, Any] = {"library": library.key}
        if payload.kind == "sync":
            internal["catalog"] = library.canonical

        def reserve() -> LoraOperationPublic:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                existing = session.scalar(
                    select(LoraOperation).where(
                        LoraOperation.actor_id == actor_id,
                        LoraOperation.idempotency_key == payload.idempotency_key,
                    )
                )
                if existing is not None:
                    if existing.request_digest != digest:
                        raise AppError(
                            "idempotency_conflict",
                            "This operation key belongs to another request.",
                            status_code=409,
                        )
                    return _public(existing)
                if session.scalar(
                    select(LoraOperation.id).where(LoraOperation.status.in_(_NONTERMINAL)).limit(1)
                ):
                    raise AppError(
                        "lora_operation_in_progress",
                        "Another LoRA change is in progress.",
                        status_code=409,
                    )
                for target in targets:
                    profile = self.container.registry.get_current(
                        session, target.source_key, require_dependencies=False
                    )
                    if _revision(profile) != target.expected:
                        raise AppError(
                            "library_changed",
                            "A library workflow was republished. Review the library and retry.",
                            status_code=409,
                        )
                row = LoraOperation(
                    actor_id=actor_id,
                    idempotency_key=payload.idempotency_key,
                    request_digest=digest,
                    source_key=targets[0].source_key,
                    source_id=targets[0].source_id,
                    scope="library",
                    action=payload.kind,
                    status="awaiting_upload" if payload.kind == "install" else "running",
                    expected_revision_json=targets[0].expected,
                    request_json=request,
                    internal_json=internal,
                    blockers_json=[],
                )
                session.add(row)
                session.flush()
                session.add_all(
                    LoraOperationTarget(
                        operation_id=row.id,
                        source_id=target.source_id,
                        source_key=target.source_key,
                        position=index,
                        expected_revision_json=target.expected,
                    )
                    for index, target in enumerate(targets)
                )
                session.commit()
                return _public(row)

        public = await run_blocking(reserve)
        if payload.kind != "install" and public.status == "running":
            self._schedule(public.id)
        return public

    async def _expire_uploads(self) -> None:
        def expired() -> list[tuple[str, str]]:
            with self.container.db.session_factory() as session:
                return [
                    (str(operation_id), str(actor_id))
                    for operation_id, actor_id in session.execute(
                        select(LoraOperation.id, LoraOperation.actor_id).where(
                            LoraOperation.status == "awaiting_upload",
                            LoraOperation.updated_at < datetime.now(UTC) - timedelta(hours=1),
                        )
                    )
                ]

        for operation_id, owner_id in await run_blocking(expired):
            await self.cancel(operation_id, owner_id)

    def _schedule(self, operation_id: str) -> None:
        if any(task.get_name() == f"lora-operation-{operation_id}" for task in self._tasks):
            return
        task = asyncio.create_task(
            self._run_guarded(operation_id), name=f"lora-operation-{operation_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ upload

    async def upload(
        self, operation_id: str, actor_id: str, request: Request
    ) -> LoraOperationPublic:
        self._secret()
        async with self._lock:
            row, targets = await run_blocking(self._load, operation_id)
            if row.actor_id != actor_id:
                raise AppError("not_found", "LoRA operation was not found.", status_code=404)
            if row.action != "install" or row.status != "awaiting_upload":
                raise AppError(
                    "lora_operation_state",
                    "This installation is not waiting for a file.",
                    status_code=409,
                )
            if (
                request.headers.get("content-type", "").split(";", 1)[0]
                != "application/octet-stream"
            ):
                raise AppError(
                    "lora_upload_type",
                    "Upload one raw .safetensors file.",
                    status_code=415,
                )
            declared = request.headers.get("content-length")
            maximum = self.container.settings.lora_upload_max_bytes
            if not declared or not declared.isdecimal() or not 8 < int(declared) <= maximum:
                raise AppError(
                    "lora_upload_size",
                    "A bounded, nonempty LoRA file is required.",
                    status_code=413,
                )
            _, _, writer_id = await self._preflight(targets)
            writer = self.container.comfyui_instances.get(writer_id)
            written = 0
            digest = hashlib.sha256()
            await run_blocking(self._update, operation_id, status="running")

            async def chunks() -> AsyncIterator[bytes]:
                nonlocal written
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > maximum or written > int(declared):
                        raise AppError(
                            "lora_upload_size",
                            "The LoRA file exceeded its declared size.",
                            status_code=413,
                        )
                    digest.update(chunk)
                    yield chunk
                if written != int(declared):
                    raise AppError(
                        "lora_upload_incomplete",
                        "The LoRA upload ended before all bytes arrived.",
                        status_code=400,
                    )

            try:
                staged = await self._request(
                    writer,
                    "PUT",
                    f"/operations/{operation_id}/file",
                    content=chunks(),
                    extra_headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(declared),
                        # The original Unicode basename was validated on POST. ComfyUI only
                        # needs the extension; the final model name is its server-owned UUID.
                        "X-CIF-Filename": "upload.safetensors",
                    },
                )
            except BaseException:
                await run_blocking(self._update, operation_id, status="awaiting_upload")
                raise
            if staged.get("sha256") != digest.hexdigest() or staged.get("size") != written:
                await run_blocking(
                    self._update,
                    operation_id,
                    status="repair_required",
                    message="ComfyUI did not confirm the exact bytes. Inspect its journal.",
                )
                raise AppError(
                    "lora_upload_mismatch",
                    "ComfyUI did not confirm the exact uploaded bytes.",
                    status_code=503,
                )
            filename = staged.get("filename")
            if not isinstance(filename, str) or not filename.startswith("cif-managed/"):
                await run_blocking(
                    self._update,
                    operation_id,
                    status="repair_required",
                    message="ComfyUI returned an invalid staged reference. Inspect its journal.",
                )
                raise AppError(
                    "lora_upload_invalid",
                    "ComfyUI returned an invalid staged file reference.",
                    status_code=503,
                )
            public = await run_blocking(
                self._update,
                operation_id,
                status="running",
                internal={
                    "model_filename": filename,
                    "model_sha256": digest.hexdigest(),
                    "upload_size": written,
                    "model_writer_instance_id": writer_id,
                },
            )
            self._schedule(operation_id)
            return public

    # ------------------------------------------------------------------ execution

    def _check_active_generations(self, filename: str) -> None:
        with self.container.db.session_factory() as session:
            for generation in session.scalars(
                select(Generation).where(Generation.status.in_(ACTIVE_STATUSES))
            ):
                if _graph_references_file(
                    generation.compiled_graph_json, filename
                ) or _graph_references_file(generation.submitted_graph_json, filename):
                    raise AppError(
                        "lora_active_generation",
                        "An accepted generation still needs this LoRA file. Wait for it to finish.",
                        status_code=409,
                    )

    async def _candidate(
        self,
        adapter: ComfyUIAdapter,
        operation_id: str,
        source_id: str,
        instance_id: str,
    ) -> dict[str, str]:
        payload = await self._request(
            adapter,
            "GET",
            f"/operations/{operation_id}/candidate",
            params={"source_path": source_id},
        )
        decoded: dict[str, bytes] = {}
        for key, limit in (
            ("workflow", self.container.settings.comfyui_workflow_max_bytes),
            ("api", self.container.settings.comfyui_api_max_bytes),
            ("manifest", self.container.settings.comfyui_manifest_max_bytes),
        ):
            raw = payload.get(f"{key}_b64")
            if not isinstance(raw, str) or len(raw) > ((limit + 2) // 3) * 4 + 4:
                raise AppError(
                    "lora_candidate_invalid",
                    "ComfyUI returned an invalid candidate publication.",
                    status_code=503,
                )
            try:
                decoded[key] = base64.b64decode(raw, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise AppError(
                    "lora_candidate_invalid",
                    "ComfyUI returned an invalid candidate publication.",
                    status_code=503,
                ) from exc
        object_info = (await adapter.probe()).object_info
        stem = (
            source_id.removesuffix(".workflow.json")
            if source_id.endswith(".workflow.json")
            else source_id.removesuffix(".json")
        )
        candidate = await run_blocking(
            validate_publication,
            instance_id=instance_id,
            manifest_path=stem + ".interface.json",
            manifest_bytes=decoded["manifest"],
            workflow_bytes=decoded["workflow"],
            api_bytes=decoded["api"],
            object_info=object_info,
            manifest_max_bytes=self.container.settings.comfyui_manifest_max_bytes,
            workflow_max_bytes=self.container.settings.comfyui_workflow_max_bytes,
            api_max_bytes=self.container.settings.comfyui_api_max_bytes,
        )
        if (
            candidate.editable_workflow_drifted
            or candidate.api_drifted
            or candidate.missing_dependencies
        ):
            raise AppError(
                "lora_candidate_invalid",
                "The candidate publication has a hash drift or missing node dependency.",
                status_code=409,
            )
        revision = {
            "publication_id": candidate.publication_id,
            "workflow_sha256": candidate.workflow_sha256,
            "api_sha256": candidate.api_sha256,
            "manifest_sha256": candidate.manifest_sha256,
        }
        if payload.get("revision") != revision:
            raise AppError(
                "lora_candidate_mismatch",
                "ComfyUI candidate revision did not match its validated bytes.",
                status_code=503,
            )
        return revision

    async def _verify_publications(self, targets: list[_Target]) -> None:
        await self.container.registry.refresh()

        def verify() -> None:
            with self.container.db.session_factory() as session:
                for target in targets:
                    for instance_id in (str(row.instance_id) for row in target.replicas):
                        key = source_key_for(instance_id, target.source_id)
                        profile = self.container.registry.get_current(
                            session, key, require_dependencies=False
                        )
                        if _revision(profile) != target.candidate:
                            raise AppError(
                                "lora_publication_unverified",
                                "ComfyUI publication replicas did not verify after the change.",
                                status_code=503,
                            )

        await run_blocking(verify)

    async def _verify_model_inventory(self, filename: str, *, present: bool) -> None:
        for instance_id in self.container.image_pool.member_ids:
            object_info = (
                await self.container.comfyui_instances.get(instance_id).probe()
            ).object_info
            installed = self._lora_choices(object_info) or set()
            if (filename in installed) != present:
                raise AppError(
                    "lora_inventory_unverified",
                    "ComfyUI replicas do not agree on the LoRA file inventory.",
                    status_code=503,
                )

    async def _rollback(self, operation_id: str, replica_ids: list[str], writer_id: str) -> bool:
        row, targets = await run_blocking(self._load, operation_id)
        mirrors = [instance_id for instance_id in replica_ids if instance_id != writer_id]
        # Removal restores the weight before old graphs. Installation removes old graphs
        # before deleting its newly installed weight.
        order = [writer_id, *mirrors] if row.action == "remove" else [*mirrors, writer_id]
        success = True
        for instance_id in order:
            try:
                await self._request(
                    self.container.comfyui_instances.get(instance_id),
                    "POST",
                    f"/operations/{operation_id}/rollback",
                    json_body={},
                )
            except (AppError, httpx.HTTPError):
                logger.exception(
                    "lora_operation_rollback_failed", extra={"operation": operation_id}
                )
                success = False
        for instance_id in replica_ids:
            for target in targets:
                try:
                    bundle = await self._request(
                        self.container.comfyui_instances.get(instance_id),
                        "GET",
                        "/bundle",
                        params={"source_path": target.source_id},
                    )
                    if bundle.get("revision") != target.expected:
                        success = False
                except AppError:
                    success = False
        try:
            await self.container.registry.refresh()
        except Exception:
            success = False
        return success

    async def _run_guarded(self, operation_id: str) -> None:
        try:
            await self._run(operation_id)
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            logger.warning(
                "lora_operation_failed", extra={"operation": operation_id, "failure_kind": exc.code}
            )
            row = await run_blocking(self._row, operation_id)
            if row.action == "install" and row.internal_json.get("model_filename"):
                writer_id = row.internal_json.get("model_writer_instance_id")
                replica_ids = list(self.container.image_pool.member_ids)
                if not isinstance(writer_id, str) or not await self._rollback(
                    operation_id, replica_ids, writer_id
                ):
                    await run_blocking(
                        self._update,
                        operation_id,
                        status="repair_required",
                        message="ComfyUI could not clear the staged LoRA after a failure.",
                    )
                    return
            await run_blocking(
                self._update,
                operation_id,
                status="failed",
                message=exc.message,
                blockers=[exc.message] if exc.status_code == 409 else [],
            )
        except Exception:
            logger.exception("lora_operation_failed", extra={"operation": operation_id})
            await run_blocking(
                self._update,
                operation_id,
                status="repair_required",
                message="LoRA change stopped unexpectedly. Inspect the ComfyUI journal.",
            )

    def _change(self, row: LoraOperation, filename: str | None) -> dict[str, Any]:
        request = row.request_json
        if row.action == "install":
            model_sha256 = row.internal_json.get("model_sha256")
            if not isinstance(filename, str) or not isinstance(model_sha256, str):
                raise AppError(
                    "lora_upload_missing",
                    "The staged LoRA upload was not confirmed.",
                    status_code=409,
                )
            return {
                "action": "install",
                "id": "lora_" + uuid.uuid4().hex,
                "label": request["display_name"],
                "trigger_word": request["trigger_word"],
                "filename": filename,
                "sha256": model_sha256,
            }
        if row.action == "remove":
            return {"action": "remove", "id": request["lora_id"]}
        if row.action == "edit":
            return {
                "action": "edit",
                "id": request["lora_id"],
                "label": request["display_name"],
                "trigger_word": request["trigger_word"],
            }
        catalog = row.internal_json.get("catalog")
        if not isinstance(catalog, list) or not catalog:
            raise AppError(
                "lora_library_unavailable", "The library catalog was not recorded.", status_code=409
            )
        return {"action": "set_catalog", "items": catalog}

    async def _run(self, operation_id: str) -> None:
        row, recorded = await run_blocking(self._load, operation_id)
        targets, bundles, writer_id = await self._preflight(recorded)
        writer = self.container.comfyui_instances.get(writer_id)
        filename: str | None = None
        if row.action == "install":
            filename = row.internal_json.get("model_filename")
        elif row.action in {"remove", "edit"}:
            found = {
                next(
                    (
                        item.get("filename")
                        for item in bundles[(writer_id, target.source_id)].get("files") or []
                        if isinstance(item, Mapping)
                        and item.get("id") == row.request_json.get("lora_id")
                    ),
                    None,
                )
                for target in targets
            }
            if len(found) != 1 or not isinstance(next(iter(found)), str):
                raise AppError(
                    "lora_not_found" if None in found else "library_file_mismatch",
                    "LoRA is no longer in every library workflow."
                    if None in found
                    else "The library workflows name different files for this LoRA.",
                    status_code=409,
                )
            filename = str(next(iter(found)))
            await run_blocking(self._update, operation_id, internal={"model_filename": filename})
            if row.action == "remove":
                await run_blocking(self._check_active_generations, filename)
        finalized = 0
        replica_ids = list(self.container.image_pool.member_ids)
        published_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            change = self._change(row, filename)
            body = {
                "targets": [
                    {
                        "source_path": target.source_id,
                        "expected_revision": target.expected,
                        "publication_id": str(uuid.uuid4()),
                    }
                    for target in targets
                ],
                "change": change,
                "published_at": published_at,
            }
            # Prepare every replica before any commit. The writer establishes the journal.
            order = [writer_id, *[item for item in replica_ids if item != writer_id]]
            candidates: dict[str, dict[str, str]] = {}
            for instance_id in order:
                adapter = self.container.comfyui_instances.get(instance_id)
                prepared = await self._request(
                    adapter, "POST", f"/operations/{operation_id}/prepare", json_body=body
                )
                announced = {
                    item.get("source_path"): item.get("candidate_revision")
                    for item in prepared.get("targets") or []
                    if isinstance(item, Mapping)
                }
                for target in targets:
                    observed = await self._candidate(
                        adapter, operation_id, target.source_id, instance_id
                    )
                    if announced.get(target.source_id) != observed:
                        raise AppError(
                            "lora_candidate_mismatch",
                            "The prepared LoRA publication did not match its candidate bytes.",
                            status_code=503,
                        )
                    if target.source_id in candidates and candidates[target.source_id] != observed:
                        raise AppError(
                            "lora_replicas_diverged",
                            "The ComfyUI replicas prepared different publications.",
                            status_code=409,
                        )
                    candidates[target.source_id] = observed
            for target in targets:
                target.candidate = candidates[target.source_id]
            await run_blocking(
                self._update,
                operation_id,
                internal={"change": change},
                candidates=candidates,
            )
            for instance_id in order:
                await self._request(
                    self.container.comfyui_instances.get(instance_id),
                    "POST",
                    f"/operations/{operation_id}/commit",
                    json_body={},
                )
            await self._verify_publications(targets)
            if filename is not None:
                await self._verify_model_inventory(filename, present=True)
            if row.action == "remove":
                await self._request(
                    writer, "POST", f"/operations/{operation_id}/quarantine", json_body={}
                )
                assert filename is not None
                await self._verify_model_inventory(filename, present=False)
            # Backups are discarded only after every observed revision and model inventory agrees.
            for instance_id in [*order[1:], writer_id]:
                await self._request(
                    self.container.comfyui_instances.get(instance_id),
                    "POST",
                    f"/operations/{operation_id}/finalize",
                    json_body={},
                )
                finalized += 1
            # Finalization can irreversibly delete a removed weight. Keep the
            # operation recoverable if app-side refresh or reconciliation fails.
            await self.container.registry.refresh()
            await run_blocking(self._reconcile_after_success, targets, change, filename)
            await run_blocking(
                self._update,
                operation_id,
                status="succeeded",
                message=_MESSAGES[row.action],
                result=self._result(targets, change),
            )
        except BaseException:
            if finalized:
                await run_blocking(
                    self._update,
                    operation_id,
                    status="repair_required",
                    message="ComfyUI finalization needs recovery. Check the operation journal.",
                )
                return
            # A prepare request can succeed while its HTTP reply is lost. Rollback is
            # idempotent, including on replicas without a journal.
            restored = await self._rollback(operation_id, replica_ids, writer_id)
            if not restored:
                await run_blocking(
                    self._update,
                    operation_id,
                    status="repair_required",
                    message="ComfyUI could not restore every publication and file.",
                )
                return
            raise

    @staticmethod
    def _result(targets: list[_Target], change: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "lora_id": change.get("id"),
            "workflows": len(targets),
            "revisions": {target.source_key: target.candidate for target in targets},
        }

    def _reconcile_after_success(
        self, targets: list[_Target], change: Mapping[str, Any], filename: str | None
    ) -> None:
        paths: list[str] = []
        with self.container.db.session_factory() as session:
            if change["action"] == "remove":
                identity = lora_identity_v1(filename)
                if identity:
                    paths = prune_library_images(session, removed={identity})
                removed_id = str(change["id"])
                source_keys = {
                    str(row.source_key)
                    for target in targets
                    for row in self.container.registry.current_replicas(
                        session, source_id=target.source_id
                    )
                } | {target.source_key for target in targets}
                for preference in session.scalars(select(UserPreference)):
                    settings = json.loads(json.dumps(preference.settings_json or {}))
                    if _strip_saved_lora(settings, source_keys, removed_id):
                        preference.settings_json = settings
                        preference.revision += 1
            session.commit()
        if paths:
            self.container.assets.delete_paths(paths)

    # ------------------------------------------------------------------ recovery

    async def recover(self) -> None:
        """Conservatively restore interrupted operations before accepting new changes."""

        def pending() -> list[str]:
            with self.container.db.session_factory() as session:
                return list(
                    session.scalars(
                        select(LoraOperation.id).where(
                            LoraOperation.status.in_(("running", "repair_required"))
                        )
                    )
                )

        for operation_id in await run_blocking(pending):
            row, targets = await run_blocking(self._load, operation_id)
            try:
                replica_ids = list(self.container.image_pool.member_ids)
                writer_id = await self._writer_id()
                states: dict[str, str] = {}
                for instance_id in replica_ids:
                    try:
                        status = await self._request(
                            self.container.comfyui_instances.get(instance_id),
                            "GET",
                            f"/operations/{row.id}",
                        )
                    except AppError as exc:
                        if exc.details.get("http_status") != 404:
                            raise
                        states[instance_id] = "missing"
                    else:
                        states[instance_id] = str(status.get("state"))
                if "finalized" in states.values():
                    change = row.internal_json.get("change")
                    if not isinstance(change, dict) or any(
                        not isinstance(target.candidate, dict) for target in targets
                    ):
                        raise AppError(
                            "lora_recovery_incomplete",
                            "The finalized LoRA operation lacks a candidate revision.",
                            status_code=503,
                        )
                    for instance_id in replica_ids:
                        for target in targets:
                            bundle = await self._request(
                                self.container.comfyui_instances.get(instance_id),
                                "GET",
                                "/bundle",
                                params={"source_path": target.source_id},
                            )
                            if bundle.get("revision") != target.candidate:
                                raise AppError(
                                    "lora_recovery_diverged",
                                    "Finalized LoRA publication replicas disagree.",
                                    status_code=503,
                                )
                    for instance_id in replica_ids:
                        if states[instance_id] != "finalized":
                            await self._request(
                                self.container.comfyui_instances.get(instance_id),
                                "POST",
                                f"/operations/{row.id}/finalize",
                                json_body={},
                            )
                    await self.container.registry.refresh()
                    for target in targets:
                        _, target.replicas = await run_blocking(self._profiles, target.source_key)
                    filename = row.internal_json.get("model_filename")
                    if not isinstance(filename, str) and row.action != "sync":
                        raise AppError(
                            "lora_recovery_incomplete",
                            "The finalized operation lacks its model identity.",
                            status_code=503,
                        )
                    if isinstance(filename, str):
                        await self._verify_model_inventory(filename, present=row.action != "remove")
                    await run_blocking(self._reconcile_after_success, targets, change, filename)
                    await run_blocking(
                        self._update,
                        row.id,
                        status="succeeded",
                        message="Interrupted LoRA change finished after restart.",
                        result=self._result(targets, change),
                    )
                else:
                    if row.status == "repair_required":
                        # A prior rollback failed. Do not reinterpret that as a fresh,
                        # safely reversible operation on restart.
                        continue
                    restored = await self._rollback(row.id, replica_ids, writer_id)
                    await run_blocking(
                        self._update,
                        row.id,
                        status="failed" if restored else "repair_required",
                        message="Interrupted LoRA change was rolled back."
                        if restored
                        else "An interrupted LoRA change needs manual repair.",
                    )
            except Exception:
                logger.exception("lora_operation_recovery_failed", extra={"operation": row.id})
                await run_blocking(
                    self._update,
                    row.id,
                    status="repair_required",
                    message="An interrupted LoRA change needs manual repair.",
                )

    async def close(self) -> None:
        if self._tasks:
            tasks = tuple(self._tasks)
            for task in tasks:
                task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self.container.settings.graceful_shutdown_timeout_seconds,
                )
            except TimeoutError:
                logger.warning("lora_operation_shutdown_pending")


def _strip_saved_lora(settings: dict[str, Any], source_keys: set[str], removed_id: str) -> bool:
    """Remove a deleted LoRA from saved stacks and strength memory of the given sources."""

    changed = False
    for source_map in (
        settings.get("sources"),
        (settings.get("prompt_generation") or {}).get("sources"),
    ):
        if not isinstance(source_map, dict):
            continue
        for key in source_keys:
            saved = source_map.get(key)
            if not isinstance(saved, dict):
                continue
            values = saved.get("values")
            if isinstance(values, dict):
                for control_id, entries in values.items():
                    if isinstance(entries, list):
                        filtered = [
                            entry
                            for entry in entries
                            if not (isinstance(entry, dict) and entry.get("id") == removed_id)
                        ]
                        if filtered != entries:
                            values[control_id] = filtered
                            changed = True
            memory = saved.get("lora_strength_memory")
            if isinstance(memory, dict):
                for strengths in memory.values():
                    if isinstance(strengths, dict) and removed_id in strengths:
                        del strengths[removed_id]
                        changed = True
    return changed
