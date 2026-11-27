"""Per-checkpoint control defaults: one account's known-good values for one checkpoint.

A published bundle carries one default per control, and that default has to be right for
every checkpoint the bundle can be pointed at. Some checkpoints make a value visible in a
way others do not: the same re-draw amount that is invisible on a soft checkpoint renders
skin as faceted patches on an INT8 quant. An owner who already knows that a checkpoint
behaves differently should be able to record the value once instead of rediscovering it per
generation, so the account keeps a map of checkpoint option ID to control ID to value.

Two rules make the map safe to keep:

* It is keyed on the checkpoint **choice option ID**, never on a file identity. A bundle
  documents its option IDs as stable across checkpoint upgrades precisely so saved
  selections keep working, and a default that silently orphaned on every upgrade would be
  worse than no default at all. (Checkpoint grades learned that the hard way.)
* A default is a prefilled value, not a lock. Nothing here knows whether the person
  editing the panel wants it; the client applies it as a default and an explicit value for
  the generation always wins.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..errors import AppError

# Caps are deliberately small: this is a hand-maintained note about a handful of
# checkpoints, not a per-generation history.
CONTROL_DEFAULT_MAX_CHECKPOINTS = 200
CONTROL_DEFAULT_MAX_CONTROLS = 64
CONTROL_DEFAULT_MAX_VALUE_CHARS = 200

CHECKPOINT_OPTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
CONTROL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Types a default can express. A seed or an image has no meaningful "known good value"
# for a checkpoint, and a LoRA stack is a list of weights, not a value.
DEFAULTABLE_TYPES = frozenset({"number", "integer", "boolean", "choice"})


def control_declarations(
    contracts: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    """Index every published control declaration by public control ID.

    The same control ID can be published by several bundles with different bounds, so the
    index keeps every declaration and the caller decides against all of them.
    """

    index: dict[str, list[Mapping[str, Any]]] = {}
    for contract in contracts:
        inputs = contract.get("inputs") if isinstance(contract, Mapping) else None
        if not isinstance(inputs, list):
            continue
        for declaration in inputs:
            if not isinstance(declaration, Mapping):
                continue
            control_id = declaration.get("id")
            if isinstance(control_id, str) and CONTROL_ID_RE.fullmatch(control_id):
                index.setdefault(control_id, []).append(declaration)
    return index


def _number(value: Any) -> float | None:
    """The value as a finite float, or ``None`` when it is not a number at all.

    A published contract is JSON, so every bound arrives as ``Any``; narrowing by return
    value keeps the callers free of ``cast`` and answers "not a number" and "not a finite
    one" with the same value.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _number_bounds(declaration: Mapping[str, Any]) -> tuple[float, float, float] | None:
    minimum = _number(declaration.get("minimum"))
    maximum = _number(declaration.get("maximum"))
    step = _number(declaration.get("step"))
    if minimum is None or maximum is None or step is None:
        return None
    if step <= 0 or minimum > maximum:
        return None
    return minimum, maximum, step


def _aligned(value: float, minimum: float, step: float) -> float:
    # Snap to the declared step grid, measured from the minimum, then let the caller
    # clamp. Rounding half away from zero keeps a 0.5 on a 0.1 grid at 0.5 rather than
    # drifting down to 0.4 every time a value is checked.
    steps = (value - minimum) / step
    rounded = math.floor(steps + 0.5) if steps >= 0 else math.ceil(steps - 0.5)
    return minimum + rounded * step


def _accepts_number(declaration: Mapping[str, Any], value: float) -> bool:
    bounds = _number_bounds(declaration)
    if bounds is None:
        return False
    minimum, maximum, step = bounds
    if not minimum <= value <= maximum:
        return False
    return math.isclose(_aligned(value, minimum, step), value, rel_tol=0, abs_tol=1e-9)


def _clamp_number(value: float, bounds: tuple[float, float, float]) -> float:
    minimum, maximum, step = bounds
    clamped = min(max(value, minimum), maximum)
    snapped = _aligned(clamped, minimum, step)
    snapped = min(max(snapped, minimum), maximum)
    # A float grid is a display contract, not a binary one: round back to the step's own
    # precision so a stored default never reads as 0.30000000000000004.
    decimals = max(0, -_decimal_exponent(step))
    return round(snapped, decimals)


def _decimal_exponent(step: float) -> int:
    text = f"{step:.10g}"
    if "." not in text and "e" not in text:
        return 0
    fraction = text.split(".", 1)[1] if "." in text else ""
    if "e" in fraction:
        fraction = fraction.split("e", 1)[0]
    return -len(fraction)


def _choice_values(declaration: Mapping[str, Any]) -> list[Any] | None:
    choices = declaration.get("choices")
    if not isinstance(choices, list):
        return None
    return [choice.get("value") for choice in choices if isinstance(choice, Mapping)]


