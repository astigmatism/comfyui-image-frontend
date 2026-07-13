from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(APIModel):
    code: str
    message: str
    fields: dict[str, str] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(APIModel):
    error: ErrorBody


class UserPublic(APIModel):
    id: str
    username: str
    role: str
    must_change_password: bool
    created_at: datetime


class SessionInfo(APIModel):
    authenticated: bool
    user: UserPublic | None = None
    csrf_token: str | None = None
    app_title: str


class LoginRequest(APIModel):
    username: str
    password: str


class ChangePasswordRequest(APIModel):
    current_password: str | None = None
    new_password: str


class CreateUserRequest(APIModel):
    username: str
    temporary_password: str


class ResetPasswordRequest(APIModel):
    temporary_password: str


class PreferenceResponse(APIModel):
    gallery_scale: int


class PreferenceUpdate(APIModel):
    gallery_scale: int = Field(ge=0, le=100)


class WorkflowIdentity(APIModel):
    workflow_id: str
    workflow_version: str
    ui_graph_sha256: str
    api_graph_sha256: str
    contract_sha256: str


class WorkflowSummary(WorkflowIdentity):
    profile_id: str
    display_name: str
    contract_schema_version: str
    adapter_version: str
    capabilities: dict[str, Any] = Field(default_factory=dict)


class WorkflowDetail(WorkflowSummary):
    contract: dict[str, Any]


class GenerationCreate(APIModel):
    profile_id: str
    controls: dict[str, Any]
    preset_id: str | None = None
    requested_outputs: list[str] = Field(default_factory=list)
    prompt_assistant_run_id: str | None = None
    expected_identity: WorkflowIdentity | None = None


class ValidationResult(APIModel):
    valid: bool
    effective_controls: dict[str, Any] = Field(default_factory=dict)
    resolved_seeds: dict[str, int] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    compiled_graph_sha256: str | None = None


class ArtifactSummary(APIModel):
    id: str
    output_id: str
    role: str
    kind: str
    state: str
    sequence: int
    batch_index: int
    width: int | None = None
    height: int | None = None
    canonical: bool
    best_available: bool
    content_url: str
    thumbnail_url: str | None = None
    available_at: datetime


class GenerationSummary(APIModel):
    id: str
    status: str
    workflow_display_name: str
    accepted_at: datetime
    current_stage_id: str | None = None
    current_stage_label: str | None = None
    artifact_count: int
    final_artifact_count: int
    best_available_artifact_id: str | None = None
    canonical_artifact_id: str | None = None
    display_artifact: ArtifactSummary | None = None
    expected_width: int | None = None
    expected_height: int | None = None
    error_message: str | None = None
    recall_available: bool = False
    recall_unavailable_reason: str | None = None
    cancel_allowed: bool = False


class GenerationPage(APIModel):
    items: list[GenerationSummary]
    next_cursor: str | None = None


class GenerationDetail(GenerationSummary):
    workflow: WorkflowIdentity
    requested_controls: dict[str, Any]
    effective_controls: dict[str, Any]
    resolved_seeds: dict[str, int]
    final_prompt: str
    artifacts: list[ArtifactSummary]
    events: list[dict[str, Any]]
    error_code: str | None = None
    delete_pending: bool


class RecallResponse(APIModel):
    available: bool
    reason: str | None = None
    profile_id: str | None = None
    identity: WorkflowIdentity | None = None
    controls: dict[str, Any] = Field(default_factory=dict)
    prompt_assistant: dict[str, Any] | None = None


class PromptComposeRequest(APIModel):
    mode: Literal["refine", "create"]
    prompt: str = ""
    creative_direction: str


class PromptComposeResponse(APIModel):
    composition_id: str
    prompt: str
    model: str
    template_version: str


class PromptAssistantStatus(APIModel):
    available: bool
    message: str | None = None


class UploadResponse(APIModel):
    id: str
    kind: str
    mime_type: str
    width: int
    height: int
    sha256: str
    preview_url: str


class AdminDiagnostic(APIModel):
    basename: str
    accepted: bool
    workflow_id: str | None
    workflow_version: str | None
    code: str
    message: str
    checked_at: datetime


class ServiceStatus(APIModel):
    service: str
    available: bool
    message: str | None
    checked_at: datetime | None
