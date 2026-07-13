from __future__ import annotations

import copy
import math
import re
import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence

from ..errors import AppError, ContractError
from .contract import (
    NodeIndex,
    api_variants,
    sha256_json,
    unwrap_api_document,
    validate_runtime_graph,
)


@dataclass(frozen=True)
class CompileResult:
    requested_controls: dict[str, Any]
    effective_controls: dict[str, Any]
    resolved_seeds: dict[str, int]
    compiled_graph: dict[str, Any]
    compiled_graph_hash: str
    selected_uploads: dict[str, str]
    requested_outputs: list[str]
    final_prompt: str
    selected_preset: str | None


RandomSeed = Callable[[int, int], int]


class WorkflowCompiler:
    def __init__(self, *, seed_resolver: RandomSeed | None = None):
        self.seed_resolver = seed_resolver or secrets.randbelow

    def compile(
        self,
        *,
        contract: Mapping[str, Any],
        api_document: Mapping[str, Any],
        object_info: Mapping[str, Any],
        requested_controls: Mapping[str, Any],
        preset_id: str | None = None,
        requested_outputs: Sequence[str] | None = None,
    ) -> CompileResult:
        controls = contract.get("controls", [])
        if not isinstance(controls, list):
            raise ContractError("contract_invalid", "Resolved contract controls are invalid.")
        control_map = {str(control["id"]): control for control in controls if isinstance(control, dict)}
        unknown = set(requested_controls) - set(control_map)
        operator_controls = {
            control_id
            for control_id, control in control_map.items()
            if control.get("tier") == "operator"
        }
        restricted = set(requested_controls) & operator_controls
        if unknown or restricted:
            fields = {key: "Unknown control." for key in sorted(unknown)}
            fields.update(
                {key: "This operator-only control cannot be supplied by a product user." for key in sorted(restricted)}
            )
            raise AppError(
                "control_validation_failed",
                "The request contains controls that are not available to this user.",
                status_code=422,
                fields=fields,
            )

        merged: dict[str, Any] = {}
        for control_id, control in control_map.items():
            if "default" in control:
                merged[control_id] = copy.deepcopy(control["default"])
        if preset_id:
            preset = next(
                (
                    item
                    for item in contract.get("presets", [])
                    if isinstance(item, Mapping) and item.get("id") == preset_id
                ),
                None,
            )
            if preset is None:
                raise AppError(
                    "control_validation_failed",
                    "The selected preset is not part of this workflow version.",
                    status_code=422,
                    fields={"preset_id": "Unknown preset."},
                )
            values = preset.get("values", {})
            if not isinstance(values, Mapping) or set(values) - set(control_map):
                raise ContractError("contract_invalid", "Preset contains unknown controls.")
            merged.update(copy.deepcopy(dict(values)))
        merged.update(copy.deepcopy(dict(requested_controls)))

        errors: dict[str, str] = {}
        capability_states = contract.get("capability_states", {})
        resolved_seeds: dict[str, int] = {}
        effective: dict[str, Any] = {}
        selected_uploads: dict[str, str] = {}
        for control_id, control in control_map.items():
            if control.get("available") is False:
                supplied = control_id in requested_controls
                if supplied and requested_controls[control_id] != control.get("default"):
                    errors[control_id] = control.get("unavailable_reason") or "This control is unavailable."
                continue
            capability = control.get("capability")
            if capability:
                state = capability_states.get(capability, {}) if isinstance(capability_states, Mapping) else {}
                if not state.get("available", False):
                    supplied = control_id in requested_controls
                    if supplied and requested_controls[control_id] != control.get("default"):
                        errors[control_id] = state.get("reason") or "Capability is unavailable."
                    continue
            value = merged.get(control_id, _MISSING)
            required = bool(control.get("required", False))
            conditional_effects = _condition_effects(control, merged)
            if "forbidden" in conditional_effects and value not in (_MISSING, None, "", []):
                errors[control_id] = "This control is not allowed with the current settings."
                continue
            if "required" in conditional_effects:
                required = True
            if value is _MISSING:
                if required:
                    errors[control_id] = "This control is required."
                continue
            try:
                normalized = _validate_value(control, value)
                if control.get("type") == "seed":
                    normalized = self._resolve_seed(control, normalized)
                    resolved_seeds[control_id] = normalized
                effective[control_id] = normalized
                if control.get("type") in {"image_upload", "mask_upload"} and normalized:
                    selected_uploads[control_id] = normalized
            except AppError as exc:
                errors[control_id] = exc.message

        for control_id, control in control_map.items():
            if control_id not in effective:
                continue
            value = effective[control_id]
            if not _is_active_value(value):
                continue
            for conflict in control.get("conflicts_with", []):
                if _is_active_value(effective.get(conflict)):
                    errors[control_id] = f"Conflicts with {conflict}."
            for requirement in control.get("requires", []):
                target = requirement if isinstance(requirement, str) else requirement.get("control")
                if not _is_active_value(effective.get(target)):
                    errors[control_id] = f"Requires {target}."
        if errors:
            raise AppError(
                "control_validation_failed",
                "Some workflow controls are invalid.",
                status_code=422,
                fields=errors,
            )

        graph = unwrap_api_document(api_document)
        variant_graphs = api_variants(api_document)
        branch_choices: dict[str, Any] = {}
        for control_id, control in control_map.items():
            if control_id not in effective:
                continue
            value = effective[control_id]
            for binding in control.get("bindings", []):
                strategy = binding.get("strategy")
                if strategy in {"select_branch", "select_variant"}:
                    branch_choices[str(binding["branch_id"])] = value
        self._reject_unavailable_branches(contract.get("branches", []), branch_choices)
        graph = self._apply_precompiled_variants(
            graph, variant_graphs, contract.get("branches", []), branch_choices
        )
        graph_index = NodeIndex(graph)

        for control_id, control in control_map.items():
            if control_id not in effective:
                continue
            value = effective[control_id]
            for binding in control.get("bindings", []):
                strategy = binding.get("strategy")
                if strategy in {"select_branch", "select_variant"}:
                    continue
                if strategy in {"patch_input", "patch_widget"}:
                    self._patch_input(graph_index, binding, value, control_id)
                elif strategy == "derive":
                    self._apply_derive(graph_index, binding, value, control_id)
                elif strategy == "upload_then_patch":
                    upload_id = value
                    if upload_id in {None, ""} and not control.get("required", False):
                        continue
                    if not isinstance(upload_id, str):
                        raise AppError(
                            "control_validation_failed",
                            "Upload controls require an application upload ID.",
                            status_code=422,
                            fields={control_id: "Upload is invalid."},
                        )
                    marker = {
                        "__app_upload_id__": upload_id,
                        "kind": binding.get("upload_kind", control.get("type")),
                    }
                    self._patch_input(graph_index, binding, marker, control_id)
                elif strategy == "fixed":
                    expected = binding.get("value", control.get("default"))
                    if value != expected:
                        raise AppError(
                            "control_validation_failed",
                            "A fixed workflow setting was changed.",
                            status_code=422,
                            fields={control_id: "This value is fixed by the workflow."},
                        )
                elif strategy == "request_policy":
                    continue
                else:
                    raise ContractError(
                        "branch_compilation_failed",
                        f"Unsupported compiler binding strategy {strategy!r}.",
                    )
        self._apply_graph_transforms(graph, contract.get("branches", []), branch_choices)
        self._validate_compiled_graph(graph, object_info)

        output_ids = {
            str(item["id"])
            for item in contract.get("outputs", [])
            if isinstance(item, Mapping) and "id" in item
        }
        if requested_outputs:
            unknown_outputs = set(requested_outputs) - output_ids
            if unknown_outputs:
                raise AppError(
                    "control_validation_failed",
                    "The request asks for undeclared workflow outputs.",
                    status_code=422,
                    fields={"requested_outputs": "Unknown output selection."},
                )
            selected_outputs = list(dict.fromkeys(requested_outputs))
        else:
            selected_outputs = [
                str(item["id"])
                for item in contract.get("outputs", [])
                if isinstance(item, Mapping) and item.get("kind") in {"image", "text"}
            ]
        final_prompt = effective.get("prompt.text")
        if not isinstance(final_prompt, str):
            raise ContractError("contract_invalid", "prompt.text did not resolve to a string.")
        return CompileResult(
            requested_controls=copy.deepcopy(dict(requested_controls)),
            effective_controls=effective,
            resolved_seeds=resolved_seeds,
            compiled_graph=graph,
            compiled_graph_hash=sha256_json(graph),
            selected_uploads=selected_uploads,
            requested_outputs=selected_outputs,
            final_prompt=final_prompt,
            selected_preset=preset_id,
        )

    def _resolve_seed(self, control: Mapping[str, Any], value: Any) -> int:
        constraints = control.get("constraints", {})
        minimum = int(constraints.get("minimum", 0)) if isinstance(constraints, Mapping) else 0
        maximum = (
            int(constraints.get("maximum", 2**63 - 1))
            if isinstance(constraints, Mapping)
            else 2**63 - 1
        )
        random_requested = value == "random" or (
            isinstance(value, Mapping) and value.get("mode") == "random"
        )
        if random_requested:
            span = maximum - minimum + 1
            if span <= 0:
                raise ContractError("contract_invalid", "Seed constraints are invalid.")
            # `secrets.randbelow` accepts one argument; injected test resolvers can accept two.
            try:
                return int(self.seed_resolver(minimum, maximum))
            except TypeError:
                return minimum + int(self.seed_resolver(span))  # type: ignore[call-arg]
        if isinstance(value, bool) or not isinstance(value, int):
            raise AppError("control_validation_failed", "Seed must be an integer or random.")
        if not minimum <= value <= maximum:
            raise AppError(
                "control_validation_failed", f"Seed must be between {minimum} and {maximum}."
            )
        return value

    def _reject_unavailable_branches(
        self, branches: Any, branch_choices: Mapping[str, Any]
    ) -> None:
        if not isinstance(branches, list):
            return
        reasons = {
            "separate_workflow": "This branch requires a separately published workflow profile.",
            "interaction_required": "This branch requires an interactive workflow and cannot run as a one-shot request.",
            "unsupported": "This workflow explicitly marks the branch as unsupported.",
        }
        for branch in branches:
            if not isinstance(branch, Mapping):
                continue
            strategy = branch.get("strategy")
            reason = reasons.get(str(strategy))
            if reason is None:
                continue
            branch_id = str(branch.get("id"))
            value = branch_choices.get(branch_id, branch.get("default_enabled", False))
            if _is_active_value(value):
                raise AppError(
                    "branch_compilation_failed",
                    f"Branch {branch_id} cannot be compiled for this request. {reason}",
                    status_code=422,
                )

    def _apply_precompiled_variants(
        self,
        graph: dict[str, Any],
        variants: Mapping[str, dict[str, Any]],
        branches: Any,
        branch_choices: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(branches, list):
            return graph
        selected: str | None = None
        for branch in branches:
            if not isinstance(branch, Mapping) or branch.get("strategy") != "precompiled_variant":
                continue
            branch_id = str(branch.get("id"))
            value = branch_choices.get(branch_id, branch.get("default_enabled"))
            variant_map = branch.get("variants", {})
            if not isinstance(variant_map, Mapping):
                raise ContractError("branch_compilation_failed", "Variant map is invalid.")
            key_candidates = [str(value).lower(), str(value)]
            variant_name = next((variant_map[key] for key in key_candidates if key in variant_map), None)
            if variant_name is None:
                raise AppError(
                    "branch_compilation_failed",
                    f"No approved graph variant exists for branch {branch_id}.",
                    status_code=422,
                )
            if selected is not None and selected != str(variant_name):
                raise ContractError(
                    "branch_compilation_failed",
                    "Multiple precompiled branches selected incompatible whole-graph variants.",
                )
            selected = str(variant_name)
        if selected is None:
            return graph
        if selected not in variants:
            raise ContractError(
                "branch_compilation_failed", f"Approved API variant {selected!r} is unavailable."
            )
        return copy.deepcopy(variants[selected])

    def _patch_input(
        self, graph_index: NodeIndex, binding: Mapping[str, Any], value: Any, control_id: str
    ) -> None:
        selector = binding.get("selector")
        if not isinstance(selector, Mapping):
            raise ContractError("binding_not_found", f"Control {control_id} selector is invalid.")
        _, node = graph_index.resolve(selector, context=f"compile control {control_id}")
        input_name = binding.get("input") or binding.get("widget_name")
        if not isinstance(input_name, str):
            raise ContractError("binding_not_found", f"Control {control_id} input is invalid.")
        transformed = _apply_transform(value, binding.get("transform"))
        inputs = node.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            raise ContractError("binding_not_found", f"Control {control_id} target inputs are invalid.")
        inputs[input_name] = transformed

    def _apply_derive(
        self, graph_index: NodeIndex, binding: Mapping[str, Any], value: Any, control_id: str
    ) -> None:
        targets = binding.get("targets", [])
        for target in targets:
            if not isinstance(target, Mapping):
                raise ContractError("binding_not_found", f"Control {control_id} derive target is invalid.")
            component = target.get("component")
            target_value = value
            if component is not None:
                if not isinstance(value, Mapping) or component not in value:
                    raise AppError(
                        "control_validation_failed",
                        f"Control {control_id} is missing derived component {component}.",
                    )
                target_value = value[component]
            target_value = _apply_transform(target_value, target.get("transform"))
            self._patch_input(graph_index, target, target_value, control_id)

    def _apply_graph_transforms(
        self, graph: dict[str, Any], branches: Any, branch_choices: Mapping[str, Any]
    ) -> None:
        if not isinstance(branches, list):
            return
        for branch in branches:
            if not isinstance(branch, Mapping) or branch.get("strategy") != "graph_transform":
                continue
            branch_id = str(branch.get("id"))
            enabled = bool(branch_choices.get(branch_id, branch.get("default_enabled", False)))
            transforms = branch.get("transforms", {})
            if not isinstance(transforms, Mapping):
                raise ContractError("branch_compilation_failed", f"Branch {branch_id} transforms invalid.")
            operations = transforms.get("enable" if enabled else "disable", [])
            if not isinstance(operations, list):
                raise ContractError("branch_compilation_failed", f"Branch {branch_id} operations invalid.")
            for operation in operations:
                self._apply_graph_operation(graph, operation, branch_id)

    def _apply_graph_operation(
        self, graph: dict[str, Any], operation: Any, branch_id: str
    ) -> None:
        if not isinstance(operation, Mapping):
            raise ContractError("branch_compilation_failed", f"Branch {branch_id} operation invalid.")
        op = operation.get("op")
        selector = operation.get("selector")
        if op == "remove_node":
            node_id, _ = NodeIndex(graph).resolve(selector, context=f"branch {branch_id}")
            graph.pop(node_id, None)
            return
        if not isinstance(selector, Mapping):
            raise ContractError("branch_compilation_failed", f"Branch {branch_id} selector missing.")
        _, node = NodeIndex(graph).resolve(selector, context=f"branch {branch_id}")
        if op in {"set_input", "rewire_input"}:
            input_name = operation.get("input")
            if not isinstance(input_name, str):
                raise ContractError("branch_compilation_failed", "Graph operation input is invalid.")
            inputs = node.setdefault("inputs", {})
            inputs[input_name] = copy.deepcopy(operation.get("value"))
        elif op == "set_class_type":
            class_type = operation.get("class_type")
            if not isinstance(class_type, str):
                raise ContractError("branch_compilation_failed", "Graph operation class is invalid.")
            node["class_type"] = class_type
        elif op in {"set_meta", "set_mode"}:
            meta = node.setdefault("_meta", {})
            if op == "set_mode":
                meta["mode"] = operation.get("value")
            else:
                key = operation.get("key")
                if not isinstance(key, str):
                    raise ContractError("branch_compilation_failed", "Graph metadata key invalid.")
                meta[key] = operation.get("value")
        else:
            raise ContractError(
                "branch_compilation_failed", f"Unsupported graph transform operation {op!r}."
            )

    def _validate_compiled_graph(
        self, graph: Mapping[str, Any], object_info: Mapping[str, Any]
    ) -> None:
        validate_runtime_graph(
            graph,
            object_info,
            context="Compiled graph",
            structural_error_code="branch_compilation_failed",
        )


_MISSING = object()


def _condition_effects(control: Mapping[str, Any], values: Mapping[str, Any]) -> set[str]:
    effects: set[str] = set()
    for condition in control.get("conditions", []):
        if not isinstance(condition, Mapping):
            continue
        when = condition.get("when", {})
        if isinstance(when, Mapping) and _evaluate_condition(when, values):
            effect = condition.get("effect")
            if isinstance(effect, str):
                effects.add(effect)
    return effects


def _evaluate_condition(when: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    current = values.get(str(when.get("control")))
    expected = when.get("value")
    operator = when.get("operator", "equals")
    if operator in {"equals", "eq"}:
        return current == expected
    if operator in {"not_equals", "ne"}:
        return current != expected
    if operator == "in":
        return isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)) and current in expected
    if operator == "not_in":
        return isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)) and current not in expected
    if operator == "truthy":
        return bool(current)
    if operator == "falsy":
        return not bool(current)
    if operator == "gt":
        return current is not None and current > expected
    if operator == "gte":
        return current is not None and current >= expected
    if operator == "lt":
        return current is not None and current < expected
    if operator == "lte":
        return current is not None and current <= expected
    return False


