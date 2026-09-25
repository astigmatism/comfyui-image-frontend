from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from .config import COMFYUI_INSTANCE_ID_PATTERN
from .domain.prompt_instructions import DEFAULT_PROMPT_INSTRUCTIONS
from .domain.source_metadata import GenerationSourceMetadata, TechnicalInventoryMetadata


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


class SourceSettings(APIModel):
    values: dict[str, Any] = Field(default_factory=dict)
    explicitInputIds: list[str] = Field(default_factory=list)
    selectedPreset: str | None = None
    revision: dict[str, Any] | None = None
    interface: dict[str, Any] | None = None


class PromptGenerationSettings(APIModel):
    @model_validator(mode="before")
    @classmethod
    def discard_runtime_preference(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key != "runtime_id"}
        return value

    previous_assistant_mode: Literal["create", "refine"] | None = None
    enabled: bool = False
    active_source: str | None = None
    sources: dict[str, SourceSettings] = Field(default_factory=dict)


class SharedSettings(APIModel):
    gallery_layout: Literal["grouped", "classic"] = "grouped"
    prompt_generation: PromptGenerationSettings = Field(default_factory=PromptGenerationSettings)
    active_source: str | None = None
    sources: dict[str, SourceSettings] = Field(default_factory=dict)
    model_selections: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    quantity: int = Field(default=1, ge=1, le=16)
    control_sections: dict[str, bool] = Field(default_factory=dict)
    recent_resolutions: dict[str, list[dict[str, int]]] = Field(default_factory=dict)
    creative_direction: str = ""
    assistant_mode: Literal["create", "refine"] = "refine"
    assistant_think: bool = True
    assistant_instructions: dict[str, str] = Field(default_factory=dict)
    use_creative_direction: bool = False
    max_generations: int | None = Field(default=200, ge=1, le=1_000_000)

    @model_validator(mode="before")
    @classmethod
    def discard_runtime_preference(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key != "runtime_id"}
        return value

    @model_validator(mode="after")
    def bounded(self) -> SharedSettings:
        if len(self.model_dump_json()) > 2_000_000:
            raise ValueError("Saved settings exceed the size limit")
        if any(len(value) > 8000 for value in self.assistant_instructions.values()):
            raise ValueError("Prompt instructions exceed the size limit")
        return self


class PreferenceResponse(APIModel):
    revision: int = 0
    settings_initialized: bool = False
    settings: SharedSettings = Field(default_factory=SharedSettings)
    gallery_scale: int
    source_ratings: dict[str, int] = Field(default_factory=dict)
    source_colors: dict[str, str] = Field(default_factory=dict)
    checkpoint_tiers: dict[str, dict[str, dict[str, list[str]]]] = Field(default_factory=dict)


