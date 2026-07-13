from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import orjson

from ..errors import ContractError

SUPPORTED_CONTROL_TYPES = {
    "string",
    "multiline_string",
    "integer",
    "number",
    "boolean",
    "enum",
    "seed",
    "image_upload",
    "mask_upload",
    "asset_selector",
    "array",
    "object",
    "resolution",
    "output_role_set",
}
SUPPORTED_BINDINGS = {
    "patch_input",
    "patch_widget",
    "derive",
    "upload_then_patch",
    "select_branch",
    "select_variant",
    "request_policy",
    "fixed",
}
SUPPORTED_BRANCH_STRATEGIES = {
    "precompiled_variant",
    "graph_transform",
    "separate_workflow",
    "interaction_required",
    "unsupported",
}
ALLOWED_TOP_LEVEL_FIELDS = {
    "kind",
    "contract_schema_version",
    "workflow",
    "presentation",
    "requirements",
    "controls",
    "branches",
    "stages",
    "outputs",
    "progression",
    "presets",
    "policies",
    "extensions",
}
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
SUPPORTED_CONTRACT_SCHEMA_LINES = {(1, 1)}
SUPPORTED_ADAPTER_MAJOR_VERSIONS = {1}
UNAVAILABLE_BRANCH_REASONS = {
    "separate_workflow": "This branch requires a separately published workflow profile.",
    "interaction_required": "This branch requires an interactive workflow that is unavailable in one-shot generation.",
    "unsupported": "This workflow explicitly marks the branch as unsupported.",
}


@dataclass(frozen=True)
class ContractLocation:
    node_index: int
    source: str
    widget_index: int | None = None


@dataclass(frozen=True)
class ValidatedProfile:
    basename: str
    workflow_id: str
    display_name: str
    workflow_version: str
    contract_schema_version: str
    adapter_version: str
    ui_hash: str
    api_hash: str
    contract_hash: str
    identity_key: str
    ui_document: dict[str, Any]
    api_document: dict[str, Any]
    manifest: dict[str, Any]
    resolved_contract: dict[str, Any]
    runtime_snapshot: dict[str, Any]


class NodeIndex:
    """Structural selector resolver for approved API graphs."""

    def __init__(self, graph: Mapping[str, Any]):
        self.graph = graph

    @staticmethod
    def title(node: Mapping[str, Any]) -> str | None:
        meta = node.get("_meta")
        if isinstance(meta, Mapping) and isinstance(meta.get("title"), str):
            return str(meta["title"])
        value = node.get("title")
        return str(value) if isinstance(value, str) else None

    def resolve(self, selector: Mapping[str, Any], *, context: str) -> tuple[str, dict[str, Any]]:
        _validate_selector_shape(selector, context)
        matches: list[tuple[str, dict[str, Any]]] = []
        for raw_id, raw_node in self.graph.items():
            if str(raw_id).startswith("__") or not isinstance(raw_node, dict):
                continue
            node_id = str(raw_id)
            if selector.get("node_id") is not None and node_id != str(selector["node_id"]):
                continue
            class_type = selector.get("class_type")
            if class_type is not None and raw_node.get("class_type") != class_type:
                continue
            title = selector.get("title")
            if title is not None and self.title(raw_node) != title:
                continue
            expected_inputs = selector.get("expected_inputs", [])
            inputs = raw_node.get("inputs", {})
            if not isinstance(inputs, Mapping):
                continue
            if any(name not in inputs for name in expected_inputs):
                continue
            expected_default = selector.get("expected_default", _MISSING)
            expected_input = selector.get("expected_input")
            if expected_default is not _MISSING and expected_input:
                if inputs.get(expected_input, _MISSING) != expected_default:
                    continue
            matches.append((node_id, raw_node))
        if not matches:
            raise ContractError(
                "binding_not_found", f"{context}: selector did not resolve to a graph node."
            )
        if len(matches) > 1:
            raise ContractError(
                "binding_ambiguous", f"{context}: selector resolved to multiple graph nodes."
            )
        return matches[0]


_MISSING = object()


def canonical_json_bytes(value: Any) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def unwrap_api_document(api_document: Mapping[str, Any]) -> dict[str, Any]:
    if "prompt" in api_document and isinstance(api_document["prompt"], dict):
        return copy.deepcopy(api_document["prompt"])
    if "default" in api_document and isinstance(api_document["default"], dict):
        return copy.deepcopy(api_document["default"])
    return copy.deepcopy(dict(api_document))


def api_variants(api_document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = api_document.get("variants", {})
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): copy.deepcopy(value) for key, value in raw.items() if isinstance(value, dict)}


