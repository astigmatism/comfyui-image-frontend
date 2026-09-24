"""Text output extraction and the pinned StableLlama seed compatibility adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import AppError
from .compiler import MAX_PUBLIC_STRING_LENGTH, CompileResult, WorkflowCompiler
from .publication import sha256_json

STABLELLAMA_PUBLICATION = "b11b9ce9-53f0-44f1-8e8d-296fc54c5949"
STABLELLAMA_HASHES = (
    "0fffb74f0331a8918b97129c1d3c91625bff5d16bec39979dffe8b4f9c4f53e3",
    "84843b65f2c0847ae4ff5ef644d56559800bce7278ed47a27968ceffb01f3a6c",
    "e5ae15f00a6ae252226364f71afcbfe6657ccc888644ad91461c069acb9341f5",
)


def adapt_seed(profile: Any, compiled: CompileResult, compiler: WorkflowCompiler) -> str:
    candidate = str(getattr(profile, "source_id", "")).endswith(
        "/StableLlama Erotic Prompts v1.json"
    )
    if profile.publication_id == STABLELLAMA_PUBLICATION or candidate:
        node = compiled.compiled_graph.get("909", {})
        if (
            profile.publication_id != STABLELLAMA_PUBLICATION
            or (profile.ui_graph_sha256, profile.api_graph_sha256, profile.manifest_sha256)
            != STABLELLAMA_HASHES
            or node.get("class_type") != "HFDatasetShuffle"
            or type(node.get("inputs", {}).get("seed")) is not int
        ):
            raise AppError(
                "prompt_adapter_mismatch",
                "The prompt source changed; its seed adapter must be reviewed.",
                status_code=409,
            )
        # HFDatasetShuffle declares a signed 32-bit, nonnegative INT input.
        seed = compiler.seed_resolver(0, 2**31 - 1)
        node["inputs"]["seed"] = seed
        compiled.resolved_seeds["stablellama.dataset_seed"] = str(seed)
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
