from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


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


class SourceRevision(APIModel):
    publication_id: str
    workflow_sha256: str
    api_sha256: str
    manifest_sha256: str


class WorkflowSummary(APIModel):
    source_key: str
    display_name: str
    instance_id: str
    readiness: str
    available: bool
    cached: bool
    message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    revision: SourceRevision
    profile_id: str | None = None
    workflow_id: str | None = None
    workflow_version: str | None = None
    ui_graph_sha256: str | None = None
    api_graph_sha256: str | None = None
    contract_sha256: str | None = None
    contract_schema_version: str | None = None
    adapter_version: str | None = None


class WorkflowDetail(WorkflowSummary):
    interface: dict[str, Any]


class GenerationCreate(APIModel):
    source_key: str | None = None
    parameters: dict[str, Any] | None = None
    revision: SourceRevision | None = None
    prompt_assistant_run_id: str | None = None

    # Temporary compatibility envelope for the pre-publication browser/API. It resolves only to
    # a current validated publication and never revives embedded-contract discovery.
    profile_id: str | None = None
    controls: dict[str, Any] | None = None
    preset_id: str | None = None
    requested_outputs: list[str] = Field(default_factory=list)
    expected_identity: WorkflowIdentity | None = None

    @model_validator(mode="after")
    def validate_request_shape(self) -> GenerationCreate:
        if not self.source_key and not self.profile_id:
            raise ValueError("source_key is required")
        if self.source_key and self.profile_id:
            raise ValueError("send source_key, not both source_key and legacy profile_id")
        if self.parameters is not None and self.controls is not None:
            raise ValueError("send parameters, not both parameters and legacy controls")
        return self

    @property
    def public_parameters(self) -> dict[str, Any]:
        return self.parameters if self.parameters is not None else (self.controls or {})


class ValidationResult(APIModel):
    valid: bool
    effective_parameters: dict[str, Any] = Field(default_factory=dict)
    resolved_seeds: dict[str, str] = Field(default_factory=dict)
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


class DeclaredArtifactReference(APIModel):
    batch_index: int = Field(ge=0)
    filename: str | None = None
    subfolder: str = ""
    type: Literal["input", "output", "temp"] = "output"
    artifact: ArtifactSummary | None = None


class DeclaredOutputSummary(APIModel):
    schema_version: str | None = None
    id: str
    output_id: str
    # Stored pre-publication rows may contain historical role/kind values; new discovery emits
    # only the strict image-role vocabulary enforced by publication validation.
    role: str
    kind: str
    label: str | None = None
    cardinality: str
    description: str
    artifacts: list[DeclaredArtifactReference] = Field(default_factory=list)


class GenerationSummary(APIModel):
    id: str
    status: str
    workflow_display_name: str
    accepted_at: datetime
    current_stage_id: str | None = None
    current_stage_label: str | None = None
    artifact_count: int
    image_count: int
    final_artifact_count: int
    best_available_artifact_id: str | None = None
    canonical_artifact_id: str | None = None
    display_artifact: ArtifactSummary | None = None
    expected_width: int | None = None
    expected_height: int | None = None
    error_message: str | None = None
    recall_available: bool = False
    recall_unavailable_reason: str | None = None
    is_favorite: bool = False
    cancel_allowed: bool = False
    prompt_id: str | None = None
    source_key: str | None = None
    publication_id: str | None = None


class GenerationPage(APIModel):
    items: list[GenerationSummary]
    next_cursor: str | None = None


class GenerationDetail(GenerationSummary):
    workflow: WorkflowIdentity
    generation_source: dict[str, Any] = Field(default_factory=dict)
    requested_controls: dict[str, Any]
    effective_controls: dict[str, Any]
    requested_parameters: dict[str, Any] = Field(default_factory=dict)
    effective_parameters: dict[str, Any] = Field(default_factory=dict)
    resolved_seeds: dict[str, str]
    final_prompt: str
    artifacts: list[ArtifactSummary]
    declared_outputs: list[DeclaredOutputSummary] = Field(default_factory=list)
    unmapped_outputs: dict[str, Any] = Field(default_factory=dict)
    raw_history: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Any] = Field(default_factory=list)
    errors: list[Any] = Field(default_factory=list)
    comfyui_status: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]]
    error_code: str | None = None
    delete_pending: bool


class RecallResponse(APIModel):
    available: bool
    reason: str | None = None
    profile_id: str | None = None
    identity: WorkflowIdentity | None = None
    controls: dict[str, Any] = Field(default_factory=dict)
    source_key: str | None = None
    revision: SourceRevision | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    prompt_assistant: dict[str, Any] | None = None


class FavoriteSummary(APIModel):
    id: str
    created_at: datetime
    final_prompt: str
    generation: GenerationSummary


class FavoritePage(APIModel):
    items: list[FavoriteSummary]
    next_cursor: str | None = None


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
    details: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime


class ServiceStatus(APIModel):
    service: str
    available: bool
    message: str | None
    checked_at: datetime | None
