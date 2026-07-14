from __future__ import annotations

import copy
import math
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from ..errors import AppError, ContractError
from .publication import canonical_json_bytes, sha256_json

CANONICAL_INTEGER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
MAX_PUBLIC_STRING_LENGTH = 100_000


@dataclass(frozen=True)
class CompileResult:
    requested_controls: dict[str, Any]
    effective_controls: dict[str, Any]
    resolved_seeds: dict[str, str]
    compiled_graph: dict[str, Any]
    compiled_graph_hash: str
    selected_uploads: dict[str, str]
    requested_outputs: list[str]
    final_prompt: str
    selected_preset: str | None


RandomSeed = Callable[[int, int], int]
_MISSING = object()


class WorkflowCompiler:
    """Compile one allowlisted publication request into a request-local ComfyUI graph."""

    def __init__(self, *, seed_resolver: RandomSeed | None = None):
        self.seed_resolver = seed_resolver or self._secure_seed

    @staticmethod
    def _secure_seed(minimum: int, maximum: int) -> int:
        return minimum + secrets.randbelow(maximum - minimum + 1)

    def compile(
        self,
        *,
        contract: Mapping[str, Any],
        api_document: Mapping[str, Any],
        requested_controls: Mapping[str, Any],
        object_info: Mapping[str, Any] | None = None,
        preset_id: str | None = None,
        requested_outputs: list[str] | None = None,
    ) -> CompileResult:
        del object_info
        if preset_id is not None or requested_outputs:
            raise AppError(
                "parameter_validation_failed",
                "Published generation sources do not accept presets or caller-selected outputs.",
                status_code=422,
                fields={
                    **(
                        {"preset_id": "Presets are not part of publication v1."}
                        if preset_id
                        else {}
                    ),
                    **(
                        {"requested_outputs": "Outputs are declared by the published source."}
                        if requested_outputs
                        else {}
                    ),
                },
            )
        raw_inputs = contract.get("inputs")
        if not isinstance(raw_inputs, list):
            raise ContractError("manifest_invalid", "Accepted source interface is unavailable.")
        inputs = {
            str(value["id"]): value
            for value in raw_inputs
            if isinstance(value, Mapping) and isinstance(value.get("id"), str)
        }
        if len(inputs) != len(raw_inputs):
            raise ContractError(
                "manifest_invalid", "Accepted source interface is internally invalid."
            )

        unknown = set(requested_controls) - set(inputs)
        if unknown:
            raise AppError(
                "parameter_validation_failed",
                "The request contains parameters that are not published by this source.",
                status_code=422,
                fields={key: "Unknown published parameter." for key in sorted(unknown)},
            )

        requested = copy.deepcopy(dict(requested_controls))
        effective: dict[str, Any] = {}
        resolved_seeds: dict[str, str] = {}
        errors: dict[str, str] = {}
        for input_id, declaration in inputs.items():
            supplied = input_id in requested_controls
            value = requested_controls.get(input_id, _MISSING)
            required = bool(declaration.get("required"))
            input_type = declaration.get("type")
            missing_value = value is _MISSING or value is None or value == ""
            if required and (not supplied or missing_value):
                errors[input_id] = "This published parameter is required."
                continue
            if input_type == "seed":
                try:
                    concrete = self._resolve_seed(declaration, value)
                except ValueError as exc:
                    errors[input_id] = str(exc)
                    continue
                effective[input_id] = str(concrete)
                resolved_seeds[input_id] = str(concrete)
                continue
            if value is _MISSING or value is None:
                value = copy.deepcopy(declaration.get("default", _MISSING))
            if value is _MISSING:
                if required:
                    errors[input_id] = "This published parameter is required."
                continue
            try:
                effective[input_id] = _validate_value(declaration, value)
            except ValueError as exc:
                errors[input_id] = str(exc)
        if errors:
            raise AppError(
                "parameter_validation_failed",
                "One or more published parameters are invalid.",
                status_code=422,
                fields=errors,
            )

        graph = copy.deepcopy(dict(api_document))
        before = canonical_json_bytes(api_document)
        for input_id, declaration in inputs.items():
            if input_id not in effective:
                continue
            value = effective[input_id]
            graph_value: Any = int(value) if declaration.get("type") == "seed" else value
            bindings = declaration.get("bindings")
            if not isinstance(bindings, list) or not bindings:
                raise ContractError(
                    "manifest_invalid", f"Parameter {input_id!r} has no trusted binding."
                )
            for binding in bindings:
                if not isinstance(binding, Mapping):
                    raise ContractError(
                        "manifest_invalid", f"Parameter {input_id!r} has an invalid binding."
                    )
                node_id = str(binding.get("node_id"))
                input_name = binding.get("input")
                node = graph.get(node_id)
                if not isinstance(node, dict) or not isinstance(input_name, str):
                    raise ContractError(
                        "manifest_invalid",
                        f"Parameter {input_id!r} binding is no longer resolvable.",
                    )
                node_inputs = node.get("inputs")
                if not isinstance(node_inputs, dict) or input_name not in node_inputs:
                    raise ContractError(
                        "manifest_invalid", f"Parameter {input_id!r} target input is unavailable."
                    )
                node_inputs[input_name] = copy.deepcopy(graph_value)

        if canonical_json_bytes(api_document) != before:
            raise RuntimeError("cached API graph was mutated during compilation")
        positive = next(
            (
                input_id
                for input_id, declaration in inputs.items()
                if declaration.get("semantic_role") == "positive_prompt"
            ),
            None,
        )
        if positive is None or not isinstance(effective.get(positive), str):
            raise ContractError(
                "manifest_invalid", "Accepted source has no effective positive prompt."
            )
        return CompileResult(
            requested_controls=requested,
            effective_controls=effective,
            resolved_seeds=resolved_seeds,
            compiled_graph=graph,
            compiled_graph_hash=sha256_json(graph),
            selected_uploads={},
            requested_outputs=[],
            final_prompt=str(effective[positive]),
            selected_preset=None,
        )

    def _resolve_seed(self, declaration: Mapping[str, Any], value: Any) -> int:
        minimum = declaration.get("minimum")
        maximum = declaration.get("maximum")
        step = declaration.get("step", 1)
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, int)
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or isinstance(step, bool)
            or not isinstance(step, int)
            or step <= 0
        ):
            raise ContractError("manifest_invalid", "Accepted seed contract is invalid.")
        random_requested = value is _MISSING or value is None or value == ""
        if isinstance(value, str) and value == "random":
            random_requested = True
        if random_requested:
            if declaration.get("default_mode") == "random":
                slot_count = (maximum - minimum) // step
                slot = self.seed_resolver(0, slot_count)
                if (
                    not isinstance(slot, int)
                    or isinstance(slot, bool)
                    or not 0 <= slot <= slot_count
                ):
                    raise RuntimeError("seed resolver returned a value outside its requested range")
                return int(minimum + slot * step)
            value = declaration.get("default", _MISSING)
        concrete = _parse_seed(value)
        _validate_numeric_constraints(concrete, minimum, maximum, step)
        return concrete


