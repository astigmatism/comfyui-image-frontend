"""Opaque checkpoint identities shared by workflows and historical images.

The v1 algorithm is also used by the backfill migration: keep it stable. Public
choice IDs and labels are workflow-local; only a resolved private binding proves
that two choices use the same checkpoint. Unresolved choices remain source-local.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
from collections.abc import Mapping
from typing import Any


def checkpoint_identity_v1(declaration: Any, value: Any, graph: Any, source_key: str) -> str | None:
    if not isinstance(declaration, Mapping) or not isinstance(value, str) or not value:
        return None
    bindings = declaration.get("bindings", [])
    resolved: list[str] = []
    for binding in bindings if isinstance(bindings, list) else []:
        node = (
            graph.get(str(binding.get("node_id")), {})
            if isinstance(graph, Mapping) and isinstance(binding, Mapping)
            else {}
        )
        inputs = node.get("inputs", {}) if isinstance(node, Mapping) else {}
        raw = inputs.get("options_json") if isinstance(inputs, Mapping) else None
        try:
            options = json.loads(raw) if isinstance(raw, str) else []
        except (ValueError, TypeError):
            options = []
        matches = (
            [
                option.get("binding")
                for option in options
                if isinstance(option, Mapping) and option.get("id", option.get("value")) == value
            ]
            if isinstance(options, list)
            else []
        )
        if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0]:
            resolved = []
            break
        resolved.append(posixpath.normpath(matches[0].replace("\\", "/")))
    if resolved:
        identity = ["checkpoint-binding-v1", sorted(set(resolved))]
    elif source_key and declaration.get("id"):
        identity = ["checkpoint-choice-v1", source_key, declaration["id"], value]
    else:
        return None
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
    return "cp1_" + hashlib.sha256(encoded).hexdigest()


def generation_checkpoint_identity_v1(
    contract: Any, controls: Any, graph: Any, source_key: str
) -> str | None:
    if not isinstance(contract, Mapping) or not isinstance(controls, Mapping):
        return None
    declarations = contract.get("inputs")
    if not isinstance(declarations, list):
        return None
    for declaration in declarations:
        if not isinstance(declaration, Mapping) or declaration.get("type") != "choice":
            continue
        if (
            declaration.get("semantic_role") not in {"model", "checkpoint"}
            and declaration.get("id") != "checkpoint"
        ):
            continue
        identity = checkpoint_identity_v1(
            declaration, controls.get(declaration.get("id")), graph, source_key
        )
        if identity:
            return identity
    return None
