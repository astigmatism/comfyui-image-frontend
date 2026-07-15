from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

JsonObject = dict[str, Any]
ArtifactMutator = Callable[[JsonObject, JsonObject, JsonObject], None]
ManifestMutator = Callable[[JsonObject], None]


KREA_STEM = "workflows/comfyui-image-frontend/Krea 2 NSFW V4"
GENERIC_STEM = "workflows/comfyui-image-frontend/Generic Landscape"
IMAGE_STEM = "workflows/comfyui-image-frontend/Moody Desire Image Input"
KREA_PUBLICATION_ID = "11111111-1111-4111-8111-111111111111"
GENERIC_PUBLICATION_ID = "22222222-2222-4222-8222-222222222222"
IMAGE_PUBLICATION_ID = "33333333-3333-4333-8333-333333333333"
NO_PUBLISHER_WARNING = (
    "No CIF publisher output is declared; complete native history outputs are collected "
    "as unmapped outputs."
)


def exact_json_bytes(value: Any) -> bytes:
    """Stable fixture encoding whose exact bytes are part of publication identity."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class PublicationBundle:
    stem: str
    manifest_path: str
    workflow_path: str
    api_path: str
    manifest_bytes: bytes
    workflow_bytes: bytes
    api_bytes: bytes

    @property
    def files(self) -> dict[str, bytes]:
        return {
            self.workflow_path: self.workflow_bytes,
            self.api_path: self.api_bytes,
            self.manifest_path: self.manifest_bytes,
        }

    def manifest(self) -> JsonObject:
        value = json.loads(self.manifest_bytes)
        assert isinstance(value, dict)
        return value

    def workflow(self) -> JsonObject:
        value = json.loads(self.workflow_bytes)
        assert isinstance(value, dict)
        return value

    def api(self) -> JsonObject:
        value = json.loads(self.api_bytes)
        assert isinstance(value, dict)
        return value


def object_info_fixture() -> JsonObject:
    return {
        "CIFTextParameter": {"input": {"required": {"value": ["STRING"]}}},
        "CIFIntegerParameter": {"input": {"required": {"value": ["INT"]}}},
        "CIFDecimalParameter": {"input": {"required": {"value": ["FLOAT"]}}},
        "CIFBooleanParameter": {"input": {"required": {"value": ["BOOLEAN"]}}},
        "CIFSeedParameter": {"input": {"required": {"value": ["INT"]}}},
        "CIFChoiceParameter": {
            "input": {
                "required": {
                    "value": ["STRING"],
                    "options_json": ["STRING"],
                }
            }
        },
        "CIFImageParameter": {
            "input": {
                "required": {
                    "image": [["fixture.png"], {"image_upload": True}],
                    "max_bytes": ["INT"],
                    "max_width": ["INT"],
                    "max_height": ["INT"],
                }
            },
            "output": ["IMAGE", "MASK"],
        },
        "CIFPublishImage": {"input": {"required": {"images": ["IMAGE"]}}},
        "FakeImageOutput": {
            "input": {
                "required": {
                    "prompt": ["STRING"],
                    "width": ["INT"],
                    "height": ["INT"],
                    "seed": ["INT"],
                    "enabled": ["BOOLEAN"],
                    "strength": ["FLOAT"],
                    "lora_name": ["STRING"],
                }
            }
        },
    }


def _binding(node_id: str, class_type: str) -> list[JsonObject]:
    return [{"node_id": node_id, "input": "value", "class_type": class_type}]


def _publisher_inputs(
    *,
    source_node_id: str,
    output_id: str,
    instance_uuid: str,
    role: str,
    description: str,
) -> JsonObject:
    return {
        "images": [source_node_id, 0],
        "output_id": output_id,
        "instance_uuid": instance_uuid,
        "role": role,
        "cardinality": "many",
        "description": description,
    }


def add_image_input(manifest: JsonObject, workflow: JsonObject, api: JsonObject) -> None:
    limits = {"max_bytes": 20 * 1024 * 1024, "max_width": 8192, "max_height": 8192}
    api["18"] = {
        "class_type": "CIFImageParameter",
        "inputs": {"image": "fixture.png", **limits},
    }
    workflow.setdefault("nodes", []).append(
        {"id": 18, "type": "CIFImageParameter", "widgets_values": ["fixture.png"]}
    )
    manifest["interface"]["inputs"].insert(
        0,
        {
            "id": "reference_image",
            "type": "image",
            "instance_uuid": "93cb0d2e-3d0f-4c3f-9ac6-f9a1ff289d61",
            "label": "Reference Image",
            "description": "Required source image whose content and dimensions guide the edit.",
            "semantic_role": "reference_image",
            "required": True,
            "advanced": False,
            "group": "Basic",
            "order": 5,
            "bindings": [{"node_id": "18", "input": "image", "class_type": "CIFImageParameter"}],
            "media": {
                "upload_route": "/upload/image",
                "storage_type": "input",
                "accepted_mime_types": ["image/png", "image/jpeg", "image/webp"],
                **limits,
                "animated": False,
                "returns_mask": True,
            },
        },
    )
    manifest["dependencies"]["class_types"] = sorted({node["class_type"] for node in api.values()})


def _krea_documents(publication_id: str) -> tuple[str, JsonObject, JsonObject, JsonObject]:
    stem = KREA_STEM
    private_choice_options = json.dumps(
        [
            {
                "value": "knp_v4_1",
                "binding": "Krea2/KNPV4.1_pre.safetensors",
            },
            {
                "value": "knp_v3_1",
                "binding": "Krea2/KNPV3.1_pre.safetensors",
            },
            {
                "value": "knp_v2",
                "binding": "Krea2/KNPV2_pre.safetensors",
            },
            {
                "value": "mysticxxx_krea2_v1",
                "binding": "Krea2/MysticXXX_Krea2_v1.safetensors",
            },
        ],
        separators=(",", ":"),
    )
    workflow: JsonObject = {
        "id": "fixture-krea-editable",
        "last_node_id": 202,
        "links": [],
        "nodes": [
            *[
                {"id": index, "type": class_type, "widgets_values": [default]}
                for index, class_type, default in (
                    (10, "CIFTextParameter", "a tree with chickens"),
                    (11, "CIFIntegerParameter", 1080),
                    (12, "CIFIntegerParameter", 1920),
                    (13, "CIFSeedParameter", 0),
                    (14, "CIFBooleanParameter", False),
                    (15, "CIFDecimalParameter", 1.0),
                )
            ],
            {
                "id": 202,
                "type": "CIFChoiceParameter",
                "widgets_values": ["knp_v4_1", private_choice_options],
            },
            {"id": 199, "type": "CIFPublishImage", "widgets_values": []},
            {"id": 200, "type": "CIFPublishImage", "widgets_values": []},
            {"id": 201, "type": "CIFPublishImage", "widgets_values": []},
        ],
        "version": 0.4,
    }
    api: JsonObject = {
        "10": {"class_type": "CIFTextParameter", "inputs": {"value": "a tree with chickens"}},
        "11": {"class_type": "CIFIntegerParameter", "inputs": {"value": 1080}},
        "12": {"class_type": "CIFIntegerParameter", "inputs": {"value": 1920}},
        "13": {"class_type": "CIFSeedParameter", "inputs": {"value": 0}},
        "14": {"class_type": "CIFBooleanParameter", "inputs": {"value": False}},
        "15": {"class_type": "CIFDecimalParameter", "inputs": {"value": 1.0}},
        "202": {
            "class_type": "CIFChoiceParameter",
            "inputs": {
                "value": "knp_v4_1",
                "options_json": private_choice_options,
            },
        },
        "20": {
            "class_type": "FakeImageOutput",
            "inputs": {
                "prompt": ["10", 0],
                "width": ["11", 0],
                "height": ["12", 0],
                "seed": ["13", 0],
                "enabled": ["14", 0],
                "strength": ["15", 0],
                "lora_name": ["202", 0],
            },
        },
        "199": {
            "class_type": "CIFPublishImage",
            "inputs": _publisher_inputs(
                source_node_id="20",
                output_id="base",
                instance_uuid="00000000-0000-4000-8000-000000000199",
                role="preview",
                description="Base-stage image before the second refinement pass.",
            ),
        },
        "200": {
            "class_type": "CIFPublishImage",
            "inputs": _publisher_inputs(
                source_node_id="20",
                output_id="second_pass",
                instance_uuid="00000000-0000-4000-8000-000000000200",
                role="comparison",
                description="Second-pass refined comparison image.",
            ),
        },
        "201": {
            "class_type": "CIFPublishImage",
            "inputs": _publisher_inputs(
                source_node_id="20",
                output_id="final",
                instance_uuid="00000000-0000-4000-8000-000000000201",
                role="final",
                description="Authoritative image selected by the workflow's final path.",
            ),
        },
    }
    inputs: list[JsonObject] = [
        {
            "id": "prompt",
            "type": "string",
            "instance_uuid": "00000000-0000-4000-8000-000000000010",
            "label": "Prompt",
            "description": "The positive image prompt.",
            "semantic_role": "positive_prompt",
            "required": True,
            "advanced": False,
            "group": "Basic",
            "order": 10,
            "default": "a tree with chickens",
            "bindings": _binding("10", "CIFTextParameter"),
        },
        {
            "id": "width",
            "type": "integer",
            "instance_uuid": "00000000-0000-4000-8000-000000000011",
            "label": "Width",
            "description": "Base latent width in pixels.",
            "semantic_role": "width",
            "required": True,
            "advanced": False,
            "group": "Basic",
            "order": 20,
            "default": 1080,
            "minimum": 16,
            "maximum": 2048,
            "step": 8,
            "bindings": _binding("11", "CIFIntegerParameter"),
        },
        {
            "id": "height",
            "type": "integer",
            "instance_uuid": "00000000-0000-4000-8000-000000000012",
            "label": "Height",
            "description": "Base latent height in pixels.",
            "semantic_role": "height",
            "required": True,
            "advanced": False,
            "group": "Basic",
            "order": 30,
            "default": 1920,
            "minimum": 16,
            "maximum": 2048,
            "step": 8,
            "bindings": _binding("12", "CIFIntegerParameter"),
        },
        {
            "id": "seed",
            "type": "seed",
            "instance_uuid": "00000000-0000-4000-8000-000000000013",
            "label": "Seed",
            "description": "Leave random or provide a fixed reproducible seed.",
            "semantic_role": "seed",
            "required": False,
            "advanced": False,
            "group": "Basic",
            "order": 40,
            "default": 0,
            "default_mode": "random",
            "minimum": 0,
            "maximum": 1125899906842624,
            "step": 1,
            "bindings": _binding("13", "CIFSeedParameter"),
        },
        {
            "id": "enable_seedvr2_upscale",
            "type": "boolean",
            "instance_uuid": "00000000-0000-4000-8000-000000000014",
            "label": "Enable SeedVR2 upscale",
            "description": "Use the optional SeedVR2 output branch.",
            "semantic_role": "feature_toggle",
            "required": False,
            "advanced": False,
            "group": "Basic",
            "order": 50,
            "default": False,
            "bindings": _binding("14", "CIFBooleanParameter"),
        },
        {
            "id": "lora",
            "type": "choice",
            "instance_uuid": "00000000-0000-4000-8000-000000000202",
            "label": "LoRA",
            "description": "Selects the LoRA applied by the primary model-only LoRA loader.",
            "semantic_role": "lora",
            "required": False,
            "advanced": True,
            "group": "Advanced",
            "order": 55,
            "default": "knp_v4_1",
            "choices": [
                {
                    "value": "knp_v4_1",
                    "label": "KNP v4.1",
                    "default_strength": 1.0,
                },
                {
                    "value": "knp_v3_1",
                    "label": "KNP v3.1",
                    "default_strength": 0.5,
                },
                {
                    "value": "knp_v2",
                    "label": "KNP v2",
                    "default_strength": 1.0,
                },
                {
                    "value": "mysticxxx_krea2_v1",
                    "label": "MysticXXX Krea2 v1",
                    "default_strength": 1.0,
                },
            ],
            "bindings": _binding("202", "CIFChoiceParameter"),
        },
        {
            "id": "lora_strength",
            "type": "number",
            "instance_uuid": "00000000-0000-4000-8000-000000000015",
            "label": "LoRA Strength",
            "description": (
                "Controls the model strength of the selected LoRA. Set 0 to disable its effect."
            ),
            "semantic_role": "lora",
            "required": False,
            "advanced": True,
            "group": "Advanced",
            "order": 60,
            "default": 1.0,
            "minimum": 0.0,
            "maximum": 2.0,
            "step": 0.05,
            "bindings": _binding("15", "CIFDecimalParameter"),
        },
    ]
    manifest: JsonObject = {
        "schema_version": "comfyui-image-frontend.publication/v1",
        "contract_schema": "comfyui-image-frontend.interface/v1",
        "publication_id": publication_id,
        "published_at": "2026-07-13T19:00:00Z",
        "source_id": f"{stem}.json",
        "workflow": {"path": f"{stem}.json", "sha256": ""},
        "api": {"path": f"{stem}.api.json", "sha256": "", "node_count": len(api)},
        "manifest": {"path": f"{stem}.interface.json"},
        "interface": {
            "inputs": inputs,
            "outputs": [
                {
                    "id": "base",
                    "instance_uuid": "00000000-0000-4000-8000-000000000199",
                    "label": "Base",
                    "description": "Base-stage image before the second refinement pass.",
                    "role": "preview",
                    "type": "image",
                    "cardinality": "many",
                    "node_id": "199",
                },
                {
                    "id": "second_pass",
                    "instance_uuid": "00000000-0000-4000-8000-000000000200",
                    "label": "Second pass",
                    "description": "Second-pass refined comparison image.",
                    "role": "comparison",
                    "type": "image",
                    "cardinality": "many",
                    "node_id": "200",
                },
                {
                    "id": "final",
                    "instance_uuid": "00000000-0000-4000-8000-000000000201",
                    "label": "Final",
                    "description": "Authoritative image selected by the workflow's final path.",
                    "role": "final",
                    "type": "image",
                    "cardinality": "many",
                    "node_id": "201",
                },
            ],
            "unmapped_outputs_policy": "collect",
            "native_outputs": [
                {"node_id": "20", "class_type": "FakeImageOutput", "title": "Native output"},
                {"node_id": "199", "class_type": "CIFPublishImage", "title": "Base"},
                {"node_id": "200", "class_type": "CIFPublishImage", "title": "Second pass"},
                {"node_id": "201", "class_type": "CIFPublishImage", "title": "Final"},
            ],
        },
        "dependencies": {"class_types": sorted({node["class_type"] for node in api.values()})},
        "warnings": [],
        "runtime": {"attach_workflow_as_extra_pnginfo": True},
    }
    return stem, workflow, api, manifest


def _generic_documents(publication_id: str) -> tuple[str, JsonObject, JsonObject, JsonObject]:
    stem = GENERIC_STEM
    workflow: JsonObject = {
        "id": "fixture-generic-editable",
        "links": [],
        "nodes": [
            {"id": 110, "type": "CIFTextParameter", "widgets_values": ["mountain lake"]},
            {"id": 111, "type": "CIFIntegerParameter", "widgets_values": [1]},
            {"id": 130, "type": "CIFPublishImage", "widgets_values": []},
        ],
        "version": 0.4,
    }
    api: JsonObject = {
        "110": {"class_type": "CIFTextParameter", "inputs": {"value": "mountain lake"}},
        "111": {"class_type": "CIFIntegerParameter", "inputs": {"value": 1}},
        "120": {
            "class_type": "FakeImageOutput",
            "inputs": {
                "prompt": ["110", 0],
                "width": 512,
                "height": 512,
                "seed": 1,
                "enabled": True,
                "strength": 1.0,
            },
        },
        "130": {
            "class_type": "CIFPublishImage",
            "inputs": _publisher_inputs(
                source_node_id="120",
                output_id="final_image",
                instance_uuid="00000000-0000-4000-8000-000000000130",
                role="final",
                description="Declared generic final image.",
            ),
        },
    }
    manifest: JsonObject = {
        "schema_version": "comfyui-image-frontend.publication/v1",
        "contract_schema": "comfyui-image-frontend.interface/v1",
        "publication_id": publication_id,
        "published_at": "2026-07-13T19:05:00Z",
        "source_id": f"{stem}.json",
        "workflow": {"path": f"{stem}.json", "sha256": ""},
        "api": {"path": f"{stem}.api.json", "sha256": "", "node_count": len(api)},
        "manifest": {"path": f"{stem}.interface.json"},
        "interface": {
            "inputs": [
                {
                    "id": "prompt",
                    "type": "string",
                    "instance_uuid": "00000000-0000-4000-8000-000000000110",
                    "label": "Prompt",
                    "description": "Landscape prompt.",
                    "semantic_role": "positive_prompt",
                    "required": True,
                    "advanced": False,
                    "group": "Basic",
                    "order": 1,
                    "default": "mountain lake",
                    "bindings": _binding("110", "CIFTextParameter"),
                },
                {
                    "id": "iterations",
                    "type": "integer",
                    "instance_uuid": "00000000-0000-4000-8000-000000000111",
                    "label": "Iterations",
                    "description": "A generic source-specific integer.",
                    "semantic_role": "iteration_count",
                    "required": False,
                    "advanced": False,
                    "group": "Basic",
                    "order": 2,
                    "default": 1,
                    "minimum": 1,
                    "maximum": 4,
                    "step": 1,
                    "bindings": _binding("111", "CIFIntegerParameter"),
                },
            ],
            "outputs": [
                {
                    "id": "final_image",
                    "instance_uuid": "00000000-0000-4000-8000-000000000130",
                    "label": "Final image",
                    "description": "Declared generic final image.",
                    "role": "final",
                    "type": "image",
                    "cardinality": "many",
                    "node_id": "130",
                }
            ],
            "unmapped_outputs_policy": "collect",
            "native_outputs": {
                "120": {"class_type": "FakeImageOutput", "title": "Native image"},
                "130": {"class_type": "CIFPublishImage", "title": "Final image"},
            },
        },
        "dependencies": {"class_types": sorted({node["class_type"] for node in api.values()})},
        "warnings": [],
        "runtime": {"attach_workflow_as_extra_pnginfo": False},
    }
    return stem, workflow, api, manifest


def _image_documents(publication_id: str) -> tuple[str, JsonObject, JsonObject, JsonObject]:
    _, workflow, api, manifest = _generic_documents(publication_id)
    stem = IMAGE_STEM
    workflow["id"] = "fixture-image-input-editable"
    manifest["source_id"] = f"{stem}.json"
    manifest["workflow"]["path"] = f"{stem}.json"
    manifest["api"]["path"] = f"{stem}.api.json"
    manifest["manifest"]["path"] = f"{stem}.interface.json"
    manifest["published_at"] = "2026-07-15T20:03:03Z"
    add_image_input(manifest, workflow, api)
    return stem, workflow, api, manifest


def build_publication_bundle(
    kind: str = "krea",
    *,
    publication_id: str | None = None,
    mutate_artifacts: ArtifactMutator | None = None,
    mutate_manifest: ManifestMutator | None = None,
    corrupt: str | None = None,
) -> PublicationBundle:
    """Build an exact-byte publication, optionally applying an adversarial mutation.

    ``mutate_artifacts`` runs before hashes and node_count are committed. ``mutate_manifest``
    runs afterward, allowing deliberate metadata mismatches. ``corrupt`` may be ``workflow`` or
    ``api`` and changes exact bytes without making the JSON syntactically invalid.
    """

    if kind == "krea":
        stem, workflow, api, manifest = _krea_documents(publication_id or KREA_PUBLICATION_ID)
    elif kind == "generic":
        stem, workflow, api, manifest = _generic_documents(publication_id or GENERIC_PUBLICATION_ID)
    elif kind == "image":
        stem, workflow, api, manifest = _image_documents(publication_id or IMAGE_PUBLICATION_ID)
    else:
        raise ValueError(f"unknown fixture publication kind: {kind}")
    workflow = copy.deepcopy(workflow)
    api = copy.deepcopy(api)
    manifest = copy.deepcopy(manifest)
    if mutate_artifacts:
        mutate_artifacts(manifest, workflow, api)
    manifest["api"]["node_count"] = len(api)
    workflow_bytes = exact_json_bytes(workflow)
    api_bytes = exact_json_bytes(api)
    manifest["workflow"]["sha256"] = sha256_bytes(workflow_bytes)
    manifest["api"]["sha256"] = sha256_bytes(api_bytes)
    if mutate_manifest:
        mutate_manifest(manifest)
    manifest_bytes = exact_json_bytes(manifest)
    if corrupt == "workflow":
        workflow_bytes += b"\n"
    elif corrupt == "api":
        api_bytes += b"\n"
    elif corrupt is not None:
        raise ValueError("corrupt must be 'workflow', 'api', or None")
    return PublicationBundle(
        stem=stem,
        manifest_path=f"{stem}.interface.json",
        workflow_path=f"{stem}.json",
        api_path=f"{stem}.api.json",
        manifest_bytes=manifest_bytes,
        workflow_bytes=workflow_bytes,
        api_bytes=api_bytes,
    )


def build_publication_files(*, include_generic: bool = True) -> dict[str, bytes]:
    files = dict(build_publication_bundle("krea").files)
    if include_generic:
        files.update(build_publication_bundle("generic").files)
    return files
