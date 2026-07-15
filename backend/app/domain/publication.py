from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, TypeGuard

import orjson

from ..errors import ContractError

PUBLICATION_SCHEMA = "comfyui-image-frontend.publication/v1"
INTERFACE_SCHEMA = "comfyui-image-frontend.interface/v1"
EDITABLE_WORKFLOW_DRIFT_WARNING = (
    "Editable workflow bytes do not match the publication's recorded SHA-256; "
    "generation remains pinned to the verified frozen API graph."
)
SUPPORTED_INPUT_TYPES = {"string", "integer", "number", "boolean", "seed", "choice", "image"}
SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
OUTPUT_ROLES = {"final", "preview", "comparison", "auxiliary"}
OUTPUT_KINDS = {"image"}
PUBLIC_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SEMANTIC_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
CANONICAL_INTEGER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
ENCODED_SEPARATOR_RE = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
MAX_INPUTS = 256
MAX_CHOICES = 100
MAX_OUTPUTS = 256
MAX_WARNINGS = 256
MAX_API_NODES = 10_000
MAX_PATH_BYTES = 1024
MAX_TEXT_DEFAULT = 100_000
MAX_JSON_DEPTH = 128
MAX_JSON_VALUES = 1_000_000
MIN_JSON_INTEGER = -(2**63)
MAX_JSON_INTEGER = 2**64 - 1
MAX_BROWSER_SAFE_INTEGER = 2**53 - 1

EXPECTED_PARAMETER_CLASSES: dict[str, set[str]] = {
    "string": {"CIFTextParameter", "CIFImageFrontendInterface"},
    "integer": {"CIFIntegerParameter", "CIFImageFrontendInterface"},
    "number": {"CIFDecimalParameter", "CIFImageFrontendInterface"},
    "boolean": {"CIFBooleanParameter", "CIFImageFrontendInterface"},
    "seed": {"CIFSeedParameter", "CIFImageFrontendInterface"},
    "choice": {"CIFChoiceParameter"},
    "image": {"CIFImageParameter"},
}
EXPECTED_RUNTIME_INPUT_TYPES = {
    "string": "STRING",
    "integer": "INT",
    "number": "FLOAT",
    "boolean": "BOOLEAN",
    "seed": "INT",
    "choice": "STRING",
}
TYPED_PARAMETER_CLASSES = {
    "CIFTextParameter",
    "CIFIntegerParameter",
    "CIFDecimalParameter",
    "CIFBooleanParameter",
    "CIFSeedParameter",
    "CIFChoiceParameter",
}

CHOICE_PUBLIC_FIELDS = frozenset({"value", "label", "default_strength"})


@dataclass(frozen=True)
class ValidatedPublication:
    instance_id: str
    source_key: str
    source_id: str
    display_name: str
    publication_id: str
    published_at: str
    publication_schema: str
    contract_schema: str
    workflow_path: str
    api_path: str
    manifest_path: str
    workflow_sha256: str
    observed_workflow_sha256: str
    editable_workflow_drifted: bool
    api_sha256: str
    manifest_sha256: str
    identity_key: str
    workflow_document: dict[str, Any]
    api_document: dict[str, Any]
    manifest: dict[str, Any]
    private_contract: dict[str, Any]
    public_interface: dict[str, Any]
    dependencies: tuple[str, ...]
    missing_dependencies: tuple[str, ...]
    warnings: tuple[str, ...]
    runtime: dict[str, Any]
    node_count: int

    @property
    def readiness(self) -> str:
        if self.missing_dependencies:
            return "dependency_missing"
        return "ready_with_warnings" if self.warnings else "ready"


@dataclass(frozen=True)
class PublicationManifest:
    document: dict[str, Any]
    manifest_path: str
    workflow_path: str
    api_path: str
    source_id: str
    publication_id: str
    published_at: str
    publication_schema: str
    contract_schema: str
    workflow_sha256: str
    api_sha256: str
    node_count: int


def canonical_json_bytes(value: Any) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_key_for(instance_id: str, source_id: str) -> str:
    """Return an opaque, stable key without exposing the server userdata path."""

    return hashlib.sha256(f"{instance_id}\0{source_id}".encode()).hexdigest()


def source_id_for_manifest_path(manifest_path: str) -> str:
    safe = validate_userdata_path(manifest_path, context="manifest listing path")
    if not safe.endswith(".interface.json"):
        raise ContractError("manifest_invalid", "Candidate is not an interface manifest.")
    return safe[: -len(".interface.json")] + ".json"


def display_name_for_source(source_id: str) -> str:
    filename = PurePosixPath(source_id).name
    return filename[: -len(".json")] if filename.endswith(".json") else filename


def validate_userdata_path(value: Any, *, context: str) -> str:
    if not isinstance(value, str):
        raise ContractError("manifest_invalid", f"{context} must be a string.")
    try:
        encoded_value = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError("manifest_invalid", f"{context} contains invalid Unicode.") from exc
    if not value or len(encoded_value) > MAX_PATH_BYTES:
        raise ContractError("manifest_invalid", f"{context} has an invalid length.")
    if (
        value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or "//" in value
        or ENCODED_SEPARATOR_RE.search(value)
    ):
        raise ContractError("manifest_invalid", f"{context} is not a safe userdata path.")
    path = PurePosixPath(value)
    if not path.parts or path.parts[0] != "workflows":
        raise ContractError("manifest_invalid", f"{context} must be rooted beneath workflows/.")
    if any(part in {"", ".", ".."} for part in path.parts) or str(path) != value:
        raise ContractError("manifest_invalid", f"{context} is not a normalized userdata path.")
    return value


def parse_json_object(raw: bytes, *, context: str, maximum_bytes: int) -> dict[str, Any]:
    if len(raw) > maximum_bytes:
        raise ContractError("artifact_too_large", f"{context} exceeds the configured size limit.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("manifest_invalid", f"{context} is not UTF-8 JSON.") from exc

    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number: {value}")
            ),
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ContractError("manifest_invalid", f"{context} is not valid strict JSON.") from exc
    if not isinstance(parsed, dict):
        raise ContractError("manifest_invalid", f"{context} must be a JSON object.")
    _validate_json_tree(parsed, context=context)
    return parsed


