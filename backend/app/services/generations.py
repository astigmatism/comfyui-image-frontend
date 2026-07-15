from __future__ import annotations

import base64
import copy
import json
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, delete, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..domain.compiler import CompileResult, WorkflowCompiler
from ..domain.results import project_public_declared_outputs, project_public_result
from ..errors import AppError
from ..models import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    AppLock,
    Artifact,
    ArtifactState,
    AuditLog,
    Favorite,
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
    WorkflowState,
)
from ..schemas import (
    ArtifactSummary,
    FavoritePage,
    FavoriteSummary,
    GenerationCreate,
    GenerationDetail,
    GenerationPage,
    GenerationSummary,
    RecallResponse,
    SourceRevision,
    ValidationResult,
    WorkflowIdentity,
)
from .assets import AssetStore
from .comfyui import ComfyUIAdapter
from .event_broker import EventBroker
from .events import add_generation_event, publish_event
from .workflow_registry import WorkflowRegistry

RECALL_SOURCE_WARNING = (
    "The original generation source isn't currently available. Your currently selected source "
    "will stay selected, and compatible historical settings will be recalled."
)


@dataclass(frozen=True)
class _GenerationSummaryRow:
    id: str
    status: GenerationStatus
    workflow_display_name: str
    accepted_at: datetime
    current_stage_id: str | None
    current_stage_label: str | None
    artifact_count: int
    final_artifact_count: int
    best_available_artifact_id: str | None
    canonical_artifact_id: str | None
    error_message: str | None
    comfyui_prompt_id: str | None
    workflow_id: str
    workflow_version: str
    ui_graph_sha256: str
    api_graph_sha256: str
    contract_sha256: str
    source_key: str | None
    publication_id: str | None
    expected_width: int | None
    expected_height: int | None

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.workflow_id,
            self.workflow_version,
            self.ui_graph_sha256,
            self.api_graph_sha256,
            self.contract_sha256,
        )


@dataclass(frozen=True)
class _FavoriteSummaryRow:
    id: str
    created_at: datetime
    final_prompt: str
    generation: _GenerationSummaryRow


