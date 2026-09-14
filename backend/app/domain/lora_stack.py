from __future__ import annotations

import math
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any


def validate_lora_stack(value: Any, declaration: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate a complete ordered permutation without coercion or mutation."""
    items = declaration["items"]
    minimum, maximum, step = (declaration[key] for key in ("minimum", "maximum", "step"))
    if any(type(n) not in (int, float) or not math.isfinite(n) for n in (minimum, maximum, step)):
        raise ValueError("LoRA constraints must be finite numbers.")
    if minimum != 0 or maximum <= minimum or step <= 0:
        raise ValueError("LoRA constraints must start at zero with positive maximum and step.")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("Publish between 1 and 100 LoRAs.")
    ids = set()
    for item in items:
        if (
            not isinstance(item, dict)
            or not {"id", "label"} <= set(item)
            or set(item) - {"id", "label", "description"}
        ):
            raise ValueError(
                "LoRA items may contain only public IDs, labels, and usage descriptions."
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
        if type(strength) not in (int, float) or not math.isfinite(strength):
            raise ValueError("LoRA strength must be a finite number.")
        if not minimum <= strength <= maximum:
            raise ValueError(f"LoRA strength must be from {minimum} to {maximum}.")
        try:
            remainder = (Decimal(str(strength)) - Decimal(str(minimum))) % Decimal(str(step))
        except InvalidOperation as exc:
            raise ValueError("Invalid LoRA strength or step.") from exc
        if remainder:
            raise ValueError(f"LoRA strength must use increments of {step}.")
        result.append({"id": public_id, "strength": strength})
    return result


def validate_lora_runtime(api_document: Mapping[str, Any], object_info: Mapping[str, Any]) -> None:
    """Require the companion and full private catalog on the execution runtime."""
    import json

    stacks = [node for node in api_document.values() if node.get("class_type") == "CIFLoraStack"]
    if not stacks:
        return
    spec = (
        object_info.get("LoraLoaderModelOnly", {})
        .get("input", {})
        .get("required", {})
        .get("lora_name", [])
    )
    if "CIFLoraStack" not in object_info or not spec or not isinstance(spec[0], list):
        raise ValueError(
            "The selected runtime needs the Image Frontend LoRA Stack node "
            "and native model-only loader."
        )
    installed = set(spec[0])
    if any(
        item["filename"] not in installed
        for node in stacks
        for item in json.loads(node["inputs"]["catalog_json"])
    ):
        raise ValueError(
            "The selected runtime is missing files required by this workflow's LoRA catalog."
        )