def _validate_json_tree(value: Any, *, context: str) -> None:
    """Reject values that cannot safely cross hashing, persistence, and HTTP JSON boundaries."""

    pending: list[tuple[Any, int]] = [(value, 0)]
    value_count = 0
    while pending:
        item, depth = pending.pop()
        value_count += 1
        if value_count > MAX_JSON_VALUES:
            raise ContractError("manifest_invalid", f"{context} contains too many JSON values.")
        if depth > MAX_JSON_DEPTH:
            raise ContractError(
                "manifest_invalid", f"{context} exceeds the supported JSON nesting depth."
            )
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, int):
            if not MIN_JSON_INTEGER <= item <= MAX_JSON_INTEGER:
                raise ContractError(
                    "manifest_invalid", f"{context} contains an unsupported integer."
                )
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ContractError("manifest_invalid", f"{context} contains a non-finite number.")
            continue
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ContractError(
                    "manifest_invalid", f"{context} contains invalid Unicode."
                ) from exc
            continue
        if isinstance(item, dict):
            for key, nested in item.items():
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ContractError(
                        "manifest_invalid", f"{context} contains an invalid object key."
                    ) from exc
                pending.append((nested, depth + 1))
            continue
        if isinstance(item, list):
            pending.extend((nested, depth + 1) for nested in item)
            continue
        raise ContractError("manifest_invalid", f"{context} contains an unsupported JSON value.")


def validate_publication_manifest(
    *, manifest_path: str, manifest_bytes: bytes, manifest_max_bytes: int
) -> PublicationManifest:
    """Validate the commit marker before retrieving either referenced artifact."""

    candidate_path = validate_userdata_path(manifest_path, context="manifest path")
    manifest = parse_json_object(
        manifest_bytes, context="Interface manifest", maximum_bytes=manifest_max_bytes
    )
    publication_schema = _required_string(manifest, "schema_version", "manifest")
    if publication_schema != PUBLICATION_SCHEMA:
        raise ContractError(
            "unsupported_publication_schema",
            f"Unsupported publication schema {publication_schema!r}.",
        )
    contract_schema = _required_string(manifest, "contract_schema", "manifest")
    if contract_schema != INTERFACE_SCHEMA:
        raise ContractError(
            "unsupported_contract_schema", f"Unsupported interface schema {contract_schema!r}."
        )

    publication_id = _canonical_uuid(manifest.get("publication_id"), "publication_id")
    published_at = _timestamp(manifest.get("published_at"), "published_at")
    source_id = validate_userdata_path(
        _required_string(manifest, "source_id", "manifest"), context="source_id"
    )
    workflow = _required_mapping(manifest, "workflow", "manifest")
    api = _required_mapping(manifest, "api", "manifest")
    workflow_path = validate_userdata_path(
        _required_string(workflow, "path", "workflow"), context="workflow.path"
    )
    api_path = validate_userdata_path(_required_string(api, "path", "api"), context="api.path")
    if source_id != workflow_path:
        raise ContractError("manifest_invalid", "source_id must exactly match workflow.path.")

    expected_stem = (
        candidate_path[: -len(".interface.json")]
        if candidate_path.endswith(".interface.json")
        else ""
    )
    if not expected_stem:
        raise ContractError("manifest_invalid", "Manifest filename must end with .interface.json.")
    if workflow_path != expected_stem + ".json" or api_path != expected_stem + ".api.json":
        raise ContractError(
            "manifest_invalid",
            "Manifest, editable workflow, and API graph must share one adjacent stem.",
        )
    manifest_record = manifest.get("manifest")
    if isinstance(manifest_record, Mapping) and "path" in manifest_record:
        recorded_manifest_path = validate_userdata_path(
            manifest_record["path"], context="manifest.path"
        )
        if recorded_manifest_path != candidate_path:
            raise ContractError(
                "manifest_invalid", "manifest.path does not match the listed candidate."
            )

    return PublicationManifest(
        document=manifest,
        manifest_path=candidate_path,
        workflow_path=workflow_path,
        api_path=api_path,
        source_id=source_id,
        publication_id=publication_id,
        published_at=published_at,
        publication_schema=publication_schema,
        contract_schema=contract_schema,
        workflow_sha256=_sha256(workflow.get("sha256"), "workflow.sha256"),
        api_sha256=_sha256(api.get("sha256"), "api.sha256"),
        node_count=_bounded_integer(api.get("node_count"), "api.node_count", 1, MAX_API_NODES),
    )


