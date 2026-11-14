from __future__ import annotations

import math
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_TRIGGER_WORD = 1000


def _finite(value: Any) -> bool:
    """Exact type test: ``True`` is a valid ``int`` in Python but never a strength."""
    return type(value) in (int, float) and math.isfinite(value)


def _step_remainder(value: Any, minimum: Any, step: Any) -> Decimal:
    """How far ``value`` sits off the step grid that starts at ``minimum``.

    Zero means aligned. A published stack defaults every entry to zero, so a
    publishable ``minimum`` is always a whole multiple of ``step``: the zero default
    has to clear the same check. That invariant is what lets the browser measure the
    identical alignment from zero rather than from ``minimum``.
    """
    try:
        return (Decimal(str(value)) - Decimal(str(minimum))) % Decimal(str(step))
    except InvalidOperation as exc:
        raise ValueError("Invalid LoRA strength or step.") from exc


def validate_lora_stack(value: Any, declaration: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate a complete ordered permutation without coercion or mutation.

    Strengths are signed. ``0`` means the LoRA is not loaded at all, a positive value
    applies it, and a negative value subtracts its low-rank update, which is how one
    control can mean "less of this LoRA" — the dry direction of a wetness slider
    rather than the wet one. Because every published default is zero, ``0`` is the
    universal off state and always stays inside the published range.
    """
    items = declaration["items"]
    minimum, maximum, step = (declaration[key] for key in ("minimum", "maximum", "step"))
    if any(not _finite(n) for n in (minimum, maximum, step)):
        raise ValueError("LoRA constraints must be finite numbers.")
    if step <= 0 or not minimum <= 0 < maximum:
        raise ValueError("LoRA constraints must span zero with a positive maximum and step.")
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("Publish at most 100 LoRAs.")
    ids = set()
    for item in items:
        if (
            not isinstance(item, dict)
            or not {"id", "label"} <= set(item)
            or set(item) - {"id", "label", "description", "trigger_word", "preferred_strength"}
        ):
            raise ValueError(
                "LoRA items may contain only public IDs, labels, descriptions, "
                "trigger words, and preferred strengths."
            )
        public_id, label = item["id"], item["label"]
        if not isinstance(public_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", public_id):
            raise ValueError("Invalid public LoRA ID.")
        if public_id in ids or not isinstance(label, str) or not label.strip() or len(label) > 120:
            raise ValueError("LoRA IDs must be unique and labels must be nonempty, bounded text.")
        if "description" in item and (
            not isinstance(item["description"], str)
            or not item["description"].strip()
            or len(item["description"]) > 1000
        ):
            raise ValueError("LoRA usage descriptions must be nonempty text up to 1000 characters.")
        if "trigger_word" in item and (
            not isinstance(item["trigger_word"], str)
            or not item["trigger_word"].strip()
            or len(item["trigger_word"]) > MAX_TRIGGER_WORD
        ):
            raise ValueError(
                f"LoRA trigger words must be nonempty text up to {MAX_TRIGGER_WORD} characters."
            )
        # A preferred strength is only a hint about the direction the author intends
        # the LoRA to be applied in when a user enables it; it never changes what the
        # stack defaults to, so it still has to be a strength this stack accepts.
        if "preferred_strength" in item and (
            not _finite(item["preferred_strength"])
            or not minimum <= item["preferred_strength"] <= maximum
            or _step_remainder(item["preferred_strength"], minimum, step)
        ):
            raise ValueError(
                "Preferred LoRA strengths must be finite step-aligned values "
                f"from {minimum} to {maximum}."
            )
        ids.add(public_id)
    if not isinstance(value, list) or len(value) != len(items):
        raise ValueError("Include every published LoRA exactly once.")
    result = []
    seen = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"id", "strength"}:
            raise ValueError("Each LoRA must contain only id and strength.")
        public_id, strength = entry["id"], entry["strength"]
        if not isinstance(public_id, str) or public_id not in ids or public_id in seen:
            raise ValueError("Include every published LoRA exactly once; unknown or duplicate ID.")
        seen.add(public_id)
        if not _finite(strength):
            raise ValueError("LoRA strength must be a finite number.")
        if not minimum <= strength <= maximum:
            raise ValueError(f"LoRA strength must be from {minimum} to {maximum}.")
        if _step_remainder(strength, minimum, step):
            raise ValueError(f"LoRA strength must use increments of {step}.")
        result.append({"id": public_id, "strength": strength})
    return result


def validate_lora_runtime(api_document: Mapping[str, Any], object_info: Mapping[str, Any]) -> None:
    """Require the companion and full private catalog on the execution runtime."""
    import json

    stacks = [node for node in api_document.values() if node.get("class_type") == "CIFLoraStack"]
    if not stacks:
        return
    if "CIFLoraStack" not in object_info:
        raise ValueError("The assigned runtime needs the Image Frontend LoRA Stack node.")
    filenames = [
        item["filename"] for node in stacks for item in json.loads(node["inputs"]["catalog_json"])
    ]
    if not filenames:
        return
    spec = (
        object_info.get("LoraLoaderModelOnly", {})
        .get("input", {})
        .get("required", {})
        .get("lora_name", [])
    )
    if not spec or not isinstance(spec[0], list):
        raise ValueError(
            "The assigned runtime needs the Image Frontend LoRA Stack node "
            "and native model-only loader."
        )
    installed = set(spec[0])
    if any(filename not in installed for filename in filenames):
        raise ValueError(
            "The assigned runtime is missing files required by this workflow's LoRA catalog."
        )