def _validate_value(control: Mapping[str, Any], value: Any) -> Any:
    control_type = control.get("type")
    constraints = control.get("constraints", {})
    if not isinstance(constraints, Mapping):
        constraints = {}
    if value is None:
        if control.get("required", False):
            raise AppError("control_validation_failed", "This value is required.")
        return None
    if control_type in {"string", "multiline_string"}:
        if not isinstance(value, str):
            raise AppError("control_validation_failed", "Must be text.")
        minimum = int(constraints.get("minimum_length", 0))
        maximum = int(constraints.get("maximum_length", 100_000))
        if not minimum <= len(value) <= maximum:
            raise AppError(
                "control_validation_failed", f"Text length must be between {minimum} and {maximum}."
            )
        pattern = constraints.get("pattern")
        if pattern and not re.fullmatch(str(pattern), value):
            raise AppError("control_validation_failed", "Text does not match the required format.")
        return value
    if control_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise AppError("control_validation_failed", "Must be a whole number.")
        _validate_number_constraints(value, constraints)
        return value
    if control_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise AppError("control_validation_failed", "Must be a finite number.")
        _validate_number_constraints(value, constraints)
        return value
    if control_type == "boolean":
        if not isinstance(value, bool):
            raise AppError("control_validation_failed", "Must be on or off.")
        return value
    if control_type in {"enum", "asset_selector"}:
        options = control.get("options", {})
        allowed = []
        if isinstance(options, Mapping):
            allowed = options.get("resolved_values", options.get("values", []))
        normalized_allowed = [
            item.get("value") if isinstance(item, Mapping) else item for item in allowed
        ]
        if value not in normalized_allowed:
            raise AppError("control_validation_failed", "Select an available option.")
        return value
    if control_type == "seed":
        if value == "random" or (isinstance(value, Mapping) and value.get("mode") == "random"):
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise AppError("control_validation_failed", "Seed must be an integer or random.")
        _validate_number_constraints(value, constraints)
        return value
    if control_type in {"image_upload", "mask_upload"}:
        if value in {"", None} and not control.get("required", False):
            return None
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", value):
            raise AppError("control_validation_failed", "Select a valid uploaded file.")
        return value
    if control_type == "resolution":
        if not isinstance(value, Mapping):
            raise AppError("control_validation_failed", "Resolution requires width and height.")
        width, height = value.get("width"), value.get("height")
        if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
            raise AppError("control_validation_failed", "Resolution dimensions must be whole numbers.")
        width_constraints = _resolution_axis_constraints(constraints, "width")
        height_constraints = _resolution_axis_constraints(constraints, "height")
        _validate_number_constraints(width, width_constraints)
        _validate_number_constraints(height, height_constraints)
        multiple = constraints.get("multiple")
        if multiple is not None:
            multiple_value = int(multiple)
            if multiple_value < 1 or width % multiple_value or height % multiple_value:
                raise AppError(
                    "control_validation_failed",
                    f"Resolution dimensions must be multiples of {multiple_value}.",
                )
        max_pixels = constraints.get("maximum_pixels")
        if max_pixels and width * height > int(max_pixels):
            raise AppError("resource_limit_exceeded", "Resolution exceeds the workflow pixel limit.")
        return {"width": width, "height": height}
    if control_type == "array" or control_type == "output_role_set":
        if not isinstance(value, list):
            raise AppError("control_validation_failed", "Must be a list.")
        minimum = int(constraints.get("minimum_items", 0))
        maximum = int(constraints.get("maximum_items", 10_000))
        if not minimum <= len(value) <= maximum:
            raise AppError("control_validation_failed", "List length is outside the allowed range.")
        return copy.deepcopy(value)
    if control_type == "object":
        if not isinstance(value, Mapping):
            raise AppError("control_validation_failed", "Must be an object.")
        return copy.deepcopy(dict(value))
    raise AppError("control_validation_failed", "Unsupported control value.")


