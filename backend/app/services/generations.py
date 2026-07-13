from __future__ import annotations

import base64
import copy
import json
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any, Mapping

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.compiler import CompileResult, WorkflowCompiler
from ..errors import AppError
from ..models import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    AppLock,
    Artifact,
    AuditLog,
    Generation,
    GenerationEvent,
    GenerationStatus,
    GenerationUpload,
    PromptAssistantRun,
    ServiceHealth,
    Upload,
    UploadKind,
    User,
    WorkflowProfile,
)
from ..schemas import (
    ArtifactSummary,
    GenerationCreate,
    GenerationDetail,
    GenerationPage,
    GenerationSummary,
    RecallResponse,
    ValidationResult,
    WorkflowIdentity,
)
from .assets import AssetStore
from .comfyui import ComfyUIAdapter
from .event_broker import EventBroker
from .events import add_generation_event, publish_event
from .workflow_registry import WorkflowRegistry


class GenerationService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        registry: WorkflowRegistry,
        compiler: WorkflowCompiler,
        assets: AssetStore,
        comfyui: ComfyUIAdapter,
        broker: EventBroker,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.compiler = compiler
        self.assets = assets
        self.comfyui = comfyui
        self.broker = broker

    def validate(
        self, session: Session, *, user: User, request: GenerationCreate
    ) -> ValidationResult:
        profile = self._profile_for_request(session, request)
        result = self._compile(session, user=user, profile=profile, request=request)
        return ValidationResult(
            valid=True,
            effective_controls=result.effective_controls,
            resolved_seeds=result.resolved_seeds,
            compiled_graph_sha256=result.compiled_graph_hash,
        )

    async def accept(
        self, session: Session, *, user: User, request: GenerationCreate
    ) -> GenerationSummary:
        health = session.get(ServiceHealth, "comfyui")
        if health is not None and not health.available:
            raise AppError(
                "comfyui_unavailable",
                "ComfyUI is unavailable. Existing history remains accessible.",
                status_code=503,
            )
        profile = self._profile_for_request(session, request)
        compiled = self._compile(session, user=user, profile=profile, request=request)
        uploads = self._verify_uploads(session, user, profile, compiled)
        prompt_run = self._verify_prompt_run(session, user, request.prompt_assistant_run_id)
        queue_seq = self._next_queue_sequence(session)
        generation = Generation(
            owner_id=user.id,
            status=GenerationStatus.QUEUED,
            queue_seq=queue_seq,
            workflow_profile_id=profile.id,
            workflow_id=profile.workflow_id,
            workflow_display_name=profile.display_name,
            workflow_version=profile.workflow_version,
            contract_schema_version=profile.contract_schema_version,
            adapter_version=profile.adapter_version,
            ui_graph_sha256=profile.ui_graph_sha256,
            api_graph_sha256=profile.api_graph_sha256,
            contract_sha256=profile.contract_sha256,
            resolved_contract_json=copy.deepcopy(profile.resolved_contract_json),
            requested_controls_json=compiled.requested_controls,
            effective_controls_json=compiled.effective_controls,
            resolved_seeds_json=compiled.resolved_seeds,
            selected_preset=compiled.selected_preset,
            requested_outputs_json=compiled.requested_outputs,
            final_prompt=compiled.final_prompt,
            compiled_graph_json=compiled.compiled_graph,
            compiled_graph_sha256=compiled.compiled_graph_hash,
        )
        session.add(generation)
        session.flush()
        for control_id, upload in uploads.items():
            session.add(
                GenerationUpload(
                    generation_id=generation.id,
                    upload_id=upload.id,
                    control_id=control_id,
                    sha256=upload.sha256,
                )
            )
        if prompt_run is not None:
            prompt_run.generation_id = generation.id
        event = add_generation_event(
            session,
            generation,
            "generation.queued",
            {"status": generation.status.value, "queue_seq": queue_seq},
        )
        session.commit()
        await publish_event(self.broker, event)
        with self.session_factory() as fresh:
            stored = self.get_owned(fresh, user.id, generation.id)
            return self.summary(fresh, stored)

    def _profile_for_request(self, session: Session, request: GenerationCreate) -> WorkflowProfile:
        profile = self.registry.get_current(session, request.profile_id)
        expected = request.expected_identity
        if expected and (
            expected.workflow_id != profile.workflow_id
            or expected.workflow_version != profile.workflow_version
            or expected.ui_graph_sha256 != profile.ui_graph_sha256
            or expected.api_graph_sha256 != profile.api_graph_sha256
            or expected.contract_sha256 != profile.contract_sha256
        ):
            raise AppError(
                "workflow_unavailable",
                "The exact recalled workflow version is no longer registered.",
                status_code=409,
            )
        return profile

    def _compile(
        self,
        session: Session,
        *,
        user: User,
        profile: WorkflowProfile,
        request: GenerationCreate,
    ) -> CompileResult:
        runtime = profile.runtime_snapshot_json
        object_info = runtime.get("object_info", {}) if isinstance(runtime, dict) else {}
        result = self.compiler.compile(
            contract=profile.resolved_contract_json,
            api_document=profile.source_api_json,
            object_info=object_info,
            requested_controls=request.controls,
            preset_id=request.preset_id,
            requested_outputs=request.requested_outputs,
        )
        self._verify_uploads(session, user, profile, result)
        return result

    def _verify_uploads(
        self,
        session: Session,
        user: User,
        profile: WorkflowProfile,
        compiled: CompileResult,
    ) -> dict[str, Upload]:
        controls = {
            str(item.get("id")): item
            for item in profile.resolved_contract_json.get("controls", [])
            if isinstance(item, dict)
        }
        result: dict[str, Upload] = {}
        for control_id, upload_id in compiled.selected_uploads.items():
            upload = session.scalar(
                select(Upload).where(Upload.id == upload_id, Upload.owner_id == user.id)
            )
            if upload is None:
                raise AppError(
                    "control_validation_failed",
                    "An uploaded source asset is unavailable.",
                    status_code=422,
                    fields={control_id: "Upload not found."},
                )
            expected = controls.get(control_id, {}).get("type")
            expected_kind = UploadKind.MASK if expected == "mask_upload" else UploadKind.IMAGE
            if upload.kind != expected_kind:
                raise AppError(
                    "control_validation_failed",
                    "An uploaded source asset has the wrong type.",
                    status_code=422,
                    fields={control_id: "Wrong upload type."},
                )
            result[control_id] = upload
        return result

    @staticmethod
    def _verify_prompt_run(
        session: Session, user: User, run_id: str | None
    ) -> PromptAssistantRun | None:
        if not run_id:
            return None
        run = session.scalar(
            select(PromptAssistantRun).where(
                PromptAssistantRun.id == run_id,
                PromptAssistantRun.owner_id == user.id,
            )
        )
        if run is None or run.generation_id is not None:
            raise AppError(
                "prompt_assistant_invalid",
                "Prompt Assistant provenance is unavailable for this request.",
                status_code=422,
            )
        return run

    @staticmethod
    def _next_queue_sequence(session: Session) -> int:
        lock = session.get(AppLock, "queue_sequence")
        if lock is None:
            lock = AppLock(key="queue_sequence", integer_value=0)
            session.add(lock)
            session.flush()
        lock.integer_value += 1
        session.flush()
        return lock.integer_value

    @staticmethod
    def get_owned(session: Session, owner_id: str, generation_id: str) -> Generation:
        generation = session.scalar(
            select(Generation).where(
                Generation.id == generation_id,
                Generation.owner_id == owner_id,
            )
        )
        if generation is None:
            raise AppError("not_found", "Generation was not found.", status_code=404)
        return generation

    def list_page(
        self,
        session: Session,
        *,
        owner_id: str,
        cursor: str | None,
        limit: int,
    ) -> GenerationPage:
        limit = max(1, min(limit, 60))
        statement = select(Generation).where(Generation.owner_id == owner_id)
        if cursor:
            cursor_time, cursor_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    Generation.accepted_at < cursor_time,
                    and_(Generation.accepted_at == cursor_time, Generation.id < cursor_id),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(Generation.accepted_at.desc(), Generation.id.desc()).limit(limit + 1)
            )
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = (
            _encode_cursor(rows[-1].accepted_at, rows[-1].id) if has_more and rows else None
        )
        return GenerationPage(
            items=[self.summary(session, generation) for generation in rows],
            next_cursor=next_cursor,
        )

    def summary(self, session: Session, generation: Generation) -> GenerationSummary:
        display = self._display_artifact(session, generation)
        exact = self._exact_profile(session, generation)
        return GenerationSummary(
            id=generation.id,
            status=generation.status.value,
            workflow_display_name=generation.workflow_display_name,
            accepted_at=generation.accepted_at,
            current_stage_id=generation.current_stage_id,
            current_stage_label=generation.current_stage_label,
            artifact_count=generation.artifact_count,
            final_artifact_count=generation.final_artifact_count,
            best_available_artifact_id=generation.best_available_artifact_id,
            canonical_artifact_id=generation.canonical_artifact_id,
            display_artifact=self.artifact_summary(display) if display else None,
            error_message=generation.error_message,
            recall_available=exact is not None,
            recall_unavailable_reason=(
                None if exact else "Original workflow version is not currently available."
            ),
        )

    def detail(self, session: Session, generation: Generation) -> GenerationDetail:
        summary = self.summary(session, generation)
        artifacts = list(
            session.scalars(
                select(Artifact)
                .where(Artifact.generation_id == generation.id)
                .order_by(Artifact.sequence, Artifact.batch_index, Artifact.available_at)
            )
        )
        events = list(
            session.scalars(
                select(GenerationEvent)
                .where(GenerationEvent.generation_id == generation.id)
                .order_by(GenerationEvent.id)
            )
        )
        return GenerationDetail(
            **summary.model_dump(),
            workflow=WorkflowIdentity(
                workflow_id=generation.workflow_id,
                workflow_version=generation.workflow_version,
                ui_graph_sha256=generation.ui_graph_sha256,
                api_graph_sha256=generation.api_graph_sha256,
                contract_sha256=generation.contract_sha256,
            ),
            requested_controls=generation.requested_controls_json,
            effective_controls=generation.effective_controls_json,
            resolved_seeds={key: int(value) for key, value in generation.resolved_seeds_json.items()},
            final_prompt=generation.final_prompt,
            artifacts=[self.artifact_summary(item) for item in artifacts],
            events=[
                {
                    "id": event.id,
                    "type": event.event_type,
                    "payload": event.payload_json,
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ],
            error_code=generation.error_code,
            cancel_allowed=generation.status in ACTIVE_STATUSES,
            delete_pending=generation.pending_delete,
        )

    @staticmethod
    def artifact_summary(artifact: Artifact) -> ArtifactSummary:
        return ArtifactSummary(
            id=artifact.id,
            output_id=artifact.output_id,
            role=artifact.role,
            kind=artifact.kind,
            state=artifact.state.value,
            sequence=artifact.sequence,
            batch_index=artifact.batch_index,
            width=artifact.width,
            height=artifact.height,
            canonical=artifact.canonical,
            best_available=artifact.best_available,
            content_url=f"/api/artifacts/{artifact.id}/content",
            thumbnail_url=(
                f"/api/artifacts/{artifact.id}/thumbnail" if artifact.thumbnail_path else None
            ),
            available_at=artifact.available_at,
        )

    @staticmethod
    def _display_artifact(session: Session, generation: Generation) -> Artifact | None:
        target_id = generation.canonical_artifact_id or generation.best_available_artifact_id
        if target_id:
            artifact = session.scalar(
                select(Artifact).where(
                    Artifact.id == target_id,
                    Artifact.generation_id == generation.id,
                )
            )
            if artifact:
                return artifact
        return session.scalar(
            select(Artifact)
            .where(Artifact.generation_id == generation.id, Artifact.kind == "image")
            .order_by(Artifact.sequence.desc(), Artifact.batch_index, Artifact.available_at.desc())
            .limit(1)
        )

    def recall(self, session: Session, generation: Generation) -> RecallResponse:
        profile = self._exact_profile(session, generation)
        if profile is None:
            return RecallResponse(
                available=False,
                reason="Original workflow version is not currently available.",
            )
        owner = session.get(User, generation.owner_id)
        if owner is None:
            return RecallResponse(
                available=False,
                reason="The generation owner is no longer available.",
            )
        candidate = copy.deepcopy(generation.requested_controls_json)
        candidate.update(generation.resolved_seeds_json)
        candidate["prompt.text"] = generation.final_prompt
        request = GenerationCreate(
            profile_id=profile.id,
            controls=candidate,
            preset_id=generation.selected_preset,
            requested_outputs=list(generation.requested_outputs_json),
        )
        try:
            compiled = self._compile(
                session,
                user=owner,
                profile=profile,
                request=request,
            )
            exact = (
                compiled.effective_controls == generation.effective_controls_json
                and compiled.compiled_graph_hash == generation.compiled_graph_sha256
            )
        except AppError:
            exact = False
        if not exact:
            candidate = copy.deepcopy(generation.effective_controls_json)
            candidate.update(generation.resolved_seeds_json)
            candidate["prompt.text"] = generation.final_prompt
            request = GenerationCreate(
                profile_id=profile.id,
                controls=candidate,
                requested_outputs=list(generation.requested_outputs_json),
            )
            try:
                compiled = self._compile(
                    session,
                    user=owner,
                    profile=profile,
                    request=request,
                )
                exact = (
                    compiled.effective_controls == generation.effective_controls_json
                    and compiled.compiled_graph_hash == generation.compiled_graph_sha256
                )
            except AppError:
                exact = False
        if not exact:
            return RecallResponse(
                available=False,
                reason="Historical controls no longer compile to the exact original request.",
            )
        prompt_run = session.scalar(
            select(PromptAssistantRun).where(PromptAssistantRun.generation_id == generation.id)
        )
        assistant = None
        if prompt_run:
            assistant = {
                "mode": prompt_run.mode,
                "creative_direction": prompt_run.creative_direction,
                "ollama_output": prompt_run.ollama_output,
                "model": prompt_run.model_name,
            }
        return RecallResponse(
            available=True,
            profile_id=profile.id,
            identity=WorkflowIdentity(
                workflow_id=profile.workflow_id,
                workflow_version=profile.workflow_version,
                ui_graph_sha256=profile.ui_graph_sha256,
                api_graph_sha256=profile.api_graph_sha256,
                contract_sha256=profile.contract_sha256,
            ),
            controls=candidate,
            prompt_assistant=assistant,
        )

    def _exact_profile(self, session: Session, generation: Generation) -> WorkflowProfile | None:
        return self.registry.find_exact(
            session,
            workflow_id=generation.workflow_id,
            workflow_version=generation.workflow_version,
            ui_hash=generation.ui_graph_sha256,
            api_hash=generation.api_graph_sha256,
            contract_hash=generation.contract_sha256,
        )

    async def cancel(self, session: Session, generation: Generation) -> GenerationSummary:
        if generation.status in TERMINAL_STATUSES:
            return self.summary(session, generation)
        if generation.status == GenerationStatus.QUEUED:
            generation.status = GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS
            generation.cancel_requested_at = datetime.now(UTC)
            generation.completed_at = datetime.now(UTC)
            event = add_generation_event(
                session,
                generation,
                "generation.cancelled",
                {"status": generation.status.value, "queued": True},
            )
            session.commit()
            await publish_event(self.broker, event)
            return self.summary(session, generation)
        generation.status = GenerationStatus.CANCEL_REQUESTED
        generation.cancel_requested_at = datetime.now(UTC)
        event = add_generation_event(
            session,
            generation,
            "generation.cancel_requested",
            {"status": generation.status.value},
        )
        prompt_id = generation.comfyui_prompt_id
        session.commit()
        await publish_event(self.broker, event)
        if prompt_id:
            try:
                await self.comfyui.cancel(prompt_id, running=True)
            except Exception:
                # Reconciliation worker retries and resolves the eventual terminal state.
                pass
        return self.summary(session, generation)

    async def request_delete(self, session: Session, generation: Generation) -> bool:
        if generation.status == GenerationStatus.QUEUED:
            await self.cancel(session, generation)
            self.delete_terminal(session, generation)
            return True
        if generation.status in ACTIVE_STATUSES:
            generation.pending_delete = True
            session.commit()
            await self.cancel(session, generation)
            return False
        self.delete_terminal(session, generation)
        return True

    def delete_terminal(self, session: Session, generation: Generation) -> None:
        if generation.status not in TERMINAL_STATUSES:
            raise AppError(
                "generation_active",
                "Active generation must be cancelled and reconciled before deletion.",
                status_code=409,
            )
        artifact_paths = [
            path
            for artifact in session.scalars(
                select(Artifact).where(Artifact.generation_id == generation.id)
            )
            for path in (artifact.storage_path, artifact.thumbnail_path)
            if path
        ]
        upload_ids = list(
            session.scalars(
                select(GenerationUpload.upload_id).where(
                    GenerationUpload.generation_id == generation.id
                )
            )
        )
        generation_id = generation.id
        owner_id = generation.owner_id
        session.execute(
            delete(PromptAssistantRun).where(
                PromptAssistantRun.generation_id == generation_id
            )
        )
        session.delete(generation)
        session.flush()
        upload_paths: list[str] = []
        for upload_id in upload_ids:
            remaining = session.scalar(
                select(func.count())
                .select_from(GenerationUpload)
                .where(GenerationUpload.upload_id == upload_id)
            ) or 0
            if remaining == 0:
                upload = session.get(Upload, upload_id)
                if upload:
                    upload_paths.append(upload.storage_path)
                    session.delete(upload)
        session.add(
            AuditLog(
                actor_user_id=owner_id,
                target_type="generation",
                target_id=generation_id,
                action="generation_deleted",
                metadata_json={},
            )
        )
        session.commit()
        self.assets.delete_paths(artifact_paths + upload_paths)


def _encode_cursor(accepted_at: datetime, generation_id: str) -> str:
    raw = json.dumps(
        {"accepted_at": accepted_at.isoformat(), "id": generation_id}, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        timestamp = datetime.fromisoformat(payload["accepted_at"])
        generation_id = str(payload["id"])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp, generation_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AppError("invalid_cursor", "Gallery cursor is invalid.") from exc