def validate_publication(
    *,
    instance_id: str,
    manifest_path: str,
    manifest_bytes: bytes,
    workflow_bytes: bytes,
    api_bytes: bytes,
    object_info: Mapping[str, Any],
    manifest_max_bytes: int,
    workflow_max_bytes: int,
    api_max_bytes: int,
) -> ValidatedPublication:
    envelope = validate_publication_manifest(
        manifest_path=manifest_path,
        manifest_bytes=manifest_bytes,
        manifest_max_bytes=manifest_max_bytes,
    )
    candidate_path = envelope.manifest_path
    manifest = envelope.document
    workflow_document = parse_json_object(
        workflow_bytes, context="Editable workflow", maximum_bytes=workflow_max_bytes
    )
    api_document = parse_json_object(
        api_bytes, context="Frozen API graph", maximum_bytes=api_max_bytes
    )

    publication_schema = envelope.publication_schema
    contract_schema = envelope.contract_schema
    publication_id = envelope.publication_id
    published_at = envelope.published_at
    source_id = envelope.source_id
    workflow_path = envelope.workflow_path
    api_path = envelope.api_path
    workflow_sha256 = envelope.workflow_sha256
    api_sha256 = envelope.api_sha256
    observed_workflow_sha256 = sha256_bytes(workflow_bytes)
    editable_workflow_drifted = observed_workflow_sha256 != workflow_sha256
    if sha256_bytes(api_bytes) != api_sha256:
        raise ContractError(
            "api_hash_mismatch", "Frozen API graph bytes do not match the manifest."
        )

    node_count = envelope.node_count
    if len(api_document) != node_count:
        raise ContractError(
            "api_node_count_mismatch", "Frozen API graph node count does not match the manifest."
        )
    _validate_api_graph(api_document)

    raw_interface = _required_mapping(manifest, "interface", "manifest")
    private_contract, public_interface = _validate_interface(
        raw_interface, api_document, object_info
    )
    dependencies = _validate_dependencies(manifest.get("dependencies"), api_document)
    missing_dependencies = tuple(
        sorted(value for value in dependencies if value not in object_info)
    )
    warnings = _validate_warnings(manifest.get("warnings", []))
    if editable_workflow_drifted and EDITABLE_WORKFLOW_DRIFT_WARNING not in warnings:
        warnings = (*warnings, EDITABLE_WORKFLOW_DRIFT_WARNING)
    runtime = _validate_runtime(manifest.get("runtime", {}))

    source_key = source_key_for(instance_id, source_id)
    manifest_sha256 = sha256_bytes(manifest_bytes)
    identity_key = hashlib.sha256(
        "\0".join(
            (
                instance_id,
                source_id,
                publication_id,
                workflow_sha256,
                api_sha256,
                manifest_sha256,
            )
        ).encode()
    ).hexdigest()
    private_contract["runtime"] = copy.deepcopy(runtime)
    private_contract["warnings"] = list(warnings)
    private_contract["publication_id"] = publication_id
    private_contract["source_key"] = source_key

    return ValidatedPublication(
        instance_id=instance_id,
        source_key=source_key,
        source_id=source_id,
        display_name=display_name_for_source(source_id),
        publication_id=publication_id,
        published_at=published_at,
        publication_schema=publication_schema,
        contract_schema=contract_schema,
        workflow_path=workflow_path,
        api_path=api_path,
        manifest_path=candidate_path,
        workflow_sha256=workflow_sha256,
        observed_workflow_sha256=observed_workflow_sha256,
        editable_workflow_drifted=editable_workflow_drifted,
        api_sha256=api_sha256,
        manifest_sha256=manifest_sha256,
        identity_key=identity_key,
        workflow_document=workflow_document,
        api_document=api_document,
        manifest=copy.deepcopy(manifest),
        private_contract=private_contract,
        public_interface=public_interface,
        dependencies=dependencies,
        missing_dependencies=missing_dependencies,
        warnings=warnings,
        runtime=runtime,
        node_count=node_count,
    )


def _validate_api_graph(graph: Mapping[str, Any]) -> None:
    for raw_node_id, raw_node in graph.items():
        node_id = str(raw_node_id)
        if not node_id or len(node_id) > 100:
            raise ContractError("manifest_invalid", "Frozen API graph contains an invalid node ID.")
        if not isinstance(raw_node, Mapping):
            raise ContractError("manifest_invalid", f"API node {node_id} must be an object.")
        if not isinstance(raw_node.get("class_type"), str) or not raw_node["class_type"]:
            raise ContractError("manifest_invalid", f"API node {node_id} has no class_type.")
        if not isinstance(raw_node.get("inputs"), Mapping):
            raise ContractError("manifest_invalid", f"API node {node_id} has no inputs object.")


