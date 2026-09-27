"""Coordinate narrow, authenticated ComfyUI-owned LoRA catalog changes."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import Request
from sqlalchemy import select

from ..blocking import run_blocking
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
    UserPreference,
    WorkflowProfile,
)
from ..schemas import (
    AdminLoraCatalog,
    AdminLoraItem,
    LoraOperationCreate,
    LoraOperationPublic,
    SourceRevision,
)
from .lora_images import catalog_bindings, logical_workflow_key, prune_lora_images
from .user_state import lock_user_state

if TYPE_CHECKING:
    from ..container import AppContainer
    from .comfyui import ComfyUIAdapter

logger = logging.getLogger(__name__)
_NONTERMINAL = ("awaiting_upload", "running", "repair_required")
_RESPONSE_LIMIT = 1024 * 1024


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
            replicas = self.container.registry.current_replicas(session, source_id=source_id)
            by_instance = {str(row.instance_id): row for row in replicas}
            if set(by_instance) != set(self.container.comfyui_instances.configured_ids):
                raise AppError(
                    "lora_replicas_missing",
                    "This source must be published on every configured ComfyUI instance.",
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

    async def _preflight(
        self, source_key: str, expected_revision: Mapping[str, str] | None = None
    ) -> tuple[str, list[WorkflowProfile], dict[str, dict[str, Any]], str]:
        self._secret()
        source_id, replicas = await run_blocking(self._profiles, source_key)
        if expected_revision is not None and _revision(replicas[0]) != expected_revision:
            raise AppError(
                "source_republished",
                "The selected source was republished. Review its current LoRA catalog.",
                status_code=409,
            )
        results: dict[str, dict[str, Any]] = {}
        writer_ids: list[str] = []
        roots: set[str] = set()
        for profile in replicas:
            instance_id = str(profile.instance_id)
            adapter = self.container.comfyui_instances.get(instance_id)
            capabilities = await self._request(adapter, "GET", "/capabilities")
            if capabilities.get("version") != 1 or capabilities.get("enabled") is not True:
                raise AppError(
                    "lora_companion_unavailable",
                    "Every ComfyUI instance needs the current LoRA management companion.",
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
            bundle = await self._request(
                adapter, "GET", "/bundle", params={"source_path": source_id}
            )
            if bundle.get("revision") != _revision(profile):
                raise AppError(
                    "source_republished",
                    "ComfyUI publication changed outside this app. Refresh sources and retry.",
                    status_code=409,
                )
            object_info = (await adapter.probe()).object_info
            loader = object_info.get("LoraLoaderModelOnly", {})
            choices = (
                loader.get("input", {}).get("required", {}).get("lora_name", [])
                if isinstance(loader, Mapping)
                else []
            )
            if (
                "CIFLoraStack" not in object_info
                or not isinstance(choices, list)
                or not choices
                or not isinstance(choices[0], list)
            ):
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
                or item["filename"] not in choices[0]
                for item in files
            ):
                raise AppError(
                    "lora_inventory_unavailable",
                    "A ComfyUI replica is missing a published LoRA file.",
                    status_code=503,
                )
            results[instance_id] = bundle
        if len(writer_ids) != 1 or len(roots) != 1:
            raise AppError(
                "lora_model_root_mismatch",
                "LoRA administration requires one model writer and a shared model library.",
                status_code=409,
            )
        return source_id, replicas, results, writer_ids[0]

    async def catalog(self, source_key: str) -> AdminLoraCatalog:
        def snapshot() -> WorkflowProfile:
            with self.container.db.session_factory() as session:
                profile = session.scalar(
                    select(WorkflowProfile)
                    .where(WorkflowProfile.source_key == source_key)
                    .order_by(
                        WorkflowProfile.is_current.desc(), WorkflowProfile.last_seen_at.desc()
                    )
                    .limit(1)
                )
                if profile is None:
                    raise AppError("source_unavailable", "Source was not found.", status_code=404)
                if publication_kind(profile.resolved_contract_json) != "image":
                    raise AppError(
                        "source_kind_invalid", "Choose an image generation source.", status_code=422
                    )
                return profile

        profile = await run_blocking(snapshot)
        controls = [
            item
            for item in profile.resolved_contract_json.get("inputs", [])
            if isinstance(item, Mapping) and item.get("type") == "lora_stack"
        ]
        items = (
            [
                AdminLoraItem(
                    **{
                        key: item[key]
                        for key in ("id", "label", "description", "trigger_word")
                        if key in item
                    }
                )
                for item in controls[0].get("items", [])
            ]
            if len(controls) == 1
            else []
        )
        reason: str | None = None
        if len(controls) != 1:
            reason = "This source does not publish exactly one LoRA stack."
        elif not profile.is_current:
            reason = "This source is not currently published."
        else:

            def active_operation() -> str | None:
                with self.container.db.session_factory() as session:
                    row = session.scalar(
                        select(LoraOperation)
                        .where(
                            LoraOperation.source_id == profile.source_id,
                            LoraOperation.status.in_(_NONTERMINAL),
                        )
                        .limit(1)
                    )
                    return row.status if row else None

            active = await run_blocking(active_operation)
            if active is not None:
                reason = (
                    "A LoRA change needs repair before another change can start."
                    if active == "repair_required"
                    else "Another LoRA change is in progress for this source."
                )
            else:
                try:
                    await self._preflight(source_key)
                except AppError as exc:
                    reason = exc.message
        return AdminLoraCatalog(
            source_key=source_key,
            revision=SourceRevision(**_revision(profile)),
            eligible=reason is None,
            reason=reason,
            items=items,
        )

    def status(self, operation_id: str, actor_id: str) -> LoraOperationPublic:
        with self.container.db.session_factory() as session:
            row = session.get(LoraOperation, operation_id)
            if row is None or row.actor_id != actor_id:
                raise AppError("not_found", "LoRA operation was not found.", status_code=404)
            return _public(row)

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
            instance_ids = [config.id for config in self.container.comfyui_instances.configs]
            try:
                capabilities = [
                    await self._request(
                        self.container.comfyui_instances.get(instance_id), "GET", "/capabilities"
                    )
                    for instance_id in instance_ids
                ]
                writer_id = next(
                    instance_id
                    for instance_id, capabilities_item in zip(
                        instance_ids, capabilities, strict=True
                    )
                    if capabilities_item.get("model_writer") is True
                )
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

    async def create(self, payload: LoraOperationCreate, actor_id: str) -> LoraOperationPublic:
        revision = payload.expected_revision.model_dump()
        digest = hashlib.sha256(
            json.dumps(
                payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
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
        source_id, replicas, _, _ = await self._preflight(payload.source_key, revision)
        await self._expire_uploads(source_id)
        primary = next(
            row
            for row in replicas
            if row.instance_id == self.container.comfyui_instances.default_id
        )
        controls = [
            item
            for item in primary.resolved_contract_json.get("inputs", [])
            if isinstance(item, Mapping) and item.get("type") == "lora_stack"
        ]
        if len(controls) != 1:
            raise AppError(
                "lora_control_unavailable",
                "This source needs exactly one published LoRA stack.",
                status_code=409,
            )
        items = controls[0].get("items", [])
        current_item = next(
            (
                item
                for item in items
                if isinstance(item, Mapping) and item.get("id") == payload.lora_id
            ),
            None,
        )
        if payload.kind in {"remove", "edit"} and current_item is None:
            raise AppError("lora_not_found", "LoRA is no longer in this catalog.", status_code=409)
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
        if payload.kind == "install" and len(items) >= 100:
            raise AppError("lora_catalog_full", "This LoRA catalog is full.", status_code=409)

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
                    select(LoraOperation.id)
                    .where(
                        LoraOperation.source_id == source_id,
                        LoraOperation.status.in_(_NONTERMINAL),
                    )
                    .limit(1)
                ):
                    raise AppError(
                        "lora_operation_in_progress",
                        "Another LoRA change is in progress for this source.",
                        status_code=409,
                    )
                current = self.container.registry.get_current(
                    session, payload.source_key, require_dependencies=False
                )
                if _revision(current) != revision:
                    raise AppError(
                        "source_republished",
                        "The selected source was republished. Review its current LoRA catalog.",
                        status_code=409,
                    )
                row = LoraOperation(
                    actor_id=actor_id,
                    idempotency_key=payload.idempotency_key,
                    request_digest=digest,
                    source_key=payload.source_key,
                    source_id=source_id,
                    action=payload.kind,
                    status="awaiting_upload" if payload.kind == "install" else "running",
                    expected_revision_json=revision,
                    request_json=payload.model_dump(mode="json"),
                    internal_json={},
                    blockers_json=[],
                )
                session.add(row)
                session.commit()
                return _public(row)

        public = await run_blocking(reserve)
        if payload.kind in {"remove", "edit"} and public.status == "running":
            self._schedule(public.id)
        return public

    async def _expire_uploads(self, source_id: str) -> None:
        def expired() -> list[tuple[str, str]]:
            with self.container.db.session_factory() as session:
                return list(
                    session.execute(
                        select(LoraOperation.id, LoraOperation.actor_id).where(
                            LoraOperation.source_id == source_id,
                            LoraOperation.status == "awaiting_upload",
                            LoraOperation.updated_at < datetime.now(UTC) - timedelta(hours=1),
                        )
                    )
                )

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

    def _update(
        self,
        operation_id: str,
        *,
        status: str | None = None,
        message: str | None = None,
        result: dict[str, Any] | None = None,
        blockers: list[str] | None = None,
        internal: dict[str, Any] | None = None,
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
            row.updated_at = datetime.now(UTC)
            session.commit()
            return _public(row)

    def _row(self, operation_id: str) -> LoraOperation:
        with self.container.db.session_factory() as session:
            row = session.get(LoraOperation, operation_id)
            assert row is not None
            return row

    async def upload(
        self, operation_id: str, actor_id: str, request: Request
    ) -> LoraOperationPublic:
        self._secret()
        async with self._lock:
            row = await run_blocking(self._row, operation_id)
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
            _, _, _, writer_id = await self._preflight(row.source_key, row.expected_revision_json)
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
        payload = await self._request(adapter, "GET", f"/operations/{operation_id}/candidate")
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
        manifest_path = stem + ".interface.json"
        candidate = await run_blocking(
            validate_publication,
            instance_id=instance_id,
            manifest_path=manifest_path,
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

    async def _verify_publication(
        self, source_id: str, revision: Mapping[str, str], replicas: list[WorkflowProfile]
    ) -> None:
        await self.container.registry.refresh(prune_images=False)

        def verify() -> None:
            with self.container.db.session_factory() as session:
                for instance_id in (str(row.instance_id) for row in replicas):
                    key = source_key_for(instance_id, source_id)
                    profile = self.container.registry.get_current(
                        session, key, require_dependencies=False
                    )
                    if _revision(profile) != revision:
                        raise AppError(
                            "lora_publication_unverified",
                            "ComfyUI publication replicas did not verify after the change.",
                            status_code=503,
                        )

        await run_blocking(verify)

    async def _verify_model_inventory(
        self, filename: str, replicas: list[WorkflowProfile], *, present: bool
    ) -> None:
        for profile in replicas:
            object_info = (
                await self.container.comfyui_instances.get(str(profile.instance_id)).probe()
            ).object_info
            spec = (
                object_info.get("LoraLoaderModelOnly", {})
                .get("input", {})
                .get("required", {})
                .get("lora_name", [])
            )
            installed = (
                set(spec[0])
                if isinstance(spec, list) and spec and isinstance(spec[0], list)
                else set()
            )
            if (filename in installed) != present:
                raise AppError(
                    "lora_inventory_unverified",
                    "ComfyUI replicas do not agree on the LoRA file inventory.",
                    status_code=503,
                )

    async def _rollback(self, operation_id: str, replica_ids: list[str], writer_id: str) -> bool:
        row = await run_blocking(self._row, operation_id)
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
            try:
                bundle = await self._request(
                    self.container.comfyui_instances.get(instance_id),
                    "GET",
                    "/bundle",
                    params={"source_path": row.source_id},
                )
                if bundle.get("revision") != row.expected_revision_json:
                    success = False
            except AppError:
                success = False
        try:
            await self.container.registry.refresh(prune_images=False)
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
                replica_ids = [config.id for config in self.container.comfyui_instances.configs]
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

    async def _run(self, operation_id: str) -> None:
        row = await run_blocking(self._row, operation_id)
        source_id, replicas, bundles, writer_id = await self._preflight(
            row.source_key, row.expected_revision_json
        )
        writer = self.container.comfyui_instances.get(writer_id)
        request = row.request_json
        filename: str | None = None
        if row.action in {"remove", "edit"}:
            files = bundles[writer_id].get("files")
            filename = next(
                (
                    item.get("filename")
                    for item in files or []
                    if isinstance(item, Mapping) and item.get("id") == request.get("lora_id")
                ),
                None,
            )
            if not isinstance(filename, str):
                raise AppError(
                    "lora_not_found", "LoRA is no longer in this catalog.", status_code=409
                )
            await run_blocking(self._update, operation_id, internal={"model_filename": filename})
            if row.action == "remove":
                await run_blocking(self._check_active_generations, filename)
        finalized = 0
        candidate_revision: dict[str, str] | None = None
        publication_id = str(uuid.uuid4())
        published_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            if row.action == "install":
                filename = row.internal_json.get("model_filename")
                model_sha256 = row.internal_json.get("model_sha256")
                if not isinstance(filename, str) or not isinstance(model_sha256, str):
                    raise AppError(
                        "lora_upload_missing",
                        "The staged LoRA upload was not confirmed.",
                        status_code=409,
                    )
                change = {
                    "action": "install",
                    "id": "lora_" + uuid.uuid4().hex,
                    "label": request["display_name"],
                    "trigger_word": request["trigger_word"],
                    "filename": filename,
                    "sha256": model_sha256,
                }
            elif row.action == "remove":
                change = {"action": "remove", "id": request["lora_id"]}
            else:
                change = {
                    "action": "edit",
                    "id": request["lora_id"],
                    "label": request["display_name"],
                    "trigger_word": request["trigger_word"],
                }
            # Prepare every publication before any commit. Writer first establishes the journal.
            order = [row for row in replicas if row.instance_id == writer_id] + [
                row for row in replicas if row.instance_id != writer_id
            ]
            for profile in order:
                instance_id = str(profile.instance_id)
                adapter = self.container.comfyui_instances.get(instance_id)
                prepared_result = await self._request(
                    adapter,
                    "POST",
                    f"/operations/{operation_id}/prepare",
                    json_body={
                        "source_path": source_id,
                        "expected_revision": row.expected_revision_json,
                        "change": change,
                        "publication_id": publication_id,
                        "published_at": published_at,
                    },
                )
                observed = await self._candidate(adapter, operation_id, source_id, instance_id)
                if prepared_result.get("candidate_revision") != observed:
                    raise AppError(
                        "lora_candidate_mismatch",
                        "The prepared LoRA publication did not match its candidate bytes.",
                        status_code=503,
                    )
                if candidate_revision is not None and observed != candidate_revision:
                    raise AppError(
                        "lora_replicas_diverged",
                        "The ComfyUI replicas prepared different publications.",
                        status_code=409,
                    )
                candidate_revision = observed
            assert candidate_revision is not None
            await run_blocking(
                self._update,
                operation_id,
                internal={"change": change, "candidate_revision": candidate_revision},
            )
            for profile in order:
                await self._request(
                    self.container.comfyui_instances.get(str(profile.instance_id)),
                    "POST",
                    f"/operations/{operation_id}/commit",
                    json_body={},
                )
            await self._verify_publication(source_id, candidate_revision, replicas)
            if filename is not None:
                await self._verify_model_inventory(filename, replicas, present=True)
            if row.action == "remove":
                await self._request(
                    writer, "POST", f"/operations/{operation_id}/quarantine", json_body={}
                )
                assert filename is not None
                await self._verify_model_inventory(filename, replicas, present=False)
            # Backups are discarded only after every observed revision and model inventory agrees.
            for profile in [row for row in order if row.instance_id != writer_id] + [
                row for row in order if row.instance_id == writer_id
            ]:
                await self._request(
                    self.container.comfyui_instances.get(str(profile.instance_id)),
                    "POST",
                    f"/operations/{operation_id}/finalize",
                    json_body={},
                )
                finalized += 1
            # Finalization can irreversibly delete a removed weight. Keep the
            # operation recoverable if app-side refresh or reconciliation fails.
            await self.container.registry.refresh(prune_images=False)
            await run_blocking(self._reconcile_after_success, source_id, change)
            await run_blocking(
                self._update,
                operation_id,
                status="succeeded",
                message={
                    "install": "LoRA installed.",
                    "remove": "LoRA removed.",
                    "edit": "LoRA details updated.",
                }[row.action],
                result={"revision": candidate_revision, "lora_id": change["id"]},
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
            restored = await self._rollback(
                operation_id, [str(profile.instance_id) for profile in replicas], writer_id
            )
            if not restored:
                await run_blocking(
                    self._update,
                    operation_id,
                    status="repair_required",
                    message="ComfyUI could not restore every publication and file.",
                )
                return
            raise

    def _reconcile_after_success(self, source_id: str, change: Mapping[str, Any]) -> None:
        with self.container.db.session_factory() as session:
            profiles = self.container.registry.current_replicas(session, source_id=source_id)
            if not profiles:
                return
            profile = profiles[0]
            bindings = catalog_bindings(profile.resolved_contract_json, profile.source_api_json)
            paths = prune_lora_images(
                session,
                workflow_key=logical_workflow_key(profile),
                current_bindings=bindings,
            )
            if change["action"] == "remove":
                removed_id = str(change["id"])
                source_keys = {str(row.source_key) for row in profiles}
                for preference in session.scalars(select(UserPreference)):
                    settings = json.loads(json.dumps(preference.settings_json or {}))
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
                                            if not (
                                                isinstance(entry, dict)
                                                and entry.get("id") == removed_id
                                            )
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
                    if changed:
                        preference.settings_json = settings
                        preference.revision += 1
            session.commit()
        if paths:
            self.container.assets.delete_paths(paths)

    async def recover(self) -> None:
        """Conservatively restore interrupted operations before accepting new changes."""

        def pending() -> list[LoraOperation]:
            with self.container.db.session_factory() as session:
                return list(
                    session.scalars(
                        select(LoraOperation).where(
                            LoraOperation.status.in_(("running", "repair_required"))
                        )
                    )
                )

        for row in await run_blocking(pending):
            try:
                replica_ids = [config.id for config in self.container.comfyui_instances.configs]
                capabilities = [
                    await self._request(
                        self.container.comfyui_instances.get(instance_id), "GET", "/capabilities"
                    )
                    for instance_id in replica_ids
                ]
                writer_id = next(
                    instance_id
                    for instance_id, capability in zip(replica_ids, capabilities, strict=True)
                    if capability.get("model_writer") is True
                )
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
                    candidate = row.internal_json.get("candidate_revision")
                    change = row.internal_json.get("change")
                    if not isinstance(candidate, dict) or not isinstance(change, dict):
                        raise AppError(
                            "lora_recovery_incomplete",
                            "The finalized LoRA operation lacks a candidate revision.",
                            status_code=503,
                        )
                    for instance_id in replica_ids:
                        bundle = await self._request(
                            self.container.comfyui_instances.get(instance_id),
                            "GET",
                            "/bundle",
                            params={"source_path": row.source_id},
                        )
                        if bundle.get("revision") != candidate:
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
                    await self.container.registry.refresh(prune_images=False)
                    _, profiles = await run_blocking(self._profiles, row.source_key)
                    filename = row.internal_json.get("model_filename")
                    if not isinstance(filename, str):
                        raise AppError(
                            "lora_recovery_incomplete",
                            "The finalized operation lacks its model identity.",
                            status_code=503,
                        )
                    await self._verify_model_inventory(
                        filename, profiles, present=row.action != "remove"
                    )
                    await run_blocking(self._reconcile_after_success, row.source_id, change)
                    await run_blocking(
                        self._update,
                        row.id,
                        status="succeeded",
                        message="Interrupted LoRA change finished after restart.",
                        result={"revision": candidate, "lora_id": change["id"]},
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