def validate_control_defaults(
    value: Mapping[str, Any],
    declarations: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Validate one account map against the published catalog, clamping numbers.

    A control ID the catalog does not publish is rejected, because a default nobody can
    see is how a stale value survives forever. A number outside the published bounds is
    clamped to them rather than rejected: the owner's intent is "as low as it goes", and
    the client clamps again against the specific bundle it is rendering.
    """

    if len(value) > CONTROL_DEFAULT_MAX_CHECKPOINTS:
        raise AppError(
            "control_defaults_too_large",
            f"Control defaults may name at most {CONTROL_DEFAULT_MAX_CHECKPOINTS} checkpoints.",
            status_code=422,
        )
    result: dict[str, dict[str, Any]] = {}
    # With nothing published there is no contract to judge a control ID against, and an
    # operator debugging a runtime that is offline right now must still be able to keep the
    # values they already know. The schema has already bounded the shape.
    judgeable = bool(declarations)
    for raw_checkpoint, raw_controls in value.items():
        checkpoint = str(raw_checkpoint)
        if not CHECKPOINT_OPTION_RE.fullmatch(checkpoint):
            raise AppError(
                "control_defaults_invalid",
                "Control defaults must be keyed on a published checkpoint option ID.",
                status_code=422,
                fields={f"control_defaults.{checkpoint}": "unknown checkpoint option ID"},
            )
        if not isinstance(raw_controls, Mapping):
            raise AppError(
                "control_defaults_invalid",
                "Each checkpoint default must be an object of control IDs to values.",
                status_code=422,
                fields={f"control_defaults.{checkpoint}": "must be an object"},
            )
        if len(raw_controls) > CONTROL_DEFAULT_MAX_CONTROLS:
            raise AppError(
                "control_defaults_invalid",
                f"A checkpoint may carry at most {CONTROL_DEFAULT_MAX_CONTROLS} control defaults.",
                status_code=422,
                fields={f"control_defaults.{checkpoint}": "too many controls"},
            )
        controls: dict[str, Any] = {}
        for raw_control, raw_value in raw_controls.items():
            control_id = str(raw_control)
            field = f"control_defaults.{checkpoint}.{control_id}"
            if not CONTROL_ID_RE.fullmatch(control_id):
                raise AppError(
                    "control_defaults_invalid",
                    "Control defaults must be keyed on a published control ID.",
                    status_code=422,
                    fields={field: "unknown control ID"},
                )
            candidates = declarations.get(control_id) if judgeable else None
            if not judgeable:
                controls[control_id] = raw_value
                continue
            if not candidates:
                raise AppError(
                    "control_defaults_unknown_control",
                    f"No published workflow declares the control {control_id!r}, so a default "
                    f"for it could never be applied.",
                    status_code=422,
                    fields={field: "unknown control ID"},
                )
            if all(candidate.get("type") not in DEFAULTABLE_TYPES for candidate in candidates):
                raise AppError(
                    "control_defaults_unknown_control",
                    f"The control {control_id!r} is not a number, integer, boolean or choice, "
                    f"so it cannot carry a checkpoint default.",
                    status_code=422,
                    fields={field: "control type cannot carry a default"},
                )
            controls[control_id] = _resolve_value(control_id, candidates, raw_value, field)
        result[checkpoint] = controls
    return result


def _resolve_value(
    control_id: str,
    candidates: list[Mapping[str, Any]],
    raw_value: Any,
    field: str,
) -> Any:
    numeric: list[tuple[Mapping[str, Any], tuple[float, float, float]]] = []
    for candidate in candidates:
        if candidate.get("type") not in {"number", "integer"}:
            continue
        bounds = _number_bounds(candidate)
        if bounds is not None:
            numeric.append((candidate, bounds))
    if numeric:
        raw_number = _number(raw_value)
        if raw_number is None:
            raise AppError(
                "control_defaults_invalid",
                f"The default for {control_id!r} must be a number.",
                status_code=422,
                fields={field: "must be a number"},
            )
        # Identical bounds across bundles are the normal case, so a value that any one
        # published contract accepts is stored exactly as the owner wrote it.
        if any(_accepts_number(candidate, raw_number) for candidate, _ in numeric):
            return raw_value
        # Otherwise clamp against the widest published range and snap to that contract's
        # grid: the owner asked for a value at least as extreme as this one.
        widest = max(numeric, key=lambda item: (item[1][1] - item[1][0], item[1][0]))
        return _clamp_number(raw_number, widest[1])
    boolean = [candidate for candidate in candidates if candidate.get("type") == "boolean"]
    if isinstance(raw_value, bool):
        if not boolean:
            raise AppError(
                "control_defaults_invalid",
                f"The default for {control_id!r} is not a number, so it cannot be true or false.",
                status_code=422,
                fields={field: "not a boolean control"},
            )
        return raw_value
    options: list[list[Any]] = []
    for candidate in candidates:
        if candidate.get("type") != "choice":
            continue
        values = _choice_values(candidate)
        if values is not None:
            options.append(values)
    if options:
        if not isinstance(raw_value, str) or not any(raw_value in values for values in options):
            allowed = ", ".join(sorted({str(item) for values in options for item in values})[:12])
            raise AppError(
                "control_defaults_invalid",
                f"The default for {control_id!r} must be one of the published choices ({allowed}).",
                status_code=422,
                fields={field: "unknown choice"},
            )
        return raw_value
    raise AppError(
        "control_defaults_invalid",
        f"The control {control_id!r} does not publish a value this default can express.",
        status_code=422,
        fields={field: "unsupported control"},
    )