class PreferenceUpdate(APIModel):
    expected_revision: int | None = Field(default=None, ge=0)
    import_if_empty: bool = False
    settings: SharedSettings | None = None
    gallery_scale: int | None = Field(default=None, ge=0, le=100)
    source_ratings: dict[str, StrictInt] | None = None
    source_colors: dict[str, str] | None = None
    checkpoint_tiers: dict[StrictStr, dict[StrictStr, dict[StrictStr, list[StrictStr]]]] | None = (
        None
    )

    @field_validator("source_ratings")
    @classmethod
    def validate_source_ratings(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return value
        if len(value) > 500:
            raise ValueError("source_ratings cannot contain more than 500 entries")
        for key, rating in value.items():
            if not key or key != key.strip() or len(key) > 256:
                raise ValueError("source rating keys must be 1 to 256 non-whitespace characters")
            if isinstance(rating, bool) or rating < 1 or rating > 5:
                raise ValueError("source ratings must be integers from 1 to 5")
        return value

    @field_validator("source_colors")
    @classmethod
    def validate_source_colors(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return value
        if len(value) > 500:
            raise ValueError("source_colors cannot contain more than 500 entries")
        for key, color in value.items():
            if not key or key != key.strip() or len(key) > 256:
                raise ValueError("source color keys must be 1 to 256 non-whitespace characters")
            normalized = str(color or "").strip()
            if not normalized or normalized.lower() == "none":
                value[key] = ""
                continue
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", normalized):
                raise ValueError("source colors must be #rrggbb hex values or empty")
            value[key] = normalized.lower()
        return value

    @field_validator("checkpoint_tiers")
    @classmethod
    def validate_checkpoint_tiers(
        cls,
        value: dict[str, dict[str, dict[str, list[str]]]] | None,
    ) -> dict[str, dict[str, dict[str, list[str]]]] | None:
        if value is None:
            return value
        if len(value) > 500:
            raise ValueError("checkpoint_tiers cannot contain more than 500 sources")
        allowed_tiers = {"top_picks", "preferred", "occasional", "unsorted"}
        total_choices = 0
        for source_key, selectors in value.items():
            if not source_key or source_key != source_key.strip() or len(source_key) > 256:
                raise ValueError(
                    "checkpoint tier source keys must be 1 to 256 non-whitespace characters"
                )
            if len(selectors) > 20:
                raise ValueError(
                    "checkpoint_tiers cannot contain more than 20 selectors per source"
                )
            for parameter_id, tiers in selectors.items():
                if (
                    not parameter_id
                    or parameter_id != parameter_id.strip()
                    or len(parameter_id) > 256
                ):
                    raise ValueError(
                        "checkpoint tier parameter keys must be 1 to 256 non-whitespace characters"
                    )
                unknown_tiers = set(tiers) - allowed_tiers
                if unknown_tiers:
                    raise ValueError("checkpoint tier names are not recognized")
                seen: set[str] = set()
                for choices in tiers.values():
                    if len(choices) > 1000:
                        raise ValueError(
                            "checkpoint tiers cannot contain more than 1000 choices per tier"
                        )
                    total_choices += len(choices)
                    for choice in choices:
                        if not choice or choice != choice.strip() or len(choice) > 512:
                            raise ValueError(
                                "checkpoint tier values must be 1 to 512 non-whitespace characters"
                            )
                        if choice in seen:
                            raise ValueError(
                                "a checkpoint value cannot appear in more than one tier"
                            )
                        seen.add(choice)
        if total_choices > 25_000:
            raise ValueError("checkpoint_tiers cannot contain more than 25000 choices")
        return value

    @model_validator(mode="after")
    def validate_update_fields(self) -> PreferenceUpdate:
        if (
            self.gallery_scale is None
            and self.source_ratings is None
            and self.source_colors is None
            and self.checkpoint_tiers is None
            and self.settings is None
        ):
            raise ValueError("at least one preference field is required")
        return self


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


class ModelSelectorChoice(APIModel):
    value: str
    label: str
    released_month: str | None = Field(
        default=None,
        pattern=r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$",
    )


class ModelSelector(APIModel):
    parameter_id: str
    label: str
    description: str
    default: str
    choices: list[ModelSelectorChoice]


class WorkflowReplica(APIModel):
    instance_id: str
    source_key: str
    revision: SourceRevision
    readiness: str
    available: bool
    cached: bool


class WorkflowSummary(APIModel):
    replicas: list[WorkflowReplica] = Field(default_factory=list)
    output_kind: Literal["image", "text"] = "image"
    source_key: str
    display_name: str
    instance_id: str
    readiness: str
    available: bool
    cached: bool
    message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    revision: SourceRevision
    generation_source: GenerationSourceMetadata | None = None
    technical_inventory: TechnicalInventoryMetadata | None = None
    model_selectors: list[ModelSelector] = Field(default_factory=list)
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


class ComfyUIInstanceStatus(APIModel):
    id: str
    label: str
    description: str | None = None
    is_default: bool
    available: bool
    message: str | None = None
    checked_at: datetime | None = None


class ComfyUIInstanceList(APIModel):
    default_instance_id: str
    text_instance_id: str | None = None
    configuration_mode: Literal["explicit", "legacy"]
    items: list[ComfyUIInstanceStatus]


class PromptAssistantSnapshot(APIModel):
    """Assistant inputs in force when a generation was submitted.

    The browser sends this with every generation request (single and every
    batch item) so recall can restore the Creative Direction section even for
    manual generations and for batch items whose prompt was composed by a run
    linked to a different item.
    """

    mode: Literal["refine", "create"]
    creative_direction: str = ""
    instructions: str | None = Field(default=None, max_length=8000)
    thinking_enabled: bool = True

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        # Unlike PromptComposeRequest, empty instructions are legal here: the
        # snapshot records what the panel had, and an empty value means "no
        # instructions in force" (assistant unavailable or cleared).
        return value.strip() if value is not None and value.strip() else None


class GenerationCreate(APIModel):
    source_key: str | None = None
    parameters: dict[str, Any] | None = None
    revision: SourceRevision | None = None
    prompt_assistant_run_id: str | None = None
    prompt_assistant: PromptAssistantSnapshot | None = None
    collection_id: str | None = None
    comfyui_instance_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=COMFYUI_INSTANCE_ID_PATTERN,
        json_schema_extra={"deprecated": True},
    )

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


class GenerationEta(APIModel):
    remaining_seconds: float = Field(ge=0)
    completion_at: datetime
    lower_seconds: float = Field(ge=0)
    upper_seconds: float = Field(ge=0)
    confidence: Literal["low", "medium", "high"]
    basis: str = Field(min_length=1, max_length=100)
    updated_at: datetime

    @model_validator(mode="after")
    def validate_interval(self) -> GenerationEta:
        if not self.lower_seconds <= self.remaining_seconds <= self.upper_seconds:
            raise ValueError("ETA interval must contain the point estimate")
        return self


class GenerationProgress(APIModel):
    kind: Literal["indeterminate", "node"]
    node_id: str | None = None
    display_node_id: str | None = None
    real_node_id: str | None = None
    parent_node_id: str | None = None
    label: str
    value: int | float | None = None
    maximum: int | float | None = None
    fraction: float | None = Field(default=None, ge=0, le=1)
    eta: GenerationEta | None = None
    updated_at: datetime


class GenerationSummary(APIModel):
    id: str
    prompt_fingerprint: str | None = None
    status: str
    workflow_display_name: str
    checkpoint_label: str | None = None
    comfyui_instance_id: str
    comfyui_instance_label: str
    accepted_at: datetime
    generation_duration_seconds: float | None = None
    current_stage_id: str | None = None
    current_stage_label: str | None = None
    progress: GenerationProgress | None = None
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
    recall_source_available: bool = False
    recall_warning: str | None = None
    recall_unavailable_reason: str | None = None
    is_favorite: bool = False
    cancel_allowed: bool = False
    prompt_id: str | None = None
    source_key: str | None = None
    publication_id: str | None = None
    collection_id: str | None = None


class GenerationPage(APIModel):
    items: list[GenerationSummary]
    next_cursor: str | None = None


class PromptGroupLookup(APIModel):
    collection_id: str | None = None
    generation_ids: list[str] = Field(min_length=1, max_length=500)


class PromptGroupSummary(APIModel):
    id: str
    generation_count: int
    previous_generation_id: str | None
    after_cursor: str


class PromptGroupMembership(APIModel):
    generation_id: str
    group: PromptGroupSummary


class PromptChangePart(APIModel):
    kind: Literal["context", "removed", "added"]
    text: str


class PromptChanges(APIModel):
    first_prompt: bool = False
    edit_count: int = 0
    snippets: list[list[PromptChangePart]] = Field(default_factory=list)
    omitted_edits: int = 0


class GenerationBatchCreate(APIModel):
    items: list[GenerationCreate] = Field(min_length=1, max_length=256)


class GenerationBatchItem(APIModel):
    generation: GenerationSummary | None = None
    error: dict[str, Any] | None = None


class GenerationBatchResult(APIModel):
    items: list[GenerationBatchItem]


class GenerationRunProgress(APIModel):
    id: str
    total_count: int
    resolved_count: int
    remaining_count: int
    succeeded_count: int
    failed_count: int
    cancelled_count: int
    completed_at: datetime | None = None
    # ETA-derived continuous progress in [0, 1]; None when no run member carries an ETA.
    completed_fraction: float | None = Field(default=None, ge=0, le=1)


class GenerationActivity(APIModel):
    run: GenerationRunProgress | None = None
    remaining_count: int = 0
    collection_remaining_counts: dict[str, int] = Field(default_factory=dict)
    collection_generation_counts: dict[str, int] = Field(default_factory=dict)


class CollectionCreate(APIModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("collection name is required")
        if len(normalized) > 100:
            raise ValueError("collection name must be at most 100 characters")
        return normalized


class CollectionUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: str | None = None
    previews_enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("collection name is required")
        if len(normalized) > 100:
            raise ValueError("collection name must be at most 100 characters")
        return normalized

    @model_validator(mode="after")
    def validate_update_fields(self) -> CollectionUpdate:
        if (
            self.name is None
            and "parent_id" not in self.model_fields_set
            and self.previews_enabled is None
        ):
            raise ValueError("at least one collection field is required")
        return self


class CollectionPreview(APIModel):
    generation_id: str
    artifact_id: str
    thumbnail_url: str


class Collection(APIModel):
    id: str
    parent_id: str | None
    name: str
    created_at: datetime
    updated_at: datetime
    generation_count: int
    previews_enabled: bool = True
    is_favorite: bool = False
    previews: list[CollectionPreview] = Field(default_factory=list)


class GenerationMove(APIModel):
    collection_id: str | None = None


class GallerySelectionScope(APIModel):
    collection_id: str | None = None
    favorites_only: bool = False


class GallerySelectionGeneration(APIModel):
    id: str
    collection_id: str | None
    status: str
    image_count: int
    is_favorite: bool


class GalleryViewItems(APIModel):
    generations: list[GallerySelectionGeneration]
    collection_ids: list[str]


class GallerySelection(APIModel):
    # Whole-view selections carry an explicit snapshot of IDs. Removing an ID
    # excludes it; later arrivals can never be picked up by a bulk operation.
    scope: GallerySelectionScope | None = None
    generation_ids: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection(self) -> GallerySelection:
        self.generation_ids = list(dict.fromkeys(self.generation_ids))
        self.collection_ids = list(dict.fromkeys(self.collection_ids))
        if not self.generation_ids and not self.collection_ids:
            raise ValueError("Select at least one image card or collection.")
        if self.scope is None and len(self.generation_ids) + len(self.collection_ids) > 500:
            raise ValueError("Select at most 500 items at a time.")
        return self


class GalleryTransfer(GallerySelection):
    operation: Literal["move", "copy"]
    collection_id: str | None = None


class GalleryTransferResult(APIModel):
    operation: Literal["move", "copy"]
    generation_ids: list[str]
    collection_ids: list[str]


class GalleryDeleteItem(APIModel):
    kind: Literal["generation", "collection"]
    id: str
    status: Literal["deleted", "pending", "failed"]
    message: str | None = None


class GalleryDeleteResult(APIModel):
    items: list[GalleryDeleteItem]


class GenerationDetail(GenerationSummary):
    workflow: WorkflowIdentity
    generation_source: dict[str, Any] = Field(default_factory=dict)
    requested_controls: dict[str, Any]
    effective_controls: dict[str, Any]
    requested_parameters: dict[str, Any] = Field(default_factory=dict)
    effective_parameters: dict[str, Any] = Field(default_factory=dict)
    input_definitions: list[dict[str, Any]] = Field(default_factory=list)
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
    source_available: bool = False
    reason: str | None = None
    profile_id: str | None = None
    identity: WorkflowIdentity | None = None
    controls: dict[str, Any] = Field(default_factory=dict)
    source_key: str | None = None
    revision: SourceRevision | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_definitions: list[dict[str, Any]] = Field(default_factory=list)
    prompt_assistant: dict[str, Any] | None = None
    comfyui_instance_id: str | None = None
    comfyui_instance_label: str | None = None
    comfyui_instance_configured: bool = False
    comfyui_instance_available: bool = False
    comfyui_instance_warning: str | None = None


class FavoriteSummary(APIModel):
    id: str
    created_at: datetime
    item_type: Literal["generation", "collection"] = "generation"
    final_prompt: str = ""
    generation: GenerationSummary | None = None
    collection: Collection | None = None

    @model_validator(mode="after")
    def validate_item(self) -> FavoriteSummary:
        if self.item_type == "generation":
            valid = self.generation is not None and self.collection is None
        else:
            valid = self.collection is not None and self.generation is None
        if not valid:
            raise ValueError("A favorite must contain exactly its declared item type.")
        return self


class PromptComposeRequest(APIModel):
    mode: Literal["refine", "create"]
    prompt: str = ""
    creative_direction: str
    think: bool = True
    instructions: str | None = Field(default=None, min_length=1, max_length=8000)

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Enter instructions or reset to the default.")
        return value.strip() if value is not None else None


class PromptComposeResponse(APIModel):
    composition_id: str
    prompt: str
    model: str
    template_version: str


class PromptAssistantStatus(APIModel):
    available: bool
    message: str | None = None
    default_instructions: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_PROMPT_INSTRUCTIONS)
    )