def extract_manifest(ui_document: Mapping[str, Any]) -> tuple[dict[str, Any], ContractLocation]:
    nodes = ui_document.get("nodes")
    if not isinstance(nodes, list):
        raise ContractError("contract_invalid", "UI workflow must contain a nodes array.")
    candidates: list[tuple[dict[str, Any], ContractLocation]] = []
    for index, raw_node in enumerate(nodes):
        if not isinstance(raw_node, dict):
            continue
        class_type = raw_node.get("type") or raw_node.get("class_type")
        if class_type != "FrontendWorkflowContract":
            continue
        direct_sources = [
            ("manifest", raw_node.get("manifest")),
            ("properties.manifest", _nested(raw_node, "properties", "manifest")),
            ("properties.manifest_json", _nested(raw_node, "properties", "manifest_json")),
            ("inputs.manifest_json", _nested(raw_node, "inputs", "manifest_json")),
        ]
        found: tuple[dict[str, Any], ContractLocation] | None = None
        for source, value in direct_sources:
            parsed = _parse_manifest_value(value)
            if parsed is not None:
                found = (parsed, ContractLocation(index, source))
                break
        if found is None:
            widgets = raw_node.get("widgets_values")
            if isinstance(widgets, list):
                for widget_index, value in enumerate(widgets):
                    parsed = _parse_manifest_value(value)
                    if parsed is not None:
                        found = (
                            parsed,
                            ContractLocation(index, "widgets_values", widget_index),
                        )
                        break
        if found is None:
            raise ContractError(
                "contract_invalid",
                "FrontendWorkflowContract node does not contain a readable manifest.",
            )
        candidates.append(found)
    if len(candidates) != 1:
        raise ContractError(
            "contract_invalid",
            f"UI workflow must contain exactly one FrontendWorkflowContract node; found {len(candidates)}.",
        )
    return candidates[0]


def _parse_manifest_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and value.get("kind") == "comfyui.frontend.workflow-contract":
        return copy.deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and parsed.get("kind") == "comfyui.frontend.workflow-contract":
            return parsed
    return None


def _nested(value: Mapping[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def normalized_ui_hash(ui_document: Mapping[str, Any], location: ContractLocation) -> str:
    """Hash a UI workflow without the self-referential declared graph hashes.

    The contract lives inside the UI workflow, so hashing the literal `ui_graph_sha256` value would
    be self-referential. The application canonicalizes by replacing both declared graph hash fields
    with empty strings at the located manifest source before sorting and hashing JSON. Workflow
    publishers must use the same documented canonicalization.
    """

    normalized = copy.deepcopy(dict(ui_document))
    node = normalized["nodes"][location.node_index]
    if location.source == "widgets_values":
        assert location.widget_index is not None
        value = node["widgets_values"][location.widget_index]
        manifest = _parse_manifest_value(value)
        assert manifest is not None
        _blank_declared_hashes(manifest)
        node["widgets_values"][location.widget_index] = json.dumps(
            manifest, separators=(",", ":"), sort_keys=True
        )
    else:
        path = location.source.split(".")
        parent = node
        for key in path[:-1]:
            parent = parent[key]
        raw = parent[path[-1]]
        manifest = _parse_manifest_value(raw)
        assert manifest is not None
        _blank_declared_hashes(manifest)
        parent[path[-1]] = json.dumps(manifest, separators=(",", ":"), sort_keys=True) if isinstance(raw, str) else manifest
    return sha256_json(normalized)


def _blank_declared_hashes(manifest: dict[str, Any]) -> None:
    workflow = manifest.get("workflow")
    if isinstance(workflow, dict):
        workflow["ui_graph_sha256"] = ""
        workflow["api_graph_sha256"] = ""


def _validate_selector_shape(selector: Mapping[str, Any], context: str) -> None:
    if not selector:
        raise ContractError("contract_invalid", f"{context}: selector is required.")
    structural = {
        "class_type",
        "title",
        "group",
        "subgraph_path",
        "route_name",
        "expected_inputs",
        "expected_default",
    }
    if "node_id" in selector and not any(key in selector for key in structural):
        raise ContractError(
            "contract_invalid", f"{context}: selector must not rely on node_id alone."
        )
    if "node_id" not in selector and not any(key in selector for key in ("class_type", "title")):
        raise ContractError(
            "contract_invalid", f"{context}: selector needs node_id, class_type, or title."
        )


def _all_runtime_inputs(runtime_node: Mapping[str, Any]) -> set[str]:
    inputs = runtime_node.get("input", {})
    if not isinstance(inputs, Mapping):
        return set()
    names: set[str] = set()
    for section in ("required", "optional", "hidden"):
        values = inputs.get(section, {})
        if isinstance(values, Mapping):
            names.update(str(name) for name in values)
    return names


def _looks_like_node_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
        and value[1] >= 0
    )


def validate_runtime_graph(
    graph: Mapping[str, Any],
    object_info: Mapping[str, Any],
    *,
    context: str,
    structural_error_code: str = "contract_invalid",
) -> None:
    """Fail closed against the live ComfyUI node schemas and graph topology.

    Discovery uses this for the approved base graph, every named API variant, and
    simulated graph-transform outcomes. The compiler invokes the same routine on
    the exact patched graph, so registration-time and request-time strictness do
    not drift apart.
    """

    node_ids = {
        str(node_id)
        for node_id, node in graph.items()
        if not str(node_id).startswith("__") and isinstance(node, Mapping)
    }
    if not node_ids:
        raise ContractError(structural_error_code, f"{context} is empty.")

    for raw_node_id, node in graph.items():
        node_id = str(raw_node_id)
        if node_id.startswith("__"):
            continue
        if not isinstance(node, Mapping):
            raise ContractError(
                structural_error_code,
                f"{context} node {node_id} is not an object.",
            )
        class_type = node.get("class_type")
        runtime_node = object_info.get(class_type) if isinstance(class_type, str) else None
        if not isinstance(runtime_node, Mapping):
            raise ContractError(
                "runtime_dependency_missing",
                f"{context} node {node_id} uses unavailable class {class_type!r}.",
            )
        inputs = node.get("inputs", {})
        if not isinstance(inputs, Mapping):
            raise ContractError(
                structural_error_code,
                f"{context} node {node_id} has invalid inputs.",
            )

        runtime_input = runtime_node.get("input", {})
        if not isinstance(runtime_input, Mapping):
            raise ContractError(
                "runtime_dependency_missing",
                f"Runtime schema for {class_type!r} has no valid input declaration.",
            )
        allowed_inputs: set[str] = set()
        required_inputs: set[str] = set()
        for section in ("required", "optional", "hidden"):
            fields = runtime_input.get(section, {})
            if not isinstance(fields, Mapping):
                raise ContractError(
                    "runtime_dependency_missing",
                    f"Runtime input schema section {section!r} for {class_type!r} is invalid.",
                )
            names = {str(name) for name in fields}
            allowed_inputs.update(names)
            if section == "required":
                required_inputs.update(names)

        supplied_inputs = {str(name) for name in inputs}
        unknown_inputs = supplied_inputs - allowed_inputs
        if unknown_inputs:
            raise ContractError(
                structural_error_code,
                f"{context} node {node_id} has inputs not declared by the runtime schema: "
                f"{', '.join(sorted(unknown_inputs))}.",
            )
        missing_inputs = required_inputs - supplied_inputs
        if missing_inputs:
            raise ContractError(
                structural_error_code,
                f"{context} node {node_id} is missing required inputs: "
                f"{', '.join(sorted(missing_inputs))}.",
            )

        for input_name, value in inputs.items():
            if not _looks_like_node_link(value):
                continue
            source_id = str(value[0])
            output_index = value[1]
            if source_id not in node_ids:
                raise ContractError(
                    structural_error_code,
                    f"{context} node {node_id} input {input_name!r} references missing node "
                    f"{source_id}.",
                )
            source_node = graph.get(source_id)
            if source_node is None:
                source_node = next(
                    (candidate for key, candidate in graph.items() if str(key) == source_id),
                    None,
                )
            if not isinstance(source_node, Mapping):
                raise ContractError(
                    structural_error_code,
                    f"{context} node {node_id} input {input_name!r} has an invalid source node.",
                )
            source_runtime = object_info.get(source_node.get("class_type"), {})
            outputs = source_runtime.get("output") if isinstance(source_runtime, Mapping) else None
            if isinstance(outputs, Sequence) and not isinstance(outputs, (str, bytes)):
                if output_index >= len(outputs):
                    raise ContractError(
                        structural_error_code,
                        f"{context} node {node_id} input {input_name!r} references unavailable "
                        f"output {output_index} on node {source_id}.",
                    )


def _enum_values(runtime_node: Mapping[str, Any], input_name: str) -> list[Any]:
    inputs = runtime_node.get("input", {})
    if not isinstance(inputs, Mapping):
        return []
    for section in ("required", "optional"):
        values = inputs.get(section, {})
        if not isinstance(values, Mapping) or input_name not in values:
            continue
        spec = values[input_name]
        if isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)) and spec:
            first = spec[0]
            if isinstance(first, Sequence) and not isinstance(first, (str, bytes)):
                return list(first)
    return []


