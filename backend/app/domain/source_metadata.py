from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

GENERATION_SOURCE_SCHEMA = "comfyui-image-frontend.generation-source/v1"
TECHNICAL_INVENTORY_SCHEMA = "comfyui-image-frontend.technical-inventory/v1"

GENERATION_SOURCE_INVALID_WARNING = (
    "Published generation-source metadata is invalid or uses an unrecognized schema; "
    "generation remains available without that metadata."
)
TECHNICAL_INVENTORY_INVALID_WARNING = (
    "Published technical-inventory metadata is invalid or uses an unrecognized schema; "
    "generation remains available without that metadata."
)
TECHNICAL_INVENTORY_COUNT_WARNING = (
    "Published technical-inventory node counts are inconsistent; generation remains available."
)


class OpenMetadataModel(BaseModel):
    """Strictly type documented fields while retaining additive publisher fields."""

    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)


class BaseModelMetadata(OpenMetadataModel):
    family: str
    family_label: str
    architecture: str
    architecture_label: str
    primary_artifacts: list[str]


class TechnologyMetadata(OpenMetadataModel):
    id: str
    label: str
    category: str


class GenerationSourceMetadata(OpenMetadataModel):
    schema_version: Literal["comfyui-image-frontend.generation-source/v1"]
    inference_method: str
    generation_type: str
    prompt_guided: bool
    input_media: list[str]
    output_media: list[str]
    dimension_policy: str
    summary: str
    base_model: BaseModelMetadata
    technologies: list[TechnologyMetadata]
    tags: list[str]


class NodeCountsMetadata(OpenMetadataModel):
    editable_root: int = Field(ge=0)
    subgraph_definitions: int = Field(ge=0)
    editable_subgraph_nodes: int = Field(ge=0)
    compiled_api: int = Field(ge=0)
    output_reachable: int = Field(ge=0)
    compiled_orphans: int = Field(ge=0)


class PrimaryModelMetadata(OpenMetadataModel):
    kind: str
    artifact: str
    usage: str


class ArtifactMetadata(OpenMetadataModel):
    artifact: str
    usage: str


class FixedLoraMetadata(OpenMetadataModel):
    usage: Literal["fixed_active"]
    artifact: str


class FixedLoraWithStrengthMetadata(FixedLoraMetadata):
    strength: int | float


class PublicLoraOptionMetadata(OpenMetadataModel):
    value: str
    label: str


class PublicLoraOptionWithStrengthMetadata(PublicLoraOptionMetadata):
    default_strength: int | float


class PublicChoiceLoraMetadata(OpenMetadataModel):
    usage: Literal["public_choice"]
    parameter_id: str
    default: str
    options: list[PublicLoraOptionWithStrengthMetadata | PublicLoraOptionMetadata]


class SamplerMetadata(OpenMetadataModel):
    class_type: str
    settings: dict[str, Any]


class LoaderMetadata(OpenMetadataModel):
    class_type: str


class TechnicalInventoryMetadata(OpenMetadataModel):
    schema_version: Literal["comfyui-image-frontend.technical-inventory/v1"]
    node_counts: NodeCountsMetadata
    models: list[PrimaryModelMetadata | dict[str, Any]]
    loras: list[
        FixedLoraWithStrengthMetadata
        | FixedLoraMetadata
        | PublicChoiceLoraMetadata
        | dict[str, Any]
    ]
    text_encoders: list[ArtifactMetadata | dict[str, Any]]
    vaes: list[ArtifactMetadata | dict[str, Any]]
    upscalers: list[ArtifactMetadata | dict[str, Any]]
    detectors: list[ArtifactMetadata | dict[str, Any]]
    samplers: list[SamplerMetadata | dict[str, Any]]
    technologies: list[TechnologyMetadata]
    reachable_class_types: list[str]
    orphan_class_types: list[str]
    unclassified_loaders: list[LoaderMetadata | dict[str, Any]]
    warnings: list[str]


@dataclass(frozen=True)
class RecognizedSourceMetadata:
    generation_source: dict[str, Any] | None
    technical_inventory: dict[str, Any] | None
    diagnostics: tuple[str, ...]
    warnings: tuple[str, ...]