def _validate_interface(
    interface: Mapping[str, Any],
    api_document: Mapping[str, Any],
    object_info: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_inputs = interface.get("inputs")
    if not isinstance(raw_inputs, list) or not 1 <= len(raw_inputs) <= MAX_INPUTS:
        raise ContractError("manifest_invalid", "interface.inputs must contain 1 to 256 entries.")
    private_inputs: list[dict[str, Any]] = []
    public_inputs: list[dict[str, Any]] = []
    ids: set[str] = set()
    instance_uuids: set[str] = set()
    positive_prompts = 0
    for index, raw_input in enumerate(raw_inputs):
        private_input, public_input = _validate_input(raw_input, index, api_document, object_info)
        input_id = private_input["id"]
        if input_id in ids:
            raise ContractError("manifest_invalid", f"Duplicate public input ID {input_id!r}.")
        ids.add(input_id)
        instance_uuid = private_input["instance_uuid"]
        if instance_uuid in instance_uuids:
            raise ContractError("manifest_invalid", "Parameter instance UUIDs must be unique.")
        instance_uuids.add(instance_uuid)
        if private_input["semantic_role"] == "positive_prompt":
            positive_prompts += 1
        private_inputs.append(private_input)
        public_inputs.append(public_input)
    if positive_prompts != 1:
        raise ContractError(
            "manifest_invalid",
            f"Publication v1 requires exactly one positive_prompt input; found {positive_prompts}.",
        )

    raw_outputs = interface.get("outputs")
    if not isinstance(raw_outputs, list) or not 1 <= len(raw_outputs) <= MAX_OUTPUTS:
        raise ContractError(
            "manifest_invalid", "interface.outputs must contain 1 to 256 image publishers."
        )
    private_outputs: list[dict[str, Any]] = []
    public_outputs: list[dict[str, Any]] = []
    output_ids: set[str] = set()
    publisher_node_ids: set[str] = set()
    final_images = 0
    for index, raw_output in enumerate(raw_outputs):
        private_output, public_output = _validate_output(raw_output, index, api_document)
        output_id = private_output["id"]
        if output_id in output_ids:
            raise ContractError("manifest_invalid", f"Duplicate public output ID {output_id!r}.")
        output_ids.add(output_id)
        output_uuid = private_output.get("instance_uuid")
        if output_uuid:
            if output_uuid in instance_uuids:
                raise ContractError(
                    "manifest_invalid", "Declaration instance UUIDs must be unique."
                )
            instance_uuids.add(output_uuid)
        publisher_node_id = private_output["node_id"]
        if publisher_node_id in publisher_node_ids:
            raise ContractError(
                "manifest_invalid", "Each public output must bind a unique publisher node."
            )
        publisher_node_ids.add(publisher_node_id)
        if private_output["role"] == "final":
            final_images += 1
        private_outputs.append(private_output)
        public_outputs.append(public_output)
    if final_images != 1:
        raise ContractError(
            "manifest_invalid",
            f"Publication v1 requires exactly one final image output; found {final_images}.",
        )

    policy = interface.get("unmapped_outputs_policy")
    if policy != "collect":
        raise ContractError(
            "manifest_invalid", "Publication v1 supports only unmapped_outputs_policy='collect'."
        )
    native_outputs, inventoried_node_ids = _validate_native_outputs(
        interface.get("native_outputs"), api_document
    )
    if inventoried_node_ids and not publisher_node_ids <= inventoried_node_ids:
        raise ContractError(
            "manifest_invalid",
            "interface.native_outputs omits one or more declared publisher nodes.",
        )
    private_contract = {
        "schema": INTERFACE_SCHEMA,
        "inputs": private_inputs,
        "outputs": private_outputs,
        "unmapped_outputs_policy": policy,
        # Native inventory is private diagnostic metadata. Runtime result collection remains
        # exhaustive over actual ComfyUI history and must never use this as an allowlist.
        "native_outputs": native_outputs,
    }
    public_interface = {
        "schema": INTERFACE_SCHEMA,
        "inputs": public_inputs,
        "outputs": public_outputs,
        "unmapped_outputs_policy": policy,
    }
    return private_contract, public_interface


def _validate_input(
    raw_input: Any,
    index: int,
    api_document: Mapping[str, Any],
    object_info: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    context = f"interface.inputs[{index}]"
    if not isinstance(raw_input, Mapping):
        raise ContractError("manifest_invalid", f"{context} must be an object.")
    input_id = _public_id(raw_input.get("id"), f"{context}.id")
    input_type = _required_string(raw_input, "type", context)
    if input_type not in SUPPORTED_INPUT_TYPES:
        raise ContractError("manifest_invalid", f"{context}.type is unsupported.")
    instance_uuid = _canonical_uuid(raw_input.get("instance_uuid"), f"{context}.instance_uuid")
    label = _bounded_string(raw_input.get("label"), f"{context}.label", 1, 200)
    description = _bounded_string(
        raw_input.get("description", ""), f"{context}.description", 0, 2_000
    )
    semantic_role = _semantic_role(raw_input.get("semantic_role"), f"{context}.semantic_role")
    required = _boolean(raw_input.get("required"), f"{context}.required")
    advanced = _boolean(raw_input.get("advanced"), f"{context}.advanced")
    group = _bounded_string(raw_input.get("group"), f"{context}.group", 1, 100)
    order = _bounded_integer(raw_input.get("order"), f"{context}.order", -1_000_000, 1_000_000)
    bindings = _validate_bindings(
        raw_input.get("bindings"), input_type, context, api_document, object_info
    )

    private_input: dict[str, Any] = {
        "id": input_id,
        "type": input_type,
        "instance_uuid": instance_uuid,
        "label": label,
        "description": description,
        "semantic_role": semantic_role,
        "required": required,
        "advanced": advanced,
        "group": group,
        "order": order,
        "bindings": bindings,
    }
    public_input = {
        key: copy.deepcopy(private_input[key])
        for key in (
            "id",
            "type",
            "label",
            "description",
            "semantic_role",
            "required",
            "advanced",
            "group",
            "order",
        )
    }

    if input_type == "string":
        default = raw_input.get("default")
        if not isinstance(default, str) or len(default) > MAX_TEXT_DEFAULT:
            raise ContractError("manifest_invalid", f"{context}.default must be a bounded string.")
        private_input["default"] = default
        public_input["default"] = default
    elif input_type == "boolean":
        default = _boolean(raw_input.get("default"), f"{context}.default")
        private_input["default"] = default
        public_input["default"] = default
    elif input_type == "choice":
        default, choices = _choice_contract(raw_input, context)
        private_input.update(default=default, choices=copy.deepcopy(choices))
        public_input.update(default=default, choices=choices)
    elif input_type == "image":
        media = _image_contract(raw_input, context, bindings, api_document)
        private_input["media"] = copy.deepcopy(media)
        public_input["media"] = media
    else:
        minimum, maximum, step = _numeric_contract(raw_input, input_type, context)
        private_input.update(minimum=minimum, maximum=maximum, step=step)
        public_input.update(minimum=minimum, maximum=maximum, step=step)
        if input_type == "seed":
            default_mode = _required_string(raw_input, "default_mode", context)
            if default_mode not in {"fixed", "random"}:
                raise ContractError("manifest_invalid", f"{context}.default_mode is invalid.")
            saved_default = raw_input.get("default", raw_input.get("value"))
            if saved_default is None:
                raise ContractError(
                    "manifest_invalid", f"{context} must contain a saved seed default."
                )
            default = _integer_value(saved_default, f"{context}.default")
            _validate_numeric_value(default, minimum, maximum, step, f"{context}.default")
            private_input.update(default=default, default_mode=default_mode)
            public_input["default_mode"] = default_mode
            public_input["default"] = None if default_mode == "random" else str(default)
        else:
            raw_default = raw_input.get("default")
            if input_type == "integer":
                numeric_default: int | float = _browser_safe_integer(
                    raw_default, f"{context}.default"
                )
            else:
                numeric_default = _number_value(raw_default, f"{context}.default")
            _validate_numeric_value(numeric_default, minimum, maximum, step, f"{context}.default")
            private_input["default"] = numeric_default
            public_input["default"] = numeric_default
    return private_input, public_input


def _image_contract(
    raw_input: Mapping[str, Any],
    context: str,
    bindings: Sequence[Mapping[str, str]],
    api_document: Mapping[str, Any],
) -> dict[str, Any]:
    if raw_input.get("semantic_role") != "reference_image":
        raise ContractError(
            "manifest_invalid", f"{context}.semantic_role must be 'reference_image'."
        )
    if raw_input.get("required") is not True:
        raise ContractError(
            "manifest_invalid", f"{context}.required must be true for image inputs."
        )
    if "default" in raw_input:
        raise ContractError(
            "manifest_invalid", f"{context}.default must be absent for image inputs."
        )
    raw_media = raw_input.get("media")
    if not isinstance(raw_media, Mapping):
        raise ContractError("manifest_invalid", f"{context}.media must be an object.")
    if raw_media.get("upload_route") != "/upload/image":
        raise ContractError(
            "manifest_invalid", f"{context}.media.upload_route must be '/upload/image'."
        )
    if raw_media.get("storage_type") != "input":
        raise ContractError("manifest_invalid", f"{context}.media.storage_type must be 'input'.")
    raw_mime_types = raw_media.get("accepted_mime_types")
    if (
        not isinstance(raw_mime_types, list)
        or not raw_mime_types
        or any(not isinstance(value, str) for value in raw_mime_types)
        or len(set(raw_mime_types)) != len(raw_mime_types)
        or not set(raw_mime_types) <= SUPPORTED_IMAGE_MIME_TYPES
    ):
        raise ContractError(
            "manifest_invalid",
            f"{context}.media.accepted_mime_types must be a nonempty unique subset "
            "of PNG, JPEG, and WebP.",
        )
    max_bytes = _bounded_integer(
        raw_media.get("max_bytes"), f"{context}.media.max_bytes", 1, MAX_BROWSER_SAFE_INTEGER
    )
    max_width = _bounded_integer(
        raw_media.get("max_width"), f"{context}.media.max_width", 1, MAX_BROWSER_SAFE_INTEGER
    )
    max_height = _bounded_integer(
        raw_media.get("max_height"), f"{context}.media.max_height", 1, MAX_BROWSER_SAFE_INTEGER
    )
    if raw_media.get("animated") is not False:
        raise ContractError("manifest_invalid", f"{context}.media.animated must be false.")
    returns_mask = _boolean(raw_media.get("returns_mask"), f"{context}.media.returns_mask")
    for binding in bindings:
        node = api_document.get(binding["node_id"])
        node_inputs = node.get("inputs") if isinstance(node, Mapping) else None
        if not isinstance(node_inputs, Mapping):
            raise ContractError("manifest_invalid", f"{context} image binding is unavailable.")
        for field, expected in (
            ("max_bytes", max_bytes),
            ("max_width", max_width),
            ("max_height", max_height),
        ):
            if node_inputs.get(field) != expected:
                raise ContractError(
                    "manifest_invalid",
                    f"{context}.media.{field} does not match its frozen CIF image parameter.",
                )
    return {
        "upload_route": "/upload/image",
        "storage_type": "input",
        "accepted_mime_types": list(raw_mime_types),
        "max_bytes": max_bytes,
        "max_width": max_width,
        "max_height": max_height,
        "animated": False,
        "returns_mask": returns_mask,
    }


def _choice_contract(
    raw_input: Mapping[str, Any], context: str
) -> tuple[str, list[dict[str, Any]]]:
    default = raw_input.get("default")
    if not isinstance(default, str):
        raise ContractError("manifest_invalid", f"{context}.default must be a string.")

    raw_choices = raw_input.get("choices")
    if not isinstance(raw_choices, list) or not 1 <= len(raw_choices) <= MAX_CHOICES:
        raise ContractError(
            "manifest_invalid",
            f"{context}.choices must contain 1 to {MAX_CHOICES} entries.",
        )

    choices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_choice in enumerate(raw_choices):
        choice_context = f"{context}.choices[{index}]"
        if not isinstance(raw_choice, Mapping):
            raise ContractError("manifest_invalid", f"{choice_context} must be an object.")
        unsupported_fields = set(raw_choice) - CHOICE_PUBLIC_FIELDS
        if unsupported_fields:
            field = min(str(value) for value in unsupported_fields)
            raise ContractError(
                "manifest_invalid",
                f"{choice_context} contains unsupported or private field {field!r}.",
            )

        value = _public_id(raw_choice.get("value"), f"{choice_context}.value")
        if value in seen:
            raise ContractError(
                "manifest_invalid", f"{context}.choices contains duplicate value {value!r}."
            )
        seen.add(value)

        label = _bounded_string(raw_choice.get("label"), f"{choice_context}.label", 1, 200)
        if not label.strip():
            raise ContractError("manifest_invalid", f"{choice_context}.label must not be blank.")
        choice: dict[str, Any] = {"value": value, "label": label}
        if "default_strength" in raw_choice:
            choice["default_strength"] = _number_value(
                raw_choice["default_strength"], f"{choice_context}.default_strength"
            )
        choices.append(choice)

    if default not in seen:
        raise ContractError(
            "manifest_invalid", f"{context}.default must match exactly one choice value."
        )
    return default, choices


def _numeric_contract(
    raw_input: Mapping[str, Any], input_type: str, context: str
) -> tuple[int | float, int | float, int | float]:
    raw_minimum = raw_input.get("minimum")
    raw_maximum = raw_input.get("maximum")
    raw_step = raw_input.get("step")
    minimum: int | float
    maximum: int | float
    step: int | float
    if input_type == "integer":
        minimum = _browser_safe_integer(raw_minimum, f"{context}.minimum")
        maximum = _browser_safe_integer(raw_maximum, f"{context}.maximum")
        step = _browser_safe_integer(raw_step, f"{context}.step")
    elif input_type == "seed":
        minimum = _integer_value(raw_minimum, f"{context}.minimum")
        maximum = _integer_value(raw_maximum, f"{context}.maximum")
        step = _integer_value(raw_step, f"{context}.step")
    else:
        minimum = _number_value(raw_minimum, f"{context}.minimum")
        maximum = _number_value(raw_maximum, f"{context}.maximum")
        step = _number_value(raw_step, f"{context}.step")
    if minimum > maximum or step <= 0:
        raise ContractError("manifest_invalid", f"{context} has invalid numeric bounds or step.")
    return minimum, maximum, step


def _validate_bindings(
    raw_bindings: Any,
    input_type: str,
    context: str,
    api_document: Mapping[str, Any],
    object_info: Mapping[str, Any],
) -> list[dict[str, str]]:
    if not isinstance(raw_bindings, list) or not 1 <= len(raw_bindings) <= 32:
        raise ContractError("manifest_invalid", f"{context}.bindings must contain 1 to 32 entries.")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_binding in enumerate(raw_bindings):
        binding_context = f"{context}.bindings[{index}]"
        if not isinstance(raw_binding, Mapping):
            raise ContractError("manifest_invalid", f"{binding_context} must be an object.")
        raw_node_id = raw_binding.get("node_id")
        if not isinstance(raw_node_id, (str, int)) or isinstance(raw_node_id, bool):
            raise ContractError("manifest_invalid", f"{binding_context}.node_id is invalid.")
        node_id = str(raw_node_id)
        input_name_value = raw_binding.get("input", raw_binding.get("input_name"))
        input_name = _bounded_string(input_name_value, f"{binding_context}.input", 1, 100)
        target = (node_id, input_name)
        if target in seen:
            raise ContractError(
                "manifest_invalid", f"{binding_context} duplicates a binding target."
            )
        seen.add(target)
        node = api_document.get(node_id)
        if node is None:
            node = next((value for key, value in api_document.items() if str(key) == node_id), None)
        if not isinstance(node, Mapping):
            raise ContractError(
                "manifest_invalid", f"{binding_context} targets a missing API node."
            )
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping) or input_name not in inputs:
            raise ContractError(
                "manifest_invalid", f"{binding_context} targets a missing API input."
            )
        class_type = node.get("class_type")
        if class_type not in EXPECTED_PARAMETER_CLASSES[input_type]:
            raise ContractError(
                "manifest_invalid",
                f"{binding_context} does not target the declared CIF parameter type.",
            )
        if input_type == "image" and input_name != "image":
            raise ContractError(
                "manifest_invalid",
                f"{binding_context} must target the CIF image parameter image input.",
            )
        if class_type in TYPED_PARAMETER_CLASSES and input_name != "value":
            raise ContractError(
                "manifest_invalid",
                f"{binding_context} must target the typed CIF parameter value input.",
            )
        runtime_node = object_info.get(str(class_type))
        if runtime_node is not None and input_type != "image":
            runtime_type = _runtime_input_type(runtime_node, input_name)
            if runtime_type != EXPECTED_RUNTIME_INPUT_TYPES[input_type]:
                raise ContractError(
                    "manifest_invalid",
                    f"{binding_context} does not match the declared runtime input type.",
                )
        declared_class = raw_binding.get("class_type")
        if declared_class is not None and declared_class != class_type:
            raise ContractError(
                "manifest_invalid", f"{binding_context}.class_type does not match the graph."
            )
        result.append({"node_id": node_id, "input": input_name, "class_type": str(class_type)})
    return result


def _runtime_input_type(runtime_node: Any, input_name: str) -> str | None:
    if not isinstance(runtime_node, Mapping):
        return None
    raw_inputs = runtime_node.get("input")
    if not isinstance(raw_inputs, Mapping):
        return None
    for section in ("required", "optional"):
        fields = raw_inputs.get(section)
        if not isinstance(fields, Mapping):
            continue
        spec = fields.get(input_name)
        if isinstance(spec, Sequence) and not isinstance(spec, (str, bytes, bytearray)) and spec:
            runtime_type = spec[0]
            if isinstance(runtime_type, str):
                return runtime_type.upper()
    return None


def _validate_output(
    raw_output: Any, index: int, api_document: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    context = f"interface.outputs[{index}]"
    if not isinstance(raw_output, Mapping):
        raise ContractError("manifest_invalid", f"{context} must be an object.")
    raw_id = raw_output.get("id", raw_output.get("output_id"))
    output_id = _public_id(raw_id, f"{context}.id")
    role = _required_string(raw_output, "role", context)
    kind = _required_string(raw_output, "type", context)
    if "type" in raw_output and "kind" in raw_output and raw_output["kind"] != kind:
        raise ContractError(
            "manifest_invalid", f"{context}.kind conflicts with the declared output type."
        )
    if role not in OUTPUT_ROLES or kind not in OUTPUT_KINDS:
        raise ContractError("manifest_invalid", f"{context} has an unsupported role or kind.")
    cardinality = _required_string(raw_output, "cardinality", context)
    if cardinality != "many":
        raise ContractError(
            "manifest_invalid", f"{context}.cardinality must be 'many' for publication v1."
        )
    label = _bounded_string(raw_output.get("label", output_id), f"{context}.label", 1, 200)
    description = _bounded_string(
        raw_output.get("description", ""), f"{context}.description", 0, 2_000
    )
    private_output: dict[str, Any] = {
        "id": output_id,
        "role": role,
        "kind": kind,
        "cardinality": cardinality,
        "label": label,
        "description": description,
        "instance_uuid": _canonical_uuid(
            raw_output.get("instance_uuid"), f"{context}.instance_uuid"
        ),
    }
    raw_node_id = raw_output.get("node_id")
    if not isinstance(raw_node_id, (str, int)) or isinstance(raw_node_id, bool):
        raise ContractError("manifest_invalid", f"{context}.node_id is required.")
    node_id = str(raw_node_id)
    node = api_document.get(node_id)
    if not isinstance(node, Mapping):
        raise ContractError("manifest_invalid", f"{context}.node_id targets a missing API node.")
    if node.get("class_type") != "CIFPublishImage":
        raise ContractError(
            "manifest_invalid", f"{context}.node_id targets the wrong publisher type."
        )
    node_inputs = node.get("inputs")
    if not isinstance(node_inputs, Mapping):
        raise ContractError("manifest_invalid", f"{context}.node_id has no publisher inputs.")
    image_connection = node_inputs.get("images")
    if not isinstance(image_connection, list) or len(image_connection) != 2:
        raise ContractError(
            "manifest_invalid", f"{context}.node_id is not connected to an image source."
        )
    raw_source_node_id, raw_source_output_index = image_connection
    if (
        isinstance(raw_source_node_id, bool)
        or not isinstance(raw_source_node_id, (str, int))
        or isinstance(raw_source_output_index, bool)
        or not isinstance(raw_source_output_index, int)
        or raw_source_output_index < 0
    ):
        raise ContractError(
            "manifest_invalid", f"{context}.node_id has an invalid image connection."
        )
    source_node_id = str(raw_source_node_id)
    if source_node_id == node_id or not isinstance(api_document.get(source_node_id), Mapping):
        raise ContractError(
            "manifest_invalid", f"{context}.node_id targets a missing image source."
        )
    for field, expected in (
        ("output_id", output_id),
        ("instance_uuid", private_output["instance_uuid"]),
        ("role", role),
        ("kind", kind),
        ("cardinality", cardinality),
        ("description", description),
    ):
        if field in node_inputs and node_inputs[field] != expected:
            raise ContractError(
                "manifest_invalid",
                f"{context}.{field} does not match its frozen publisher node.",
            )
    private_output["node_id"] = node_id
    public_output = {
        key: copy.deepcopy(private_output[key])
        for key in ("id", "role", "kind", "cardinality", "label", "description")
    }
    return private_output, public_output


def _validate_native_outputs(
    raw_inventory: Any, api_document: Mapping[str, Any]
) -> tuple[list[Any] | dict[str, Any], set[str]]:
    """Validate recognizable diagnostic claims without defining a brittle inventory schema."""

    if (
        not isinstance(raw_inventory, (list, Mapping))
        or not 1 <= len(raw_inventory) <= MAX_API_NODES
    ):
        raise ContractError(
            "manifest_invalid",
            "interface.native_outputs must be a non-empty bounded array or object.",
        )

    recognized_node_ids: set[str] = set()
    container_keys = {"entries", "items", "native_outputs", "nodes", "outputs"}

    def node_like(value: Any) -> bool:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return False
        node_id = str(value)
        return node_id in api_document or bool(re.fullmatch(r"[0-9]+(?::[0-9]+)*", node_id))

    def record(raw_node_id: Any, raw_class: Any, context: str) -> None:
        if isinstance(raw_node_id, bool) or not isinstance(raw_node_id, (str, int)):
            raise ContractError(
                "manifest_invalid", f"{context} contains an invalid node reference."
            )
        node_id = str(raw_node_id)
        node = api_document.get(node_id)
        if not isinstance(node, Mapping):
            raise ContractError(
                "manifest_invalid", f"{context} references a missing frozen API node."
            )
        if node_id in recognized_node_ids:
            raise ContractError(
                "manifest_invalid", "interface.native_outputs contains a duplicate node reference."
            )
        if raw_class is not None:
            class_type = _bounded_string(raw_class, f"{context}.class_type", 1, 200)
            if node.get("class_type") != class_type:
                raise ContractError(
                    "manifest_invalid",
                    f"{context}.class_type does not match the frozen API graph.",
                )
        recognized_node_ids.add(node_id)

    def inspect_entry(value: Any, context: str, *, key_hint: str | None = None) -> None:
        if isinstance(value, Mapping):
            raw_node_id = value.get("node_id")
            if raw_node_id is None and "id" in value and node_like(value.get("id")):
                raw_node_id = value.get("id")
            raw_class = value.get("class_type", value.get("node_class"))
            if raw_node_id is not None:
                record(raw_node_id, raw_class, context)
            elif key_hint is not None and node_like(key_hint):
                record(key_hint, raw_class, context)
            for key in container_keys:
                nested = value.get(key)
                if isinstance(nested, (list, Mapping)):
                    inspect_container(nested, f"{context}.{key}")
            return
        if key_hint is not None and node_like(key_hint):
            raw_class = value if isinstance(value, str) and not node_like(value) else None
            record(key_hint, raw_class, context)
        elif node_like(value):
            record(value, None, context)

    def inspect_container(value: list[Any] | Mapping[str, Any], context: str) -> None:
        if len(value) > MAX_API_NODES:
            raise ContractError(
                "manifest_invalid", "interface.native_outputs exceeds the supported size."
            )
        if isinstance(value, list):
            for index, item in enumerate(value):
                inspect_entry(item, f"{context}[{index}]")
            return
        if "node_id" in value or ("id" in value and node_like(value.get("id"))):
            inspect_entry(value, context)
            return
        for key, item in value.items():
            key_hint = str(key) if node_like(key) else None
            if key_hint is not None or key in container_keys:
                if key in container_keys and isinstance(item, (list, Mapping)):
                    inspect_container(item, f"{context}.{key}")
                else:
                    inspect_entry(item, f"{context}.{key}", key_hint=key_hint)

    inspect_container(raw_inventory, "interface.native_outputs")
    if not recognized_node_ids:
        raise ContractError(
            "manifest_invalid",
            "interface.native_outputs must identify at least one frozen API output node.",
        )
    copied_inventory: list[Any] | dict[str, Any]
    if isinstance(raw_inventory, list):
        copied_inventory = copy.deepcopy(raw_inventory)
    else:
        copied_inventory = copy.deepcopy(dict(raw_inventory))
    return copied_inventory, recognized_node_ids


def _validate_dependencies(
    raw_dependencies: Any, api_document: Mapping[str, Any]
) -> tuple[str, ...]:
    if not isinstance(raw_dependencies, Mapping):
        raise ContractError("manifest_invalid", "dependencies must be an object.")
    raw_class_types = raw_dependencies.get("class_types")
    if not isinstance(raw_class_types, list) or not raw_class_types:
        raise ContractError(
            "manifest_invalid", "dependencies.class_types must be a non-empty array."
        )
    class_types: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(raw_class_types):
        class_type = _bounded_string(value, f"dependencies.class_types[{index}]", 1, 200)
        if class_type in seen:
            raise ContractError("manifest_invalid", "dependencies.class_types must be unique.")
        seen.add(class_type)
        class_types.append(class_type)
    graph_classes = {
        str(node["class_type"]) for node in api_document.values() if isinstance(node, Mapping)
    }
    omitted = graph_classes - seen
    if omitted:
        raise ContractError(
            "manifest_invalid", "dependencies.class_types does not cover the frozen API graph."
        )
    return tuple(class_types)


def _validate_warnings(raw_warnings: Any) -> tuple[str, ...]:
    if not isinstance(raw_warnings, list) or len(raw_warnings) > MAX_WARNINGS:
        raise ContractError("manifest_invalid", "warnings must be an array of at most 256 entries.")
    result: list[str] = []
    for index, warning in enumerate(raw_warnings):
        if isinstance(warning, str):
            message = warning
        elif isinstance(warning, Mapping) and isinstance(warning.get("message"), str):
            message = str(warning["message"])
        else:
            raise ContractError("manifest_invalid", f"warnings[{index}] is invalid.")
        result.append(_bounded_string(message, f"warnings[{index}]", 1, 2_000))
    return tuple(result)


def _validate_runtime(raw_runtime: Any) -> dict[str, Any]:
    if not isinstance(raw_runtime, Mapping):
        raise ContractError("manifest_invalid", "runtime must be an object.")
    attach = raw_runtime.get("attach_workflow_as_extra_pnginfo", False)
    if not isinstance(attach, bool):
        raise ContractError(
            "manifest_invalid", "runtime.attach_workflow_as_extra_pnginfo must be Boolean."
        )
    return {"attach_workflow_as_extra_pnginfo": attach}


def _required_mapping(mapping: Mapping[str, Any], key: str, context: str) -> Mapping[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise ContractError("manifest_invalid", f"{context}.{key} must be an object.")
    return value


def _required_string(mapping: Mapping[str, Any], key: str, context: str) -> str:
    return _bounded_string(mapping.get(key), f"{context}.{key}", 1, 200)


def _bounded_string(value: Any, context: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ContractError(
            "manifest_invalid", f"{context} must contain {minimum} to {maximum} characters."
        )
    return value


def _public_id(value: Any, context: str) -> str:
    if not isinstance(value, str) or not PUBLIC_ID_RE.fullmatch(value):
        raise ContractError("manifest_invalid", f"{context} is not a valid public ID.")
    return value


def _semantic_role(value: Any, context: str) -> str:
    if not isinstance(value, str) or not SEMANTIC_ROLE_RE.fullmatch(value):
        raise ContractError("manifest_invalid", f"{context} is invalid.")
    return value


def _canonical_uuid(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ContractError("manifest_invalid", f"{context} must be a UUID.")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ContractError("manifest_invalid", f"{context} must be a UUID.") from exc
    if str(parsed) != value:
        raise ContractError(
            "manifest_invalid", f"{context} must use canonical lowercase UUID form."
        )
    return value


def _timestamp(value: Any, context: str) -> str:
    if not isinstance(value, str) or len(value) > 100:
        raise ContractError("manifest_invalid", f"{context} must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(
            "manifest_invalid", f"{context} must be an ISO-8601 timestamp."
        ) from exc
    if parsed.tzinfo is None:
        raise ContractError("manifest_invalid", f"{context} must include a timezone.")
    return value


def _sha256(value: Any, context: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ContractError("manifest_invalid", f"{context} must be a lowercase SHA-256 digest.")
    return value


def _boolean(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError("manifest_invalid", f"{context} must be Boolean.")
    return value


def _bounded_integer(value: Any, context: str, minimum: int, maximum: int) -> int:
    result = _integer_value(value, context)
    if result < minimum or result > maximum:
        raise ContractError("manifest_invalid", f"{context} is outside the supported range.")
    return result


def _integer_value(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("manifest_invalid", f"{context} must be an integer.")
    return int(value)


def _browser_safe_integer(value: Any, context: str) -> int:
    result = _integer_value(value, context)
    if not -MAX_BROWSER_SAFE_INTEGER <= result <= MAX_BROWSER_SAFE_INTEGER:
        raise ContractError("manifest_invalid", f"{context} must be a browser-safe integer.")
    return result


def _number_value(value: Any, context: str) -> int | float:
    if not _is_number(value):
        raise ContractError("manifest_invalid", f"{context} must be a finite number.")
    return value


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_numeric_value(
    value: int | float,
    minimum: int | float,
    maximum: int | float,
    step: int | float,
    context: str,
) -> None:
    if not _is_number(value) or value < minimum or value > maximum:
        raise ContractError("manifest_invalid", f"{context} is outside the declared range.")
    from decimal import Decimal, InvalidOperation

    try:
        remainder = (Decimal(str(value)) - Decimal(str(minimum))) % Decimal(str(step))
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise ContractError("manifest_invalid", f"{context} has invalid step semantics.") from exc
    if remainder != 0:
        raise ContractError("manifest_invalid", f"{context} does not align with the declared step.")


def find_file_references(value: Any) -> list[dict[str, str]]:
    """Collect only ComfyUI's allowlisted filename/subfolder/type tuples from native output JSON."""

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            filename = item.get("filename")
            storage_type = item.get("type", "output")
            subfolder = item.get("subfolder", "")
            if (
                isinstance(filename, str)
                and isinstance(storage_type, str)
                and isinstance(subfolder, str)
                and storage_type in {"input", "output", "temp"}
            ):
                key = (filename, subfolder, storage_type)
                if key not in seen:
                    seen.add(key)
                    result.append(
                        {"filename": filename, "subfolder": subfolder, "type": storage_type}
                    )
            for nested in item.values():
                visit(nested)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for nested in item:
                visit(nested)

    visit(value)
    return result
