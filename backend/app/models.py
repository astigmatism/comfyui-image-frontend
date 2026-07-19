from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def uuid_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    USER = "user"


class UserState(enum.StrEnum):
    ACTIVE = "active"
    DELETING = "deleting"


class WorkflowState(enum.StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    STALE = "stale"


class UploadKind(enum.StrEnum):
    IMAGE = "image"
    MASK = "mask"


class GenerationStatus(enum.StrEnum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    CANCELLED_WITH_ARTIFACTS = "cancelled_with_artifacts"
    CANCELLED_WITHOUT_ARTIFACTS = "cancelled_without_artifacts"
    FAILED_WITH_ARTIFACTS = "failed_with_artifacts"
    FAILED_WITHOUT_ARTIFACTS = "failed_without_artifacts"
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES: set[GenerationStatus] = {
    GenerationStatus.SUCCEEDED,
    GenerationStatus.CANCELLED_WITH_ARTIFACTS,
    GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS,
    GenerationStatus.FAILED_WITH_ARTIFACTS,
    GenerationStatus.FAILED_WITHOUT_ARTIFACTS,
    GenerationStatus.INTERRUPTED,
}

ACTIVE_STATUSES: set[GenerationStatus] = {
    GenerationStatus.QUEUED,
    GenerationStatus.DISPATCHING,
    GenerationStatus.RUNNING,
    GenerationStatus.CANCEL_REQUESTED,
}


class ArtifactState(enum.StrEnum):
    PROVISIONAL = "provisional"
    SUPERSEDED = "superseded"
    BEST_AVAILABLE = "best_available"
    FINAL = "final"
    PARTIAL = "partial"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    username_normalized: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.USER)
    state: Mapped[UserState] = mapped_column(
        Enum(UserState), nullable=False, default=UserState.ACTIVE
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_bootstrap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Session(Base):
    __tablename__ = "sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class LoginThrottle(Base):
    __tablename__ = "login_throttles"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    gallery_scale: Mapped[int] = mapped_column(Integer, nullable=False, default=45)
    source_ratings_json: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkflowProfile(Base):
    __tablename__ = "workflow_profiles"
    __table_args__ = (
        UniqueConstraint("identity_key", name="uq_workflow_identity"),
        Index("ix_workflow_profiles_current", "is_current", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    identity_key: Mapped[str] = mapped_column(String(500), nullable=False)
    basename: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False, default="1.0.0")
    ui_graph_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    api_graph_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ui_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_api_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resolved_contract_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    runtime_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    instance_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_key: Mapped[str | None] = mapped_column(String(64), index=True)
    source_id: Mapped[str | None] = mapped_column(String(1024))
    publication_id: Mapped[str | None] = mapped_column(String(36), index=True)
    publication_schema: Mapped[str | None] = mapped_column(String(64))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[str | None] = mapped_column(String(100))
    warnings_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    readiness: Mapped[str] = mapped_column(String(40), nullable=False, default="ready")
    state: Mapped[WorkflowState] = mapped_column(
        Enum(WorkflowState), nullable=False, default=WorkflowState.VALID
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowDiagnostic(Base):
    __tablename__ = "workflow_diagnostics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    basename: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(255))
    workflow_version: Mapped[str | None] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ServiceHealth(Base):
    __tablename__ = "service_health"

    service: Mapped[str] = mapped_column(String(32), primary_key=True)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    message: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (Index("ix_upload_owner_created", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[UploadKind] = mapped_column(Enum(UploadKind), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromptAssistantRun(Base):
    __tablename__ = "prompt_assistant_runs"
    __table_args__ = (Index("ix_prompt_run_owner_created", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    generation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("generations.id", ondelete="SET NULL"), index=True
    )
    mode: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_before: Mapped[str] = mapped_column(Text, nullable=False, default="")
    creative_direction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_name: Mapped[str | None] = mapped_column(String(255))
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    ollama_output: Mapped[str | None] = mapped_column(Text)
    raw_response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Generation(Base):
    __tablename__ = "generations"
    __table_args__ = (
        Index("ix_generations_owner_created", "owner_id", "accepted_at", "id"),
        Index("ix_generations_queue", "status", "queue_seq"),
        Index("ix_generations_timing_audit", "status", "completed_at", "id"),
        Index("ix_generations_prompt_id", "comfyui_prompt_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[GenerationStatus] = mapped_column(
        Enum(GenerationStatus), nullable=False, default=GenerationStatus.QUEUED
    )
    queue_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, default=uuid_str)
    comfyui_client_id: Mapped[str] = mapped_column(String(64), nullable=False, default=uuid_str)
    comfyui_prompt_id: Mapped[str | None] = mapped_column(String(100))

    workflow_profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    ui_graph_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    api_graph_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_contract_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    requested_controls_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    effective_controls_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resolved_seeds_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    selected_preset: Mapped[str | None] = mapped_column(String(100))
    requested_outputs_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    final_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    compiled_graph_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    compiled_graph_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_graph_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    submitted_graph_sha256: Mapped[str | None] = mapped_column(String(64))

    current_stage_id: Mapped[str | None] = mapped_column(String(100))
    current_stage_label: Mapped[str | None] = mapped_column(String(255))
    current_stage_sequence: Mapped[int | None] = mapped_column(Integer)
    progress_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    best_available_artifact_id: Mapped[str | None] = mapped_column(String(36))
    canonical_artifact_id: Mapped[str | None] = mapped_column(String(36))
    artifact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    final_artifact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    internal_diagnostics_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    generation_source_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    raw_history_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    declared_outputs_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    unmapped_outputs_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    result_warnings_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    result_errors_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    comfyui_status_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_delete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("owner_id", "generation_id", name="uq_favorite_owner_generation"),
        Index("ix_favorites_owner_created", "owner_id", "created_at", "id"),
        Index("ix_favorites_generation", "generation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    generation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generations.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationUpload(Base):
    __tablename__ = "generation_uploads"
    __table_args__ = (UniqueConstraint("generation_id", "control_id", name="uq_generation_upload"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    upload_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("uploads.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    control_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifact_generation_sequence", "generation_id", "sequence", "batch_index"),
        Index("ix_artifact_owner", "owner_id", "id"),
        UniqueConstraint(
            "generation_id",
            "output_id",
            "sequence",
            "batch_index",
            "sha256",
            name="uq_artifact_generation_output_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    generation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generations.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    output_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    state: Mapped[ArtifactState] = mapped_column(Enum(ArtifactState), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_artifact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(500), unique=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_node_id: Mapped[str | None] = mapped_column(String(100))
    source_filename: Mapped[str | None] = mapped_column(String(500))
    source_subfolder: Mapped[str | None] = mapped_column(String(500))
    source_type: Mapped[str | None] = mapped_column(String(50))
    usable_on_cancel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    usable_on_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    best_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationEvent(Base):
    __tablename__ = "generation_events"
    __table_args__ = (
        Index("ix_generation_events_owner_id", "owner_id", "id"),
        Index("ix_generation_events_generation_id", "generation_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generations.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationTimingProfile(Base):
    """Bounded, content-free timing statistics used for generation ETA lookup."""

    __tablename__ = "generation_timing_profiles"
    __table_args__ = (
        UniqueConstraint(
            "feature_version",
            "scope",
            "scope_key",
            name="uq_generation_timing_profile_scope",
        ),
        Index("ix_generation_timing_profiles_lookup", "feature_version", "scope", "scope_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feature_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    samples_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    median_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    lower_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    upper_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class GenerationTimingAuditState(Base):
    """Single bounded cursor for idle timing-profile maintenance."""

    __tablename__ = "generation_timing_audit_state"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    feature_version: Mapped[int] = mapped_column(Integer, nullable=False)
    cursor_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor_generation_id: Mapped[str | None] = mapped_column(String(36))
    backfill_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SchedulerState(Base):
    __tablename__ = "scheduler_state"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_user_id: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppLock(Base):
    """Small database-backed lock/sequence row for SQLite-safe coordination."""

    __tablename__ = "app_locks"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    integer_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token: Mapped[bytes | None] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