def recognize_source_metadata(
    manifest: Mapping[str, Any], *, compiled_api_nodes: int | None = None
) -> RecognizedSourceMetadata:
    """Recognize additive metadata without making it an execution/discovery gate."""

    diagnostics: list[str] = []
    warnings: list[str] = []
    generation_source = _recognize_section(
        manifest.get("generation_source"),
        model=GenerationSourceMetadata,
        schema=GENERATION_SOURCE_SCHEMA,
        invalid_code="generation_source_invalid",
        schema_code="generation_source_schema_unrecognized",
        warning=GENERATION_SOURCE_INVALID_WARNING,
        diagnostics=diagnostics,
        warnings=warnings,
    )
    technical_inventory = _recognize_section(
        manifest.get("technical_inventory"),
        model=TechnicalInventoryMetadata,
        schema=TECHNICAL_INVENTORY_SCHEMA,
        invalid_code="technical_inventory_invalid",
        schema_code="technical_inventory_schema_unrecognized",
        warning=TECHNICAL_INVENTORY_INVALID_WARNING,
        diagnostics=diagnostics,
        warnings=warnings,
    )
    if technical_inventory is not None:
        counts = technical_inventory["node_counts"]
        arithmetic_matches = (
            counts["output_reachable"] + counts["compiled_orphans"] == counts["compiled_api"]
        )
        compiled_count_matches = (
            compiled_api_nodes is None or counts["compiled_api"] == compiled_api_nodes
        )
        if not arithmetic_matches:
            diagnostics.append("technical_inventory_node_count_arithmetic_mismatch")
        if not compiled_count_matches:
            diagnostics.append("technical_inventory_compiled_api_count_mismatch")
        if not arithmetic_matches or not compiled_count_matches:
            warnings.append(TECHNICAL_INVENTORY_COUNT_WARNING)

    return RecognizedSourceMetadata(
        generation_source=generation_source,
        technical_inventory=technical_inventory,
        diagnostics=tuple(diagnostics),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _recognize_section(
    raw: Any,
    *,
    model: type[OpenMetadataModel],
    schema: str,
    invalid_code: str,
    schema_code: str,
    warning: str,
    diagnostics: list[str],
    warnings: list[str],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        diagnostics.append(invalid_code)
        warnings.append(warning)
        return None
    if raw.get("schema_version") != schema:
        diagnostics.append(schema_code)
        warnings.append(warning)
        return None
    section = copy.deepcopy(dict(raw))
    try:
        model.model_validate(section)
    except ValidationError:
        diagnostics.append(invalid_code)
        warnings.append(warning)
        return None
    if model is TechnicalInventoryMetadata:
        if not _known_inventory_entries_are_typed(section):
            diagnostics.append(invalid_code)
            warnings.append(warning)
            return None
        if not _public_choice_loras_are_safe(section):
            diagnostics.append(invalid_code)
            warnings.append(warning)
            return None
    return section


def _known_inventory_entries_are_typed(section: Mapping[str, Any]) -> bool:
    model_lists: tuple[tuple[str, type[OpenMetadataModel], set[str]], ...] = (
        ("models", PrimaryModelMetadata, {"kind", "artifact", "usage"}),
        ("text_encoders", ArtifactMetadata, {"artifact", "usage"}),
        ("vaes", ArtifactMetadata, {"artifact", "usage"}),
        ("upscalers", ArtifactMetadata, {"artifact", "usage"}),
        ("detectors", ArtifactMetadata, {"artifact", "usage"}),
        ("samplers", SamplerMetadata, {"class_type", "settings"}),
        ("unclassified_loaders", LoaderMetadata, {"class_type"}),
    )
    for field, entry_model, documented_keys in model_lists:
        entries = section.get(field)
        if not isinstance(entries, list):
            return False
        for entry in entries:
            if not isinstance(entry, Mapping):
                return False
            # Entirely new entry shapes remain lossless; entries using documented fields are typed.
            if documented_keys.issubset(entry) and not _matches_model(entry_model, entry):
                return False

    loras = section.get("loras")
    if not isinstance(loras, list):
        return False
    for lora in loras:
        if not isinstance(lora, Mapping):
            return False
        usage = lora.get("usage")
        if usage == "fixed_active":
            lora_model = FixedLoraWithStrengthMetadata if "strength" in lora else FixedLoraMetadata
            if not _matches_model(lora_model, lora):
                return False
        elif usage == "public_choice":
            if not _matches_model(PublicChoiceLoraMetadata, lora):
                return False
            options = lora.get("options")
            if not isinstance(options, list):
                return False
            for option in options:
                if not isinstance(option, Mapping):
                    return False
                option_model = (
                    PublicLoraOptionWithStrengthMetadata
                    if "default_strength" in option
                    else PublicLoraOptionMetadata
                )
                if not _matches_model(option_model, option):
                    return False
    return True


def _matches_model(model: type[OpenMetadataModel], value: Mapping[str, Any]) -> bool:
    try:
        model.model_validate(dict(value))
    except ValidationError:
        return False
    return True


def _public_choice_loras_are_safe(section: Mapping[str, Any]) -> bool:
    """Known public-choice entries must not smuggle private artifact bindings."""

    private_keys = {"artifact", "filename", "path", "bindings", "options_json"}
    raw_loras = section.get("loras")
    if not isinstance(raw_loras, list):
        return False
    for raw_lora in raw_loras:
        if not isinstance(raw_lora, Mapping) or raw_lora.get("usage") != "public_choice":
            continue
        if private_keys.intersection(raw_lora):
            return False
        options = raw_lora.get("options")
        if not isinstance(options, list):
            return False
        if any(
            isinstance(option, Mapping) and private_keys.intersection(option) for option in options
        ):
            return False
    return True