@dataclass(frozen=True)
class _SummaryContext:
    image_counts: Mapping[str, int]
    display_artifacts: Mapping[str, ArtifactSummary]
    favorite_generation_ids: frozenset[str]
    recallable_identities: frozenset[tuple[str, str, str, str, str]]


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
            effective_parameters=result.effective_controls,
            resolved_seeds=result.resolved_seeds,
            compiled_graph_sha256=result.compiled_graph_hash,
        )

    async def accept(
        self, session: Session, *, user: User, request: GenerationCreate
    ) -> GenerationSummary:
        health = session.get(ServiceHealth, "comfyui")
        if health is None or not health.available:
            raise AppError(
                "comfyui_unavailable",
                (
                    "ComfyUI source discovery is still loading."
                    if health is None
                    else "ComfyUI is unavailable. Existing history remains accessible."
                ),
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
            generation_source_json={
                "source_key": profile.source_key,
                "instance_id": profile.instance_id,
                "publication_id": profile.publication_id,
                "workflow_sha256": profile.ui_graph_sha256,
                "api_sha256": profile.api_graph_sha256,
                "manifest_sha256": profile.manifest_sha256,
            },
            result_warnings_json=copy.deepcopy(profile.warnings_json or []),
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
        if request.source_key:
            profile = self.registry.get_current(session, request.source_key)
        elif request.profile_id:
            profile = self.registry.get_current_by_profile(session, request.profile_id)
        else:  # Pydantic rejects this; keep the service boundary defensive.
            raise AppError("source_unavailable", "Generation source is required.", status_code=422)
        revision = request.revision
        if revision and (
            revision.publication_id != profile.publication_id
            or revision.workflow_sha256 != profile.ui_graph_sha256
            or revision.api_sha256 != profile.api_graph_sha256
            or revision.manifest_sha256 != profile.manifest_sha256
        ):
            raise AppError(
                "source_republished",
                (
                    "The selected source was republished. Review its current controls before "
                    "generating."
                ),
                status_code=409,
            )
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
            requested_controls=request.public_parameters,
            preset_id=request.preset_id,
            requested_outputs=request.requested_outputs,
        )
        uploads = self._verify_uploads(session, user, profile, result)
        inputs = {
            str(item.get("id")): item
            for item in profile.resolved_contract_json.get("inputs", [])
            if isinstance(item, dict)
        }
        for control_id, upload in uploads.items():
            if inputs.get(control_id, {}).get("type") != "image":
                continue
            requested = result.effective_controls.get(control_id)
            asset_id = requested.get("asset_id") if isinstance(requested, dict) else upload.id
            result.effective_controls[control_id] = {
                "asset_id": asset_id,
                "mime_type": upload.mime_type,
                "bytes": upload.byte_size,
                "width": upload.width,
                "height": upload.height,
                "sha256": upload.sha256,
            }
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
            for item in profile.resolved_contract_json.get("inputs", [])
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
            if expected == "image":
                media = controls[control_id].get("media")
                if not isinstance(media, dict):
                    raise AppError(
                        "source_unavailable",
                        "The accepted image input contract is unavailable.",
                        status_code=409,
                    )
                accepted_mime_types = media.get("accepted_mime_types", [])
                if upload.mime_type not in accepted_mime_types:
                    raise AppError(
                        "parameter_validation_failed",
                        "One or more published parameters are invalid.",
                        status_code=422,
                        fields={
                            control_id: "Choose a PNG, JPEG, or WebP image accepted by this source."
                        },
                    )
                if upload.byte_size > media.get("max_bytes", 0):
                    raise AppError(
                        "parameter_validation_failed",
                        "One or more published parameters are invalid.",
                        status_code=422,
                        fields={control_id: "Image exceeds this source's byte limit."},
                    )
                if upload.width > media.get("max_width", 0):
                    raise AppError(
                        "parameter_validation_failed",
                        "One or more published parameters are invalid.",
                        status_code=422,
                        fields={control_id: "Image exceeds this source's maximum width."},
                    )
                if upload.height > media.get("max_height", 0):
                    raise AppError(
                        "parameter_validation_failed",
                        "One or more published parameters are invalid.",
                        status_code=422,
                        fields={control_id: "Image exceeds this source's maximum height."},
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
        statement = select(*_summary_projection()).where(Generation.owner_id == owner_id)
        if cursor:
            cursor_time, cursor_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    Generation.accepted_at < cursor_time,
                    and_(Generation.accepted_at == cursor_time, Generation.id < cursor_id),
                )
            )
        result_rows = list(
            session.execute(
                statement.order_by(Generation.accepted_at.desc(), Generation.id.desc()).limit(
                    limit + 1
                )
            )
        )
        has_more = len(result_rows) > limit
        rows = [_summary_row(row) for row in result_rows[:limit]]
        next_cursor = (
            _encode_cursor(rows[-1].accepted_at, rows[-1].id) if has_more and rows else None
        )
        context = self._summary_context(session, owner_id=owner_id, rows=rows)
        return GenerationPage(
            items=[self._project_summary(row, context) for row in rows],
            next_cursor=next_cursor,
        )

    def list_favorites(
        self,
        session: Session,
        *,
        owner_id: str,
        cursor: str | None,
        limit: int,
    ) -> FavoritePage:
        limit = max(1, min(limit, 60))
        statement = (
            select(
                Favorite.id.label("favorite_id"),
                Favorite.created_at.label("favorite_created_at"),
                Generation.final_prompt.label("final_prompt"),
                *_summary_projection(),
            )
            .join(Generation, Favorite.generation_id == Generation.id)
            .where(Favorite.owner_id == owner_id, Generation.owner_id == owner_id)
        )
        if cursor:
            cursor_time, cursor_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    Favorite.created_at < cursor_time,
                    and_(Favorite.created_at == cursor_time, Favorite.id < cursor_id),
                )
            )
        result_rows = list(
            session.execute(
                statement.order_by(Favorite.created_at.desc(), Favorite.id.desc()).limit(limit + 1)
            )
        )
        has_more = len(result_rows) > limit
        rows = [_favorite_summary_row(row) for row in result_rows[:limit]]
        next_cursor = (
            _encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
        )
        context = self._summary_context(
            session,
            owner_id=owner_id,
            rows=[row.generation for row in rows],
            known_favorite_generation_ids=frozenset(row.generation.id for row in rows),
        )
        return FavoritePage(
            items=[
                FavoriteSummary(
                    id=row.id,
                    created_at=row.created_at,
                    final_prompt=row.final_prompt,
                    generation=self._project_summary(row.generation, context),
                )
                for row in rows
            ],
            next_cursor=next_cursor,
        )

    def _summary_context(
        self,
        session: Session,
        *,
        owner_id: str,
        rows: list[_GenerationSummaryRow],
        known_favorite_generation_ids: frozenset[str] | None = None,
    ) -> _SummaryContext:
        generation_ids = [row.id for row in rows]
        if not generation_ids:
            return _SummaryContext({}, {}, frozenset(), frozenset())

        image_counts = {
            str(generation_id): int(count)
            for generation_id, count in session.execute(
                select(Artifact.generation_id, func.count())
                .where(
                    Artifact.generation_id.in_(generation_ids),
                    Artifact.owner_id == owner_id,
                    Artifact.kind == "image",
                )
                .group_by(Artifact.generation_id)
            )
        }
        display_artifacts = self._page_display_artifacts(
            session,
            owner_id=owner_id,
            generation_ids=generation_ids,
        )
        favorite_generation_ids = known_favorite_generation_ids
        if favorite_generation_ids is None:
            favorite_generation_ids = frozenset(
                str(value)
                for value in session.scalars(
                    select(Favorite.generation_id).where(
                        Favorite.owner_id == owner_id,
                        Favorite.generation_id.in_(generation_ids),
                    )
                )
            )

        capabilities = session.scalar(
            select(ServiceHealth.capabilities_json).where(ServiceHealth.service == "comfyui")
        )
        unavailable_source_keys = {
            value
            for value in (
                capabilities.get("dependency_unavailable_source_keys", [])
                if isinstance(capabilities, dict)
                else []
            )
            if isinstance(value, str)
        }
        workflow_ids = {row.workflow_id for row in rows}
        recallable_identities = frozenset(
            (
                str(workflow_id),
                str(workflow_version),
                str(ui_hash),
                str(api_hash),
                str(contract_hash),
            )
            for (
                workflow_id,
                workflow_version,
                ui_hash,
                api_hash,
                contract_hash,
                source_key,
            ) in session.execute(
                select(
                    WorkflowProfile.workflow_id,
                    WorkflowProfile.workflow_version,
                    WorkflowProfile.ui_graph_sha256,
                    WorkflowProfile.api_graph_sha256,
                    WorkflowProfile.contract_sha256,
                    WorkflowProfile.source_key,
                ).where(
                    WorkflowProfile.workflow_id.in_(workflow_ids),
                    WorkflowProfile.is_current.is_(True),
                    WorkflowProfile.state == WorkflowState.VALID,
                )
            )
            if source_key is None or source_key not in unavailable_source_keys
        )
        return _SummaryContext(
            image_counts=image_counts,
            display_artifacts=display_artifacts,
            favorite_generation_ids=favorite_generation_ids,
            recallable_identities=recallable_identities,
        )

    @staticmethod
    def _page_display_artifacts(
        session: Session,
        *,
        owner_id: str,
        generation_ids: list[str],
    ) -> dict[str, ArtifactSummary]:
        target_id = func.coalesce(
            Generation.canonical_artifact_id,
            Generation.best_available_artifact_id,
        )
        ranked = (
            select(
                Artifact.generation_id.label("generation_id"),
                Artifact.id.label("id"),
                Artifact.output_id.label("output_id"),
                Artifact.role.label("role"),
                Artifact.kind.label("kind"),
                Artifact.state.label("state"),
                Artifact.sequence.label("sequence"),
                Artifact.batch_index.label("batch_index"),
                Artifact.width.label("width"),
                Artifact.height.label("height"),
                Artifact.canonical.label("canonical"),
                Artifact.best_available.label("best_available"),
                Artifact.thumbnail_path.label("thumbnail_path"),
                Artifact.available_at.label("available_at"),
                func.row_number()
                .over(
                    partition_by=Artifact.generation_id,
                    order_by=(
                        case((Artifact.id == target_id, 0), else_=1),
                        Artifact.sequence.desc(),
                        Artifact.batch_index,
                        Artifact.available_at.desc(),
                    ),
                )
                .label("summary_rank"),
            )
            .join(Generation, Generation.id == Artifact.generation_id)
            .where(
                Artifact.generation_id.in_(generation_ids),
                Artifact.owner_id == owner_id,
                Generation.owner_id == owner_id,
                or_(Artifact.id == target_id, Artifact.kind == "image"),
            )
            .subquery()
        )
        result: dict[str, ArtifactSummary] = {}
        for row in session.execute(select(ranked).where(ranked.c.summary_rank == 1)):
            state = row.state.value if isinstance(row.state, ArtifactState) else str(row.state)
            artifact = ArtifactSummary(
                id=str(row.id),
                output_id=str(row.output_id),
                role=str(row.role),
                kind=str(row.kind),
                state=state,
                sequence=int(row.sequence),
                batch_index=int(row.batch_index),
                width=row.width,
                height=row.height,
                canonical=bool(row.canonical),
                best_available=bool(row.best_available),
                content_url=f"/api/artifacts/{row.id}/content",
                thumbnail_url=(
                    f"/api/artifacts/{row.id}/thumbnail" if row.thumbnail_path else None
                ),
                available_at=row.available_at,
            )
            result[str(row.generation_id)] = artifact
        return result

    @staticmethod
    def _project_summary(
        row: _GenerationSummaryRow,
        context: _SummaryContext,
    ) -> GenerationSummary:
        exact = row.identity in context.recallable_identities
        status = row.status.value if isinstance(row.status, GenerationStatus) else str(row.status)
        return GenerationSummary(
            id=row.id,
            status=status,
            workflow_display_name=row.workflow_display_name,
            accepted_at=row.accepted_at,
            current_stage_id=row.current_stage_id,
            current_stage_label=row.current_stage_label,
            artifact_count=row.artifact_count,
            image_count=context.image_counts.get(row.id, 0),
            final_artifact_count=row.final_artifact_count,
            best_available_artifact_id=row.best_available_artifact_id,
            canonical_artifact_id=row.canonical_artifact_id,
            display_artifact=context.display_artifacts.get(row.id),
            expected_width=_positive_int(row.expected_width),
            expected_height=_positive_int(row.expected_height),
            error_message=row.error_message,
            recall_available=True,
            recall_source_available=exact,
            recall_warning=None if exact else RECALL_SOURCE_WARNING,
            recall_unavailable_reason=None,
            is_favorite=row.id in context.favorite_generation_ids,
            cancel_allowed=(
                row.status in ACTIVE_STATUSES and row.status != GenerationStatus.CANCEL_REQUESTED
            ),
            prompt_id=row.comfyui_prompt_id,
            source_key=row.source_key,
            publication_id=row.publication_id,
        )

    def add_favorite(
        self, session: Session, *, owner_id: str, generation_id: str
    ) -> FavoriteSummary:
        generation = self.get_owned(session, owner_id, generation_id)
        favorite = session.scalar(
            select(Favorite).where(
                Favorite.owner_id == owner_id,
                Favorite.generation_id == generation.id,
            )
        )
        if favorite is None:
            favorite = Favorite(owner_id=owner_id, generation_id=generation.id)
            session.add(favorite)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                favorite = session.scalar(
                    select(Favorite).where(
                        Favorite.owner_id == owner_id,
                        Favorite.generation_id == generation.id,
                    )
                )
                if favorite is None:
                    raise
        return self.favorite_summary(session, favorite, generation)

    def remove_favorite(self, session: Session, *, owner_id: str, generation_id: str) -> None:
        generation = self.get_owned(session, owner_id, generation_id)
        session.execute(
            delete(Favorite).where(
                Favorite.owner_id == owner_id,
                Favorite.generation_id == generation.id,
            )
        )
        session.commit()

    def favorite_summary(
        self, session: Session, favorite: Favorite, generation: Generation
    ) -> FavoriteSummary:
        return FavoriteSummary(
            id=favorite.id,
            created_at=favorite.created_at,
            final_prompt=generation.final_prompt,
            generation=self.summary(session, generation),
        )

    def summary(self, session: Session, generation: Generation) -> GenerationSummary:
        display = self._display_artifact(session, generation)
        exact = self._exact_profile(session, generation)
        expected_width, expected_height = self._expected_dimensions(generation)
        image_count = (
            session.scalar(
                select(func.count())
                .select_from(Artifact)
                .where(Artifact.generation_id == generation.id, Artifact.kind == "image")
            )
            or 0
        )
        return GenerationSummary(
            id=generation.id,
            status=generation.status.value,
            workflow_display_name=generation.workflow_display_name,
            accepted_at=generation.accepted_at,
            current_stage_id=generation.current_stage_id,
            current_stage_label=generation.current_stage_label,
            artifact_count=generation.artifact_count,
            image_count=image_count,
            final_artifact_count=generation.final_artifact_count,
            best_available_artifact_id=generation.best_available_artifact_id,
            canonical_artifact_id=generation.canonical_artifact_id,
            display_artifact=self.artifact_summary(display) if display else None,
            expected_width=expected_width,
            expected_height=expected_height,
            error_message=generation.error_message,
            recall_available=True,
            recall_source_available=exact is not None,
            recall_warning=None if exact is not None else RECALL_SOURCE_WARNING,
            recall_unavailable_reason=None,
            is_favorite=session.scalar(
                select(Favorite.id).where(
                    Favorite.owner_id == generation.owner_id,
                    Favorite.generation_id == generation.id,
                )
            )
            is not None,
            cancel_allowed=(
                generation.status in ACTIVE_STATUSES
                and generation.status != GenerationStatus.CANCEL_REQUESTED
            ),
            prompt_id=generation.comfyui_prompt_id,
            source_key=(generation.generation_source_json or {}).get("source_key"),
            publication_id=(generation.generation_source_json or {}).get("publication_id"),
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
        artifact_summaries = [self.artifact_summary(item) for item in artifacts]
        declared_output_order = [
            str(item.get("id", item.get("output_id")))
            for item in generation.resolved_contract_json.get("outputs", [])
            if isinstance(item, Mapping) and isinstance(item.get("id", item.get("output_id")), str)
        ]
        return GenerationDetail(
            **summary.model_dump(),
            workflow=WorkflowIdentity(
                workflow_id=generation.workflow_id,
                workflow_version=generation.workflow_version,
                ui_graph_sha256=generation.ui_graph_sha256,
                api_graph_sha256=generation.api_graph_sha256,
                contract_sha256=generation.contract_sha256,
            ),
            generation_source=copy.deepcopy(generation.generation_source_json or {}),
            requested_controls=generation.requested_controls_json,
            effective_controls=generation.effective_controls_json,
            requested_parameters=generation.requested_controls_json,
            effective_parameters=generation.effective_controls_json,
            input_definitions=self._input_definitions(generation),
            resolved_seeds={
                key: str(value) for key, value in generation.resolved_seeds_json.items()
            },
            final_prompt=generation.final_prompt,
            artifacts=artifact_summaries,
            declared_outputs=project_public_declared_outputs(
                generation.declared_outputs_json or {},
                output_order=declared_output_order,
                artifacts=[item.model_dump() for item in artifact_summaries],
            ),
            unmapped_outputs=copy.deepcopy(generation.unmapped_outputs_json or {}),
            raw_history=_public_raw_history(generation.raw_history_json or {}),
            warnings=copy.deepcopy(generation.result_warnings_json or []),
            errors=copy.deepcopy(generation.result_errors_json or []),
            comfyui_status=copy.deepcopy(generation.comfyui_status_json or {}),
            events=[
                {
                    "id": event.id,
                    "type": event.event_type,
                    "payload": _public_mapping(event.payload_json),
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ],
            error_code=generation.error_code,
            delete_pending=generation.pending_delete,
        )

    @staticmethod
    def _input_definitions(generation: Generation) -> list[dict[str, Any]]:
        contract = generation.resolved_contract_json or {}
        inputs = contract.get("inputs") or contract.get("controls") or []
        public_keys = {
            "id",
            "type",
            "label",
            "description",
            "semantic_role",
            "required",
            "advanced",
            "group",
            "order",
            "choices",
        }
        return [
            {key: copy.deepcopy(value) for key, value in item.items() if key in public_keys}
            for item in inputs
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        ]

    @staticmethod
    def _expected_dimensions(generation: Generation) -> tuple[int | None, int | None]:
        width: int | None = None
        height: int | None = None
        for control in generation.resolved_contract_json.get("inputs", []):
            if not isinstance(control, Mapping):
                continue
            value = generation.effective_controls_json.get(str(control.get("id")))
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                continue
            if control.get("semantic_role") == "width":
                width = value
            elif control.get("semantic_role") == "height":
                height = value
        if width is not None or height is not None:
            return width, height
        # Historical embedded-contract generations remain readable after the publication
        # migration; retain their resolution-card hint without reviving legacy discovery.
        for control in generation.resolved_contract_json.get("controls", []):
            if not isinstance(control, Mapping) or control.get("type") != "resolution":
                continue
            value = generation.effective_controls_json.get(str(control.get("id")))
            if not isinstance(value, Mapping):
                continue
            old_width, old_height = value.get("width"), value.get("height")
            if (
                isinstance(old_width, int)
                and not isinstance(old_width, bool)
                and isinstance(old_height, int)
                and not isinstance(old_height, bool)
            ):
                return old_width, old_height
        return None, None

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
        candidate = copy.deepcopy(generation.effective_controls_json)
        image_input_ids = {
            str(item.get("id"))
            for item in generation.resolved_contract_json.get("inputs", [])
            if isinstance(item, Mapping) and item.get("type") == "image"
        }
        for input_id in image_input_ids:
            value = candidate.get(input_id)
            asset_id = value.get("asset_id") if isinstance(value, Mapping) else None
            if isinstance(asset_id, str) and asset_id:
                candidate[input_id] = {"asset_id": asset_id}
        candidate.update({key: str(value) for key, value in generation.resolved_seeds_json.items()})
        identity = WorkflowIdentity(
            workflow_id=generation.workflow_id,
            workflow_version=generation.workflow_version,
            ui_graph_sha256=generation.ui_graph_sha256,
            api_graph_sha256=generation.api_graph_sha256,
            contract_sha256=generation.contract_sha256,
        )
        source_data = generation.generation_source_json or {}
        source_key = source_data.get("source_key")
        if not isinstance(source_key, str):
            source_key = None
        revision_values = {
            "publication_id": source_data.get("publication_id"),
            "workflow_sha256": source_data.get("workflow_sha256"),
            "api_sha256": source_data.get("api_sha256"),
            "manifest_sha256": source_data.get("manifest_sha256"),
        }
        historical_revision = (
            SourceRevision(**revision_values)
            if all(isinstance(value, str) and value for value in revision_values.values())
            else None
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
        historical = {
            "identity": identity,
            "controls": candidate,
            "source_key": source_key,
            "revision": historical_revision,
            "parameters": candidate,
            "input_definitions": self._input_definitions(generation),
            "prompt_assistant": assistant,
        }
        profile = self._exact_profile(session, generation)
        if profile is None or not profile.source_key or not profile.publication_id:
            return RecallResponse(
                available=True,
                source_available=False,
                reason=RECALL_SOURCE_WARNING,
                **historical,
            )
        owner = session.get(User, generation.owner_id)
        if owner is None:
            return RecallResponse(
                available=False,
                reason="The generation owner is no longer available.",
            )
        revision = SourceRevision(
            publication_id=profile.publication_id,
            workflow_sha256=profile.ui_graph_sha256,
            api_sha256=profile.api_graph_sha256,
            manifest_sha256=profile.manifest_sha256 or profile.contract_sha256,
        )
        request = GenerationCreate(
            source_key=profile.source_key,
            parameters=candidate,
            revision=revision,
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
                available=True,
                source_available=False,
                reason=RECALL_SOURCE_WARNING,
                **historical,
            )
        return RecallResponse(
            available=True,
            source_available=True,
            profile_id=profile.id,
            identity=identity,
            controls=candidate,
            source_key=profile.source_key,
            revision=revision,
            parameters=candidate,
            input_definitions=self._input_definitions(generation),
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

    async def cancel(self, session: Session, generation: Generation) -> GenerationSummary | None:
        if generation.status in TERMINAL_STATUSES:
            return self.summary(session, generation)
        if generation.status == GenerationStatus.QUEUED:
            generation_id = generation.id
            owner_id = generation.owner_id
            generation.status = GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS
            generation.cancel_requested_at = datetime.now(UTC)
            generation.completed_at = datetime.now(UTC)
            self.delete_terminal(session, generation)
            await self.broker.publish(
                owner_id,
                {
                    "id": None,
                    "type": "generation.deleted",
                    "generation_id": generation_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "payload": {"reason": "queued_cancellation"},
                },
            )
            return None
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
            with suppress(Exception):
                await self.comfyui.cancel(prompt_id, running=True)
        return self.summary(session, generation)

    async def request_delete(self, session: Session, generation: Generation) -> bool:
        if generation.status == GenerationStatus.QUEUED:
            await self.cancel(session, generation)
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
            delete(PromptAssistantRun).where(PromptAssistantRun.generation_id == generation_id)
        )
        session.delete(generation)
        session.flush()
        upload_paths: list[str] = []
        for upload_id in upload_ids:
            remaining = (
                session.scalar(
                    select(func.count())
                    .select_from(GenerationUpload)
                    .where(GenerationUpload.upload_id == upload_id)
                )
                or 0
            )
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


def _summary_projection() -> tuple[Any, ...]:
    inputs = (
        func.json_each(Generation.resolved_contract_json, "$.inputs")
        .table_valued("value")
        .alias("summary_inputs")
    )
    controls = (
        func.json_each(Generation.resolved_contract_json, "$.controls")
        .table_valued("value")
        .alias("summary_controls")
    )

    def current_dimension(role: str) -> Any:
        input_id = func.json_extract(inputs.c.value, "$.id")
        path = _json_key_path(input_id)
        value = func.json_extract(Generation.effective_controls_json, path)
        return (
            select(value)
            .select_from(inputs)
            .where(
                func.json_extract(inputs.c.value, "$.semantic_role") == role,
                func.json_type(Generation.effective_controls_json, path) == "integer",
                value > 0,
            )
            .limit(1)
            .correlate(Generation)
            .scalar_subquery()
        )

    def historical_dimension(axis: str) -> Any:
        control_id = func.json_extract(controls.c.value, "$.id")
        path = _json_key_path(control_id).op("||")(literal(f".{axis}"))
        value = func.json_extract(Generation.effective_controls_json, path)
        return (
            select(value)
            .select_from(controls)
            .where(
                func.json_extract(controls.c.value, "$.type") == "resolution",
                func.json_type(Generation.effective_controls_json, path) == "integer",
                value > 0,
            )
            .limit(1)
            .correlate(Generation)
            .scalar_subquery()
        )

    return (
        Generation.id.label("id"),
        Generation.status.label("status"),
        Generation.workflow_display_name.label("workflow_display_name"),
        Generation.accepted_at.label("accepted_at"),
        Generation.current_stage_id.label("current_stage_id"),
        Generation.current_stage_label.label("current_stage_label"),
        Generation.artifact_count.label("artifact_count"),
        Generation.final_artifact_count.label("final_artifact_count"),
        Generation.best_available_artifact_id.label("best_available_artifact_id"),
        Generation.canonical_artifact_id.label("canonical_artifact_id"),
        Generation.error_message.label("error_message"),
        Generation.comfyui_prompt_id.label("comfyui_prompt_id"),
        Generation.workflow_id.label("workflow_id"),
        Generation.workflow_version.label("workflow_version"),
        Generation.ui_graph_sha256.label("ui_graph_sha256"),
        Generation.api_graph_sha256.label("api_graph_sha256"),
        Generation.contract_sha256.label("contract_sha256"),
        func.json_extract(Generation.generation_source_json, "$.source_key").label("source_key"),
        func.json_extract(Generation.generation_source_json, "$.publication_id").label(
            "publication_id"
        ),
        func.coalesce(current_dimension("width"), historical_dimension("width")).label(
            "expected_width"
        ),
        func.coalesce(current_dimension("height"), historical_dimension("height")).label(
            "expected_height"
        ),
    )


def _json_key_path(key: Any) -> Any:
    escaped = func.replace(key, literal('"'), literal('\\"'))
    return literal('$."').op("||")(escaped).op("||")(literal('"'))


def _summary_row(row: Any) -> _GenerationSummaryRow:
    values = row._mapping
    return _GenerationSummaryRow(
        id=str(values["id"]),
        status=values["status"],
        workflow_display_name=str(values["workflow_display_name"]),
        accepted_at=values["accepted_at"],
        current_stage_id=values["current_stage_id"],
        current_stage_label=values["current_stage_label"],
        artifact_count=int(values["artifact_count"]),
        final_artifact_count=int(values["final_artifact_count"]),
        best_available_artifact_id=values["best_available_artifact_id"],
        canonical_artifact_id=values["canonical_artifact_id"],
        error_message=values["error_message"],
        comfyui_prompt_id=values["comfyui_prompt_id"],
        workflow_id=str(values["workflow_id"]),
        workflow_version=str(values["workflow_version"]),
        ui_graph_sha256=str(values["ui_graph_sha256"]),
        api_graph_sha256=str(values["api_graph_sha256"]),
        contract_sha256=str(values["contract_sha256"]),
        source_key=values["source_key"],
        publication_id=values["publication_id"],
        expected_width=values["expected_width"],
        expected_height=values["expected_height"],
    )


def _favorite_summary_row(row: Any) -> _FavoriteSummaryRow:
    values = row._mapping
    return _FavoriteSummaryRow(
        id=str(values["favorite_id"]),
        created_at=values["favorite_created_at"],
        final_prompt=str(values["final_prompt"]),
        generation=_summary_row(row),
    )


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _encode_cursor(accepted_at: datetime, generation_id: str) -> str:
    raw = json.dumps(
        {"accepted_at": accepted_at.isoformat(), "id": generation_id}, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _public_raw_history(history: Mapping[str, Any]) -> dict[str, Any]:
    """Remove only graph-bearing root envelopes from an otherwise exhaustive history result."""

    graph_envelopes = {
        "api_graph",
        "extra_data",
        "graph",
        "prompt",
        "prompt_graph",
        "submitted_graph",
        "workflow",
    }
    return {
        str(key): copy.deepcopy(value)
        for key, value in history.items()
        if str(key).casefold().replace("-", "_") not in graph_envelopes
    }


def _public_mapping(value: Any) -> dict[str, Any]:
    projected = project_public_result(value)
    return projected if isinstance(projected, dict) else {}


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
        raise AppError("invalid_cursor", "Pagination cursor is invalid.") from exc