def _resolution_axis_constraints(
    constraints: Mapping[str, Any], axis: str
) -> dict[str, Any]:
    nested = constraints.get(axis)
    result = dict(nested) if isinstance(nested, Mapping) else {}
    for generic, specialized in (
        ("minimum", f"minimum_{axis}"),
        ("maximum", f"maximum_{axis}"),
        ("step", f"{axis}_step"),
    ):
        if specialized in constraints:
            result[generic] = constraints[specialized]
        elif generic in constraints and generic not in result:
            result[generic] = constraints[generic]
    return result


def _validate_number_constraints(value: int | float, constraints: Mapping[str, Any]) -> None:
    minimum = constraints.get("minimum")
    maximum = constraints.get("maximum")
    if minimum is not None and value < minimum:
        raise AppError("control_validation_failed", f"Must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise AppError("control_validation_failed", f"Must be at most {maximum}.")
    step = constraints.get("step")
    if step and minimum is not None:
        quotient = (Decimal(str(value)) - Decimal(str(minimum))) / Decimal(str(step))
        if quotient != quotient.to_integral_value():
            raise AppError("control_validation_failed", f"Must use increments of {step}.")


def _apply_transform(value: Any, transform: Any) -> Any:
    if transform is None:
        return copy.deepcopy(value)
    if not isinstance(transform, Mapping):
        raise ContractError("contract_invalid", "Binding transform must be an object.")
    name = transform.get("name")
    arguments = transform.get("arguments", {})
    if not isinstance(arguments, Mapping):
        raise ContractError("contract_invalid", "Transform arguments must be an object.")
    if name == "snap_to_multiple":
        multiple = int(arguments.get("multiple", 1))
        mode = arguments.get("mode", "nearest")
        numeric = float(value)
        if mode == "floor":
            result = math.floor(numeric / multiple) * multiple
        elif mode == "ceil":
            result = math.ceil(numeric / multiple) * multiple
        else:
            result = round(numeric / multiple) * multiple
        return int(result) if isinstance(value, int) else result
    if name == "clamp":
        return max(arguments.get("minimum", value), min(arguments.get("maximum", value), value))
    if name == "multiply":
        return value * arguments.get("factor", 1)
    if name == "add":
        return value + arguments.get("amount", 0)
    if name == "map":
        mapping = arguments.get("values", {})
        if not isinstance(mapping, Mapping) or str(value) not in mapping:
            raise AppError("control_validation_failed", "No transform mapping exists for this value.")
        return copy.deepcopy(mapping[str(value)])
    if name == "bool_to_int":
        return int(bool(value))
    raise ContractError("contract_invalid", f"Unsupported transform {name!r}.")


def _is_active_value(value: Any) -> bool:
    return value not in (None, False, "", [], {})
