"""Opaque LoRA identities shared by every workflow and historical image.

Public catalog item IDs are workflow data; only the frozen private filename proves
that two catalog entries apply the same weight. The v1 algorithm is also used by a
backfill migration: keep it stable.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

LORA_IDENTITY_PATTERN = r"^lr1_[0-9a-f]{64}$"


def lora_identity_v1(filename: Any) -> str | None:
    if not isinstance(filename, str) or not filename:
        return None
    normalized = posixpath.normpath(filename.replace("\\", "/"))
    encoded = json.dumps(["lora-binding-v1", normalized], separators=(",", ":")).encode()
    return "lr1_" + hashlib.sha256(encoded).hexdigest()


def stack_catalog(declaration: Any, graph: Any) -> list[dict[str, Any]] | None:
    """Return the frozen private catalog bound to one public lora_stack declaration."""

    if not isinstance(declaration, Mapping) or not isinstance(graph, Mapping):
        return None
    bindings = declaration.get("bindings")
    if not isinstance(bindings, list) or not bindings or not isinstance(bindings[0], Mapping):
        return None
    node = graph.get(str(bindings[0].get("node_id")))
    inputs = node.get("inputs") if isinstance(node, Mapping) else None
    raw = inputs.get("catalog_json") if isinstance(inputs, Mapping) else None
    if not isinstance(raw, str):
        return None
    try:
        catalog = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(catalog, list):
        return None
    return [item for item in catalog if isinstance(item, Mapping)]  # type: ignore[misc]


def item_identities(declaration: Any, graph: Any) -> dict[str, str]:
    """Map public item IDs of one stack to their shared LoRA identities."""

    result: dict[str, str] = {}
    for item in stack_catalog(declaration, graph) or []:
        item_id, identity = item.get("id"), lora_identity_v1(item.get("filename"))
        if isinstance(item_id, str) and identity:
            result[item_id] = identity
    return result


def contract_item_identities(contract: Any, graph: Any) -> dict[tuple[str, str], str]:
    """Map (control_id, item_id) for every lora_stack in a contract."""

    result: dict[tuple[str, str], str] = {}
    if not isinstance(contract, Mapping):
        return result
    for declaration in contract.get("inputs", []):
        if not isinstance(declaration, Mapping) or declaration.get("type") != "lora_stack":
            continue
        control_id = declaration.get("id")
        if not isinstance(control_id, str):
            continue
        for item_id, identity in item_identities(declaration, graph).items():
            result[(control_id, item_id)] = identity
    return result


@dataclass(frozen=True)
class LoraUsage:
    position: int
    lora_identity: str
    label: str
    strength: float


def generation_lora_usage_v1(contract: Any, controls: Any, graph: Any) -> list[LoraUsage]:
    """Return the enabled LoRAs of one accepted generation in application order."""

    if not isinstance(contract, Mapping) or not isinstance(controls, Mapping):
        return []
    usages: list[LoraUsage] = []
    seen: set[str] = set()
    for declaration in contract.get("inputs", []):
        if not isinstance(declaration, Mapping) or declaration.get("type") != "lora_stack":
            continue
        values = controls.get(declaration.get("id"))
        if not isinstance(values, list):
            continue
        identities = item_identities(declaration, graph)
        labels = {
            item.get("id"): item.get("label")
            for item in declaration.get("items", [])
            if isinstance(item, Mapping)
        }
        for entry in values:
            if not isinstance(entry, Mapping):
                continue
            strength = entry.get("strength")
            if isinstance(strength, bool) or not isinstance(strength, int | float):
                continue
            identity = identities.get(entry.get("id"))  # type: ignore[arg-type]
            if strength <= 0 or not identity or identity in seen:
                continue
            label = labels.get(entry.get("id"))
            seen.add(identity)
            usages.append(
                LoraUsage(
                    position=len(usages),
                    lora_identity=identity,
                    label=(label if isinstance(label, str) and label else "LoRA")[:120],
                    strength=float(strength),
                )
            )
    return usages