def _validate_version(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SEMVER_RE.match(value):
        raise ContractError("contract_invalid", f"{field} must be a semantic version.")
    return value


def _version_numbers(value: str) -> tuple[int, int, int]:
    core = re.split(r"[-+]", value, maxsplit=1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)


def _semantic_value_active(value: Any) -> bool:
    if value in (None, False, 0, "", "off", "disabled", "none"):
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _require_string(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError("contract_invalid", f"{context}.{key} must be a non-empty string.")
    return value


def _validate_input_target(
    *,
    selector: Mapping[str, Any],
    input_name: str,
    graph_indexes: Sequence[tuple[str, NodeIndex]],
    object_info: Mapping[str, Any],
    context: str,
) -> dict[str, Any]:
    base_node: dict[str, Any] | None = None
    for graph_name, graph_index in graph_indexes:
        _, node = graph_index.resolve(selector, context=f"{context} in {graph_name}")
        if base_node is None:
            base_node = node
        class_type = node.get("class_type")
        runtime_node = object_info.get(class_type)
        if not isinstance(runtime_node, Mapping):
            raise ContractError(
                "runtime_dependency_missing",
                f"{context}: runtime schema for {class_type!r} is missing.",
            )
        runtime_inputs = _all_runtime_inputs(runtime_node)
        node_inputs = node.get("inputs", {})
        if input_name not in runtime_inputs and (
            not isinstance(node_inputs, Mapping) or input_name not in node_inputs
        ):
            raise ContractError(
                "binding_not_found",
                f"{context}: input {input_name!r} does not exist on {class_type!r} in "
                f"{graph_name}.",
            )
    assert base_node is not None
    return base_node


def _apply_registration_graph_operation(
    graph: dict[str, Any], operation: Any, branch_id: str
) -> None:
    if not isinstance(operation, Mapping):
        raise ContractError(
            "branch_compilation_failed", f"Branch {branch_id} operation must be an object."
        )
    op = operation.get("op")
    selector = operation.get("selector")
    if not isinstance(selector, Mapping):
        raise ContractError(
            "branch_compilation_failed", f"Branch {branch_id} operation selector is missing."
        )
    node_id, node = NodeIndex(graph).resolve(selector, context=f"branch {branch_id}")
    if op == "remove_node":
        graph.pop(node_id, None)
        return
    if op in {"set_input", "rewire_input"}:
        input_name = operation.get("input")
        if not isinstance(input_name, str) or not input_name:
            raise ContractError(
                "branch_compilation_failed", f"Branch {branch_id} graph input is invalid."
            )
        value = copy.deepcopy(operation.get("value"))
        if op == "rewire_input" and not _looks_like_node_link(value):
            raise ContractError(
                "branch_compilation_failed",
                f"Branch {branch_id} rewire operation must reference [node_id, output_index].",
            )
        inputs = node.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            raise ContractError(
                "branch_compilation_failed", f"Branch {branch_id} target inputs are invalid."
            )
        inputs[input_name] = value
        return
    if op == "set_class_type":
        class_type = operation.get("class_type")
        if not isinstance(class_type, str) or not class_type:
            raise ContractError(
                "branch_compilation_failed", f"Branch {branch_id} graph class is invalid."
            )
        node["class_type"] = class_type
        return
    if op == "set_mode":
        value = operation.get("value")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ContractError(
                "branch_compilation_failed", f"Branch {branch_id} graph mode must be an integer."
            )
        meta = node.setdefault("_meta", {})
        if not isinstance(meta, dict):
            raise ContractError(
                "branch_compilation_failed", f"Branch {branch_id} target metadata is invalid."
            )
        meta["mode"] = value
        return
    if op == "set_meta":
        key = operation.get("key")
        if not isinstance(key, str) or not key:
            raise ContractError(
                "branch_compilation_failed", f"Branch {branch_id} metadata key is invalid."
            )
        meta = node.setdefault("_meta", {})
        if not isinstance(meta, dict):
            raise ContractError(
                "branch_compilation_failed", f"Branch {branch_id} target metadata is invalid."
            )
        meta[key] = copy.deepcopy(operation.get("value"))
        return
    raise ContractError(
        "branch_compilation_failed",
        f"Branch {branch_id} uses unsupported graph transform operation {op!r}.",
    )


def _validate_graph_transform_branch(
    branch: Mapping[str, Any],
    graph_entries: Sequence[tuple[str, Mapping[str, Any]]],
    object_info: Mapping[str, Any],
) -> None:
    branch_id = str(branch["id"])
    transforms = branch.get("transforms")
    if not isinstance(transforms, Mapping):
        raise ContractError(
            "contract_invalid", f"Branch {branch_id} graph transforms must be an object."
        )
    for mode in ("enable", "disable"):
        operations = transforms.get(mode, [])
        if not isinstance(operations, list):
            raise ContractError(
                "contract_invalid", f"Branch {branch_id} {mode} transforms must be an array."
            )
        for graph_name, source_graph in graph_entries:
            candidate = copy.deepcopy(dict(source_graph))
            for operation in operations:
                _apply_registration_graph_operation(candidate, operation, branch_id)
            validate_runtime_graph(
                candidate,
                object_info,
                context=f"Branch {branch_id} {mode} result for {graph_name}",
                structural_error_code="branch_compilation_failed",
            )


def validate_profile(
    *,
    basename: str,
    ui_document: dict[str, Any],
    api_document: dict[str, Any],
    object_info: dict[str, Any],
    runtime_capabilities: dict[str, Any] | None = None,
) -> ValidatedProfile:
    runtime_capabilities = runtime_capabilities or {}
    manifest, location = extract_manifest(ui_document)
    unknown_top = set(manifest) - ALLOWED_TOP_LEVEL_FIELDS
    if unknown_top:
        raise ContractError(
            "contract_invalid",
            f"Unknown top-level contract fields: {', '.join(sorted(unknown_top))}.",
        )
    if manifest.get("kind") != "comfyui.frontend.workflow-contract":
        raise ContractError("contract_invalid", "Contract kind is invalid.")
    contract_schema_version = _validate_version(
        manifest.get("contract_schema_version"), "contract_schema_version"
    )
    schema_major, schema_minor, _ = _version_numbers(contract_schema_version)
    if (schema_major, schema_minor) not in SUPPORTED_CONTRACT_SCHEMA_LINES:
        raise ContractError(
            "contract_invalid",
            f"Unsupported contract schema version {contract_schema_version}; this adapter supports 1.1.x.",
        )
    workflow = manifest.get("workflow")
    if not isinstance(workflow, dict):
        raise ContractError("contract_invalid", "Contract workflow object is required.")
    workflow_id = _require_string(workflow, "id", "workflow")
    display_name = _require_string(workflow, "display_name", "workflow")
    workflow_version = _validate_version(workflow.get("version"), "workflow.version")
    adapter_version = workflow.get("adapter_version", "1.0.0")
    adapter_version = _validate_version(adapter_version, "workflow.adapter_version")
    adapter_major, _, _ = _version_numbers(adapter_version)
    if adapter_major not in SUPPORTED_ADAPTER_MAJOR_VERSIONS:
        raise ContractError(
            "contract_invalid",
            f"Unsupported workflow adapter version {adapter_version}; this application supports adapter 1.x.",
        )
    declared_ui_hash = _require_string(workflow, "ui_graph_sha256", "workflow")
    declared_api_hash = _require_string(workflow, "api_graph_sha256", "workflow")
    if not SHA256_RE.match(declared_ui_hash) or not SHA256_RE.match(declared_api_hash):
        raise ContractError("contract_invalid", "Declared graph hashes must be lowercase SHA-256.")

    computed_ui_hash = normalized_ui_hash(ui_document, location)
    computed_api_hash = sha256_json(api_document)
    if computed_ui_hash != declared_ui_hash:
        raise ContractError(
            "workflow_hash_mismatch",
            "UI workflow hash does not match the contract declaration.",
            {"declared": declared_ui_hash, "computed": computed_ui_hash},
        )
    if computed_api_hash != declared_api_hash:
        raise ContractError(
            "workflow_hash_mismatch",
            "API graph hash does not match the contract declaration.",
            {"declared": declared_api_hash, "computed": computed_api_hash},
        )

    graph = unwrap_api_document(api_document)
    if not graph:
        raise ContractError("contract_invalid", "Approved API graph is empty.")
    variants = api_variants(api_document)
    graph_entries: list[tuple[str, Mapping[str, Any]]] = [("approved API graph", graph)]
    for variant_name, variant_graph in variants.items():
        if not variant_graph:
            raise ContractError("contract_invalid", f"API variant {variant_name!r} is empty.")
        graph_entries.append((f"API variant {variant_name!r}", variant_graph))
    for graph_name, candidate_graph in graph_entries:
        validate_runtime_graph(candidate_graph, object_info, context=graph_name)
    graph_indexes = [(name, NodeIndex(candidate)) for name, candidate in graph_entries]

    requirements = manifest.get("requirements", {})
    if not isinstance(requirements, dict):
        raise ContractError("contract_invalid", "requirements must be an object.")
    capability_states: dict[str, dict[str, Any]] = {}
    runtime_requirements = requirements.get("runtime", {})
    if not isinstance(runtime_requirements, Mapping):
        raise ContractError("contract_invalid", "requirements.runtime must be an object.")
    advertised_capabilities = runtime_capabilities.get("capabilities", {})
    if not isinstance(advertised_capabilities, Mapping):
        advertised_capabilities = {}
    features = runtime_requirements.get("features", [])
    if not isinstance(features, list):
        raise ContractError("contract_invalid", "requirements.runtime.features must be an array.")
    for raw_feature in features:
        if isinstance(raw_feature, str):
            feature_name = raw_feature
            required = True
            capability_name = raw_feature
        elif isinstance(raw_feature, Mapping):
            feature_name = raw_feature.get("id") or raw_feature.get("name") or raw_feature.get("feature")
            if not isinstance(feature_name, str) or not feature_name:
                raise ContractError("contract_invalid", "Runtime feature entries need an id or name.")
            required = bool(raw_feature.get("required", True))
            capability_name = str(raw_feature.get("capability", feature_name))
        else:
            raise ContractError("contract_invalid", "Runtime feature entries must be strings or objects.")
        available = bool(advertised_capabilities.get(feature_name, False))
        capability_states[capability_name] = {
            "available": available,
            "reason": None if available else f"Required runtime feature {feature_name} is unavailable.",
        }
        if required and not available:
            raise ContractError(
                "runtime_dependency_missing",
                f"Required ComfyUI runtime feature {feature_name} is unavailable.",
            )

    minimum_version = runtime_requirements.get("minimum_comfyui_version")
    if minimum_version not in {None, ""}:
        minimum_version = _validate_version(minimum_version, "requirements.runtime.minimum_comfyui_version")
        system = runtime_capabilities.get("system", {})
        actual_version = None
        if isinstance(system, Mapping):
            actual_version = system.get("comfyui_version") or system.get("version")
        if not isinstance(actual_version, str) or not SEMVER_RE.match(actual_version):
            raise ContractError(
                "runtime_dependency_missing",
                "ComfyUI did not report a comparable runtime version required by this workflow.",
            )
        if _version_numbers(actual_version) < _version_numbers(minimum_version):
            raise ContractError(
                "runtime_dependency_missing",
                f"Workflow requires ComfyUI {minimum_version} or newer; runtime is {actual_version}.",
            )

    for requirement in requirements.get("node_classes", []):
        if not isinstance(requirement, dict):
            raise ContractError("contract_invalid", "node_classes entries must be objects.")
        class_type = _require_string(requirement, "class_type", "requirements.node_classes")
        available = class_type in object_info
        capability = requirement.get("capability")
        if capability:
            capability_states[str(capability)] = {
                "available": available,
                "reason": None if available else f"Required node class {class_type} is unavailable.",
            }
        if requirement.get("required", True) and not available:
            raise ContractError(
                "runtime_dependency_missing", f"Required ComfyUI node class {class_type} is missing."
            )

    runtime_assets = _collect_runtime_assets(object_info, runtime_capabilities)
    for asset in requirements.get("assets", []):
        if not isinstance(asset, dict):
            raise ContractError("contract_invalid", "assets entries must be objects.")
        path = _require_string(asset, "path", "requirements.assets")
        available = path in runtime_assets
        capability = asset.get("capability")
        if capability:
            capability_states[str(capability)] = {
                "available": available,
                "reason": None if available else f"Required asset {path} is unavailable.",
            }
        # A required asset is a hard registration prerequisite.  Treat an
        # empty inventory as "not found", not as permission to skip the
        # check: the contract is deliberately fail-closed when the adapter
        # cannot prove that an allowlisted model or other asset exists.
        if asset.get("required", True) and not available:
            raise ContractError("asset_missing", f"Required workflow asset {path} is unavailable.")

    raw_controls = manifest.get("controls")
    if not isinstance(raw_controls, list) or not raw_controls:
        raise ContractError("contract_invalid", "Contract must declare controls.")
    controls: list[dict[str, Any]] = []
    control_ids: set[str] = set()
    branches_raw = manifest.get("branches", [])
    if not isinstance(branches_raw, list):
        raise ContractError("contract_invalid", "branches must be an array.")
    branch_ids: set[str] = set()
    branch_states: dict[str, dict[str, Any]] = {}
    for branch in branches_raw:
        if not isinstance(branch, dict):
            raise ContractError("contract_invalid", "branch entries must be objects.")
        branch_id = _require_string(branch, "id", "branch")
        if branch_id in branch_ids:
            raise ContractError("contract_invalid", f"Duplicate branch ID {branch_id}.")
        branch_ids.add(branch_id)
        strategy = branch.get("strategy")
        if strategy not in SUPPORTED_BRANCH_STRATEGIES:
            raise ContractError("contract_invalid", f"Unsupported branch strategy {strategy!r}.")
        reason = UNAVAILABLE_BRANCH_REASONS.get(str(strategy))
        branch_states[branch_id] = {"available": reason is None, "reason": reason}
        if reason and _semantic_value_active(branch.get("default_enabled", False)):
            raise ContractError(
                "branch_compilation_failed",
                f"Branch {branch_id} is enabled by default but cannot be compiled: {reason}",
            )
        if strategy == "precompiled_variant":
            variant_map = branch.get("variants", {})
            if not isinstance(variant_map, Mapping) or not variant_map:
                raise ContractError(
                    "contract_invalid", f"Branch {branch_id} must declare precompiled variants."
                )
            for key, variant in variant_map.items():
                if not isinstance(key, str) or not key:
                    raise ContractError(
                        "contract_invalid",
                        f"Branch {branch_id} variant selector keys must be non-empty strings.",
                    )
                if not isinstance(variant, str) or variant not in variants:
                    raise ContractError(
                        "contract_invalid",
                        f"Branch {branch_id} references missing API variant {variant!r}.",
                    )
            default_value = branch.get("default_enabled", branch.get("default", _MISSING))
            if default_value is not _MISSING:
                default_keys = (str(default_value).lower(), str(default_value))
                if not any(key in variant_map for key in default_keys):
                    raise ContractError(
                        "contract_invalid",
                        f"Branch {branch_id} has no precompiled variant for its default value.",
                    )
        elif strategy == "graph_transform":
            _validate_graph_transform_branch(branch, graph_entries, object_info)

    for raw_control in raw_controls:
        if not isinstance(raw_control, dict):
            raise ContractError("contract_invalid", "Control entries must be objects.")
        control = copy.deepcopy(raw_control)
        control_id = _require_string(control, "id", "control")
        if control_id in control_ids:
            raise ContractError("contract_invalid", f"Duplicate control ID {control_id}.")
        control_ids.add(control_id)
        _require_string(control, "label", f"control {control_id}")
        control_type = control.get("type")
        if control_type not in SUPPORTED_CONTROL_TYPES:
            raise ContractError(
                "contract_invalid", f"Control {control_id} has unsupported type {control_type!r}."
            )
        tier = control.get("tier")
        if tier not in {"basic", "advanced", "operator"}:
            raise ContractError("contract_invalid", f"Control {control_id} has invalid tier.")
        bindings = control.get("bindings")
        if not isinstance(bindings, list) or not bindings:
            raise ContractError("contract_invalid", f"Control {control_id} must declare bindings.")
        capability = control.get("capability")
        if capability and str(capability) not in capability_states:
            advertised = runtime_capabilities.get("capabilities", {})
            is_available = bool(advertised.get(capability, False)) if isinstance(advertised, Mapping) else False
            capability_states[str(capability)] = {
                "available": is_available,
                "reason": None if is_available else "Optional capability is unavailable.",
            }
        for binding_index, binding in enumerate(bindings):
            if not isinstance(binding, dict):
                raise ContractError("contract_invalid", f"Control {control_id} binding is invalid.")
            strategy = binding.get("strategy")
            if strategy not in SUPPORTED_BINDINGS:
                raise ContractError(
                    "contract_invalid", f"Control {control_id} has unsupported binding {strategy!r}."
                )
            context = f"control {control_id} binding {binding_index}"
            if strategy in {"patch_input", "patch_widget", "upload_then_patch"}:
                selector = binding.get("selector")
                if not isinstance(selector, Mapping):
                    raise ContractError("contract_invalid", f"{context}: selector is required.")
                input_name = binding.get("input") or binding.get("widget_name")
                if not isinstance(input_name, str) or not input_name:
                    raise ContractError("contract_invalid", f"{context}: input name is required.")
                _validate_input_target(
                    selector=selector,
                    input_name=input_name,
                    graph_indexes=graph_indexes,
                    object_info=object_info,
                    context=context,
                )
            elif strategy == "derive":
                targets = binding.get("targets")
                if not isinstance(targets, list) or not targets:
                    raise ContractError("contract_invalid", f"{context}: derive targets are required.")
                for target in targets:
                    _validate_derive_target(target, graph_indexes, object_info, context)
            elif strategy in {"select_branch", "select_variant"}:
                branch_id = binding.get("branch_id")
                if branch_id not in branch_ids:
                    raise ContractError(
                        "contract_invalid", f"{context}: unknown branch {branch_id!r}."
                    )
                branch_state = branch_states.get(str(branch_id), {"available": True, "reason": None})
                if not branch_state["available"]:
                    if _semantic_value_active(control.get("default")):
                        raise ContractError(
                            "branch_compilation_failed",
                            f"Control {control_id} enables unavailable branch {branch_id} by default.",
                        )
                    control["available"] = False
                    control["unavailable_reason"] = branch_state["reason"]

        options = control.get("options")
        if isinstance(options, dict) and options.get("source") == "comfyui_object_info":
            selector = options.get("selector")
            input_name = options.get("input")
            if not isinstance(selector, Mapping) or not isinstance(input_name, str):
                raise ContractError(
                    "contract_invalid", f"Control {control_id} dynamic options are incomplete."
                )
            class_type = selector.get("class_type")
            runtime_node = object_info.get(class_type)
            if not isinstance(runtime_node, Mapping):
                raise ContractError(
                    "runtime_dependency_missing",
                    f"Control {control_id} option provider class is unavailable.",
                )
            runtime_values = _enum_values(runtime_node, input_name)
            allowlist = options.get("allowlist", runtime_values)
            if not isinstance(allowlist, list):
                raise ContractError("contract_invalid", f"Control {control_id} allowlist is invalid.")
            resolved = [item for item in runtime_values if item in allowlist]
            if not resolved and control.get("required", False):
                raise ContractError(
                    "runtime_dependency_missing",
                    f"Control {control_id} has no runtime-approved options.",
                )
            control["options"] = {**options, "resolved_values": resolved}
        controls.append(control)

    prompt_controls = [item for item in controls if item.get("id") == "prompt.text"]
    if len(prompt_controls) != 1 or prompt_controls[0].get("type") not in {
        "string",
        "multiline_string",
    }:
        raise ContractError(
            "contract_invalid",
            "Profile must expose exactly one string or multiline_string control named prompt.text.",
        )

    _validate_control_references(controls, control_ids)
    stages = _validate_stages(manifest.get("stages", []), graph_indexes)
    outputs = _validate_outputs(manifest.get("outputs", []), graph_indexes)
    output_ids = {output["id"] for output in outputs}
    progression = manifest.get("progression", {})
    if progression:
        if not isinstance(progression, dict):
            raise ContractError("contract_invalid", "progression must be an object.")
        for output_id in progression.get("ordered_output_ids", []):
            if output_id not in output_ids:
                raise ContractError(
                    "contract_invalid", f"Progression references unknown output {output_id!r}."
                )
        terminal = progression.get("terminal_output_id")
        if terminal and terminal not in output_ids:
            raise ContractError("contract_invalid", "Progression terminal output is unknown.")

    resolved_contract = copy.deepcopy(manifest)
    resolved_contract["controls"] = controls
    resolved_contract["branches"] = branches_raw
    resolved_contract["stages"] = stages
    resolved_contract["outputs"] = outputs
    resolved_contract["capability_states"] = capability_states
    resolved_contract["branch_states"] = branch_states
    resolved_contract["runtime"] = {
        "node_classes": sorted(object_info.keys()),
        "capabilities": runtime_capabilities.get("capabilities", {}),
    }
    contract_hash = sha256_json(manifest)
    identity_key = "|".join(
        [workflow_id, workflow_version, computed_ui_hash, computed_api_hash, contract_hash]
    )
    runtime_snapshot = {
        "object_info_sha256": sha256_json(object_info),
        "object_info": copy.deepcopy(object_info),
        "capability_states": capability_states,
        "assets": sorted(runtime_assets),
        "runtime_capabilities": copy.deepcopy(runtime_capabilities),
    }
    return ValidatedProfile(
        basename=basename,
        workflow_id=workflow_id,
        display_name=display_name,
        workflow_version=workflow_version,
        contract_schema_version=contract_schema_version,
        adapter_version=adapter_version,
        ui_hash=computed_ui_hash,
        api_hash=computed_api_hash,
        contract_hash=contract_hash,
        identity_key=identity_key,
        ui_document=copy.deepcopy(ui_document),
        api_document=copy.deepcopy(api_document),
        manifest=manifest,
        resolved_contract=resolved_contract,
        runtime_snapshot=runtime_snapshot,
    )


def _validate_derive_target(
    target: Any,
    graph_indexes: Sequence[tuple[str, NodeIndex]],
    object_info: Mapping[str, Any],
    context: str,
) -> None:
    if not isinstance(target, Mapping):
        raise ContractError("contract_invalid", f"{context}: derive target must be an object.")
    selector = target.get("selector")
    input_name = target.get("input")
    if not isinstance(selector, Mapping) or not isinstance(input_name, str):
        raise ContractError("contract_invalid", f"{context}: derive target is incomplete.")
    _validate_input_target(
        selector=selector,
        input_name=input_name,
        graph_indexes=graph_indexes,
        object_info=object_info,
        context=context,
    )


def _validate_control_references(controls: Sequence[Mapping[str, Any]], control_ids: set[str]) -> None:
    for control in controls:
        control_id = str(control["id"])
        for conflict in control.get("conflicts_with", []):
            if conflict not in control_ids:
                raise ContractError(
                    "contract_invalid", f"Control {control_id} conflicts with unknown control {conflict}."
                )
        for requirement in control.get("requires", []):
            target = requirement if isinstance(requirement, str) else requirement.get("control")
            if target not in control_ids:
                raise ContractError(
                    "contract_invalid", f"Control {control_id} requires unknown control {target}."
                )
        for condition in control.get("conditions", []):
            if not isinstance(condition, Mapping):
                raise ContractError("contract_invalid", f"Control {control_id} condition is invalid.")
            when = condition.get("when", {})
            target = when.get("control") if isinstance(when, Mapping) else None
            if target not in control_ids:
                raise ContractError(
                    "contract_invalid", f"Control {control_id} condition references {target!r}."
                )
            if condition.get("effect") not in {
                "visible",
                "hidden",
                "enabled",
                "disabled",
                "required",
                "forbidden",
            }:
                raise ContractError(
                    "contract_invalid", f"Control {control_id} condition has invalid effect."
                )


def _validate_stages(
    raw_stages: Any, graph_indexes: Sequence[tuple[str, NodeIndex]]
) -> list[dict[str, Any]]:
    if not isinstance(raw_stages, list):
        raise ContractError("contract_invalid", "stages must be an array.")
    ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for stage in raw_stages:
        if not isinstance(stage, dict):
            raise ContractError("contract_invalid", "stage entries must be objects.")
        stage_id = _require_string(stage, "id", "stage")
        if stage_id in ids:
            raise ContractError("contract_invalid", f"Duplicate stage ID {stage_id}.")
        ids.add(stage_id)
        _require_string(stage, "label", f"stage {stage_id}")
        if not isinstance(stage.get("sequence"), int):
            raise ContractError("contract_invalid", f"Stage {stage_id} sequence must be integer.")
        selectors = stage.get("node_selectors", [])
        if not isinstance(selectors, list):
            raise ContractError("contract_invalid", f"Stage {stage_id} selectors must be an array.")
        resolved_node_ids: list[str] = []
        for selector in selectors:
            if not isinstance(selector, Mapping):
                raise ContractError("contract_invalid", f"Stage {stage_id} selector is invalid.")
            node_id = ""
            for graph_position, (graph_name, graph_index) in enumerate(graph_indexes):
                resolved_id, _ = graph_index.resolve(
                    selector, context=f"stage {stage_id} in {graph_name}"
                )
                if graph_position == 0:
                    node_id = resolved_id
            resolved_node_ids.append(node_id)
        enriched = copy.deepcopy(stage)
        enriched["resolved_node_ids"] = resolved_node_ids
        result.append(enriched)
    return sorted(result, key=lambda item: (item.get("sequence", 0), item["id"]))


def _validate_outputs(
    raw_outputs: Any, graph_indexes: Sequence[tuple[str, NodeIndex]]
) -> list[dict[str, Any]]:
    if not isinstance(raw_outputs, list) or not raw_outputs:
        raise ContractError("contract_invalid", "Contract must declare outputs.")
    ids: set[str] = set()
    result: list[dict[str, Any]] = []
    canonical_count = 0
    for output in raw_outputs:
        if not isinstance(output, dict):
            raise ContractError("contract_invalid", "output entries must be objects.")
        output_id = _require_string(output, "id", "output")
        if output_id in ids:
            raise ContractError("contract_invalid", f"Duplicate output ID {output_id}.")
        ids.add(output_id)
        _require_string(output, "role", f"output {output_id}")
        kind = output.get("kind")
        if kind not in {"image", "text", "metadata"}:
            raise ContractError("contract_invalid", f"Output {output_id} has unsupported kind.")
        selector = output.get("selector")
        if not isinstance(selector, Mapping):
            raise ContractError("contract_invalid", f"Output {output_id} selector is required.")
        node_id = ""
        for graph_position, (graph_name, graph_index) in enumerate(graph_indexes):
            resolved_id, _ = graph_index.resolve(
                selector, context=f"output {output_id} in {graph_name}"
            )
            if graph_position == 0:
                node_id = resolved_id
        progression = output.get("progression", {})
        if progression and not isinstance(progression, dict):
            raise ContractError("contract_invalid", f"Output {output_id} progression is invalid.")
        sequence = progression.get("sequence", output.get("sequence", 0))
        if not isinstance(sequence, int):
            raise ContractError("contract_invalid", f"Output {output_id} sequence must be integer.")
        canonical = bool(output.get("canonical_on_success", False))
        canonical_count += int(canonical)
        enriched = copy.deepcopy(output)
        enriched["resolved_node_id"] = node_id
        enriched["resolved_sequence"] = sequence
        result.append(enriched)
    if canonical_count != 1:
        raise ContractError(
            "contract_invalid",
            f"Contract must designate exactly one canonical_on_success output; found {canonical_count}.",
        )
    return sorted(result, key=lambda item: (item["resolved_sequence"], item["id"]))


def _collect_runtime_assets(
    object_info: Mapping[str, Any], runtime_capabilities: Mapping[str, Any]
) -> set[str]:
    assets: set[str] = set()
    raw_assets = runtime_capabilities.get("assets", [])
    if isinstance(raw_assets, Iterable) and not isinstance(raw_assets, (str, bytes, Mapping)):
        assets.update(str(item) for item in raw_assets)
    for runtime_node in object_info.values():
        if not isinstance(runtime_node, Mapping):
            continue
        inputs = runtime_node.get("input", {})
        if not isinstance(inputs, Mapping):
            continue
        for section in ("required", "optional"):
            fields = inputs.get(section, {})
            if not isinstance(fields, Mapping):
                continue
            for spec in fields.values():
                if not isinstance(spec, Sequence) or isinstance(spec, (str, bytes)) or not spec:
                    continue
                choices = spec[0]
                if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)):
                    for choice in choices:
                        if isinstance(choice, str) and ("/" in choice or "." in choice):
                            assets.add(choice)
    return assets
