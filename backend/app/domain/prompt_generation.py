"""Text output extraction and the structural dataset-seed compatibility adapter."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from ..errors import AppError
from .compiler import MAX_PUBLIC_STRING_LENGTH, CompileResult, WorkflowCompiler
from .publication import sha256_json

# Recognized sampling nodes: class_type -> (input name, inclusive minimum, inclusive
# maximum). ComfyUI rejects a value above the node's own declared maximum, so the
# bounds belong to the recognition entry. HFDatasetShuffle declares a signed 32-bit,
# nonnegative INT input.
DATASET_SEED_NODES: dict[str, tuple[str, int, int]] = {
    "HFDatasetShuffle": ("seed", 0, 2**31 - 1),
}
# Retained key name: automatic-batch retirement reads this seed on restart.
LEGACY_DATASET_SEED_KEY = "stablellama.dataset_seed"


def _declared_seed_bindings(contract: Any) -> set[tuple[str, str]]:
    """Return the (node ID, input) pairs a published seed parameter already owns."""

    bound: set[tuple[str, str]] = set()
    declarations = contract.get("inputs") if isinstance(contract, Mapping) else None
    if not isinstance(declarations, list):
        return bound
    for declaration in declarations:
        if not isinstance(declaration, Mapping) or declaration.get("type") != "seed":
            continue
        bindings = declaration.get("bindings")
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if isinstance(binding, Mapping):
                bound.add((str(binding.get("node_id")), str(binding.get("input"))))
    return bound


def _dataset_seed_nodes(
    graph: Mapping[str, Any],
) -> Iterator[tuple[str, str, dict[str, Any], str, int, int]]:
    """Yield recognized sampling nodes in a stable, numeric-aware node ID order."""

    for node_id in sorted(graph, key=lambda value: (len(value), value)):
        node = graph[node_id]
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(class_type, str) or not isinstance(inputs, dict):
            continue
        recognized = DATASET_SEED_NODES.get(class_type)
        if recognized is None:
            continue
        input_name, minimum, maximum = recognized
        yield node_id, class_type, inputs, input_name, minimum, maximum


def adapt_seed(profile: Any, compiled: CompileResult, compiler: WorkflowCompiler) -> str:
    """Give recognized sampling nodes a fresh request-local seed, then hash the graph.

    Recognition is structural. Publication identity (its ID, revision hashes and
    filename) is deliberately not a gate: ComfyUI mints a new publication ID on every
    publish, so an identity pin locks the source out permanently once it is published
    again. A published seed parameter owns its own binding and is never patched here.
    """

    bound = _declared_seed_bindings(getattr(profile, "resolved_contract_json", None))
    patched = 0
    for node_id, class_type, inputs, input_name, minimum, maximum in _dataset_seed_nodes(
        compiled.compiled_graph
    ):
        if (node_id, input_name) in bound:
            continue
        current = inputs.get(input_name)
        if isinstance(current, bool) or not isinstance(current, int):
            # A converted widget carries a link, not a value; it cannot be patched
            # without changing the published graph, so the request fails visibly
            # instead of silently repeating one cached sample.
            raise AppError(
                "prompt_adapter_mismatch",
                f"This prompt source cannot receive a fresh sampling seed. "
                f"Publish node {node_id} ({class_type}) with an ordinary seed parameter.",
                status_code=409,
                details={
                    "node_id": node_id,
                    "class_type": class_type,
                    "input": input_name,
                    "reason": "seed_input_not_literal",
                },
            )
        seed = compiler.seed_resolver(minimum, maximum)
        if isinstance(seed, bool) or not isinstance(seed, int) or not minimum <= seed <= maximum:
            raise RuntimeError("seed resolver returned a value outside its requested range")
        inputs[input_name] = seed
        key = (
            LEGACY_DATASET_SEED_KEY
            if patched == 0
            else f"{class_type}.{node_id}.{input_name}".lower()
        )
        compiled.resolved_seeds[key] = str(seed)
        patched += 1
    return sha256_json(compiled.compiled_graph)


def collect_text(contract: Mapping[str, Any], history: Mapping[str, Any]) -> str:
    final = next(item for item in contract["outputs"] if item["role"] == "final")
    output = history.get("outputs", {}).get(str(final["node_id"]), {})
    metadata = output.get("comfyui_image_frontend", [])
    matches = (
        [
            item
            for item in metadata
            if isinstance(item, dict)
            and all(
                item.get(key) == expected
                for key, expected in (
                    ("output_id", final["id"]),
                    ("instance_uuid", final["instance_uuid"]),
                    ("role", "final"),
                    ("kind", "text"),
                    ("cardinality", "one"),
                )
            )
        ]
        if isinstance(metadata, list)
        else []
    )
    text = output.get("text")
    if (
        len(matches) != 1
        or not isinstance(text, list)
        or len(text) != 1
        or not isinstance(text[0], str)
        or matches[0].get("value") != text[0]
        or not text[0].strip()
        or len(text[0]) > MAX_PUBLIC_STRING_LENGTH
    ):
        raise AppError(
            "prompt_output_invalid",
            "The prompt source did not return one valid declared text result.",
        )
    return text[0]