class SpeechToTextStatus(APIModel):
    available: bool
    message: str | None = None


class TranscriptionResponse(APIModel):
    text: str


class UploadResponse(APIModel):
    id: str
    kind: str
    mime_type: str
    byte_size: int
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


class PromptGenerationCreate(APIModel):
    comfyui_instance_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=COMFYUI_INSTANCE_ID_PATTERN,
        json_schema_extra={"deprecated": True},
    )
    source_key: str
    revision: SourceRevision
    parameters: dict[str, Any] = Field(default_factory=dict)


class GenerationPreparationItem(APIModel):
    generation: GenerationCreate
    prompt_generation: PromptGenerationCreate
    assistant: PromptComposeRequest | None = None

    @model_validator(mode="after")
    def refinement_only(self) -> GenerationPreparationItem:
        if self.assistant and self.assistant.mode != "refine":
            raise ValueError("Prompt generation supports Creative Direction in Refine mode only")
        if self.generation.prompt_assistant_run_id:
            raise ValueError("Preparation composes its own prompt")
        return self


class GenerationPreparationCreate(APIModel):
    items: list[GenerationPreparationItem] = Field(min_length=1, max_length=256)


class AutoGenerationSnapshot(APIModel):
    prompt_generation: PromptGenerationCreate | None = None
    generation: GenerationCreate
    variants: list[dict[str, str]] = Field(
        default_factory=lambda: [dict[str, str]()], min_length=1, max_length=256
    )
    quantity: int = Field(default=1, ge=1, le=16)
    assistant: PromptComposeRequest | None = None
    max_generations: int | None = Field(default=200, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_size(self) -> AutoGenerationSnapshot:
        if self.prompt_generation and self.assistant and self.assistant.mode != "refine":
            raise ValueError("Prompt generation supports Refine mode only")
        if len(self.variants) * self.quantity > 256:
            raise ValueError("An automatic batch cannot exceed 256 generations")
        if self.generation.prompt_assistant_run_id:
            raise ValueError("Automation composes its own prompts")
        return self


class AutoGenerationUpdate(APIModel):
    expected_revision: int = Field(ge=0)
    enabled: bool
    snapshot: AutoGenerationSnapshot | None = None


class AutoGenerationApply(APIModel):
    expected_revision: int = Field(ge=0)
    snapshot: AutoGenerationSnapshot


class AutoGenerationRetry(APIModel):
    expected_revision: int = Field(ge=0)


class AutoGenerationProgress(APIModel):
    revision: int
    cycle_id: str | None = None
    cycle_created_at: datetime | None = None
    active_stages: list[Literal["prompt_generation", "creative_direction", "image"]] = Field(
        default_factory=list
    )
    raw_prompt: str | None = None
    refined_prompt: str | None = None


class AutoGenerationResponse(APIModel):
    progress: AutoGenerationProgress | None = None
    prompt_ready: bool = False
    workflow_name: str | None = None
    enabled: bool = False
    revision: int = 0
    status: str = "off"
    snapshot: AutoGenerationSnapshot | None = None
    latest_prompt: str | None = None
    accepted_count: int = 0
    remaining: int | None = None
    error_code: str | None = None
    message: str | None = None
    next_retry_at: datetime | None = None
    updated_at: datetime | None = None


class AutoGenerationLimit(APIModel):
    expected_revision: int = Field(ge=0)
    max_generations: int | None = Field(ge=1, le=1_000_000)