def _parse_seed(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Enter a whole-number seed or leave the field blank for random.")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str) and CANONICAL_INTEGER_RE.fullmatch(value):
        return int(value)
    raise ValueError("Enter a whole-number seed or leave the field blank for random.")


def _validate_value(declaration: Mapping[str, Any], value: Any) -> Any:
    input_type = declaration.get("type")
    if input_type == "string":
        if not isinstance(value, str):
            raise ValueError("Enter text.")
        if len(value) > MAX_PUBLIC_STRING_LENGTH:
            raise ValueError("Text is too long.")
        return value
    if input_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("Choose true or false.")
        return value
    if input_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Enter a whole number.")
        _validate_numeric_constraints(
            value,
            declaration.get("minimum"),
            declaration.get("maximum"),
            declaration.get("step"),
        )
        return value
    if input_type == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("Enter a finite number.")
        _validate_numeric_constraints(
            value,
            declaration.get("minimum"),
            declaration.get("maximum"),
            declaration.get("step"),
        )
        return value
    raise ContractError("manifest_invalid", f"Unsupported accepted input type {input_type!r}.")


def _validate_numeric_constraints(
    value: int | float, minimum: Any, maximum: Any, step: Any
) -> None:
    if not all(
        isinstance(candidate, (int, float)) and not isinstance(candidate, bool)
        for candidate in (minimum, maximum, step)
    ):
        raise ContractError("manifest_invalid", "Accepted numeric contract is invalid.")
    if value < minimum or value > maximum:
        raise ValueError(f"Enter a value from {minimum} to {maximum}.")
    try:
        remainder = (Decimal(str(value)) - Decimal(str(minimum))) % Decimal(str(step))
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise ContractError("manifest_invalid", "Accepted numeric step is invalid.") from exc
    if remainder != 0:
        raise ValueError(f"Use increments of {step} from {minimum}.")
