from __future__ import annotations

import json
import math
import os
import re
import uuid
from typing import Any

import folder_paths
import nodes as comfy_nodes
from PIL import Image, UnidentifiedImageError

from .lora_stack import validate_lora_stack

CONTRACT_SCHEMA = "comfyui-image-frontend.interface/v1"
CATEGORY = "comfyui-image-frontend/interface"
MAX_SEED = 0xFFFFFFFFFFFFFFFF
MIN_INTEGER = -(2**63)
MAX_INTEGER = (2**63) - 1

_PUBLIC_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)

SEMANTIC_ROLES = [
    "custom",
    "positive_prompt",
    "negative_prompt",
    "seed",
    "width",
    "height",
    "batch_size",
    "steps",
    "cfg",
    "denoise",
    "sampler",
    "scheduler",
    "model",
    "lora",
    "upscale",
    "reference_image",
]

OUTPUT_ROLES = ["final", "preview", "comparison", "auxiliary"]
MAX_CHOICE_OPTIONS = 100
MAX_CHOICE_OPTIONS_JSON = 50000
ACCEPTED_IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}


def _validate_public_id(value: str, field: str) -> str:
    normalized = str(value).strip()
    if not _PUBLIC_ID.fullmatch(normalized):
        raise ValueError(
            f"{field} must start with a lowercase letter and contain only "
            "lowercase letters, digits, and underscores (maximum 64 characters)"
        )
    return normalized


def _validate_uuid(value: str, *, allow_empty: bool = False) -> str:
    normalized = str(value).strip().lower()
    if not normalized and allow_empty:
        return ""

    try:
        parsed = uuid.UUID(normalized)
    except (ValueError, AttributeError) as exc:
        raise ValueError("instance_uuid must be a UUID generated for this node instance") from exc

    if str(parsed) != normalized:
        raise ValueError("instance_uuid must use canonical lowercase UUID form")
    return normalized


def _validate_common_declaration(
    *,
    parameter_id: str,
    instance_uuid: str,
    label: str,
    description: str,
    group: str,
    order: int,
) -> None:
    _validate_public_id(parameter_id, "parameter_id")
    # The UUID is authoring/compiler metadata. A missing value must not make an
    # otherwise valid ComfyUI graph fail at runtime; the frontend helper fills it
    # when the workflow is edited, and bundle validation can require it later.
    _validate_uuid(instance_uuid, allow_empty=True)

    if not str(label).strip():
        raise ValueError("label must not be empty")
    if len(str(label)) > 120:
        raise ValueError("label must not exceed 120 characters")
    if len(str(description)) > 1000:
        raise ValueError("description must not exceed 1000 characters")
    if not str(group).strip():
        raise ValueError("group must not be empty")
    if int(order) < 0:
        raise ValueError("order must be non-negative")


def _validate_number_contract(
    *,
    value: int | float,
    minimum: int | float,
    maximum: int | float,
    step: int | float,
) -> None:
    numbers = (value, minimum, maximum, step)
    if any(isinstance(item, float) and not math.isfinite(item) for item in numbers):
        raise ValueError("numeric parameter fields must be finite")
    if minimum > maximum:
        raise ValueError("minimum must not exceed maximum")
    if step <= 0:
        raise ValueError("step must be greater than zero")
    if value < minimum or value > maximum:
        raise ValueError("value must be between minimum and maximum")


def _common_parameter_inputs() -> dict[str, tuple[Any, ...]]:
    return {
        "parameter_id": (
            "STRING",
            {"default": "parameter", "multiline": False},
        ),
        "instance_uuid": (
            "STRING",
            {"default": "", "multiline": False},
        ),
        "label": ("STRING", {"default": "Parameter", "multiline": False}),
        "description": ("STRING", {"default": "", "multiline": True}),
        "semantic_role": (SEMANTIC_ROLES,),
        "required": ("BOOLEAN", {"default": False}),
        "advanced": ("BOOLEAN", {"default": False}),
        "group": ("STRING", {"default": "Basic", "multiline": False}),
        "order": ("INT", {"default": 100, "min": 0, "max": 100000}),
    }


def _validate_parameter_kwargs(kwargs: dict[str, Any]) -> None:
    _validate_common_declaration(
        parameter_id=kwargs["parameter_id"],
        instance_uuid=kwargs["instance_uuid"],
        label=kwargs["label"],
        description=kwargs["description"],
        group=kwargs["group"],
        order=kwargs["order"],
    )


def _parse_choice_options(options_json: str) -> list[dict[str, Any]]:
    serialized = str(options_json)
    if len(serialized) > MAX_CHOICE_OPTIONS_JSON:
        raise ValueError(f"options_json must not exceed {MAX_CHOICE_OPTIONS_JSON} characters")

    try:
        value = json.loads(serialized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("options_json must be a valid JSON array") from exc

    if not isinstance(value, list) or not value:
        raise ValueError("options_json must contain at least one choice")
    if len(value) > MAX_CHOICE_OPTIONS:
        raise ValueError(f"options_json must not contain more than {MAX_CHOICE_OPTIONS} choices")

    choices: list[dict[str, Any]] = []
    public_ids: set[str] = set()
    bindings: set[str] = set()
    for index, raw_option in enumerate(value):
        if not isinstance(raw_option, dict):
            raise ValueError(f"choice {index + 1} must be a JSON object")

        public_id = _validate_public_id(raw_option.get("id", ""), "choice id")
        label = str(raw_option.get("label", "")).strip()
        binding = raw_option.get("binding")
        if not label:
            raise ValueError(f"choice {public_id} label must not be empty")
        if len(label) > 120:
            raise ValueError(f"choice {public_id} label must not exceed 120 characters")
        if not isinstance(binding, str) or not binding.strip():
            raise ValueError(f"choice {public_id} binding must be a nonempty string")
        binding = binding.strip()
        if len(binding) > 1000 or "\x00" in binding:
            raise ValueError(f"choice {public_id} binding is invalid")
        if public_id in public_ids:
            raise ValueError(f"duplicate choice id {public_id}")
        if binding in bindings:
            raise ValueError(f"duplicate choice binding for {public_id}")

        choice: dict[str, Any] = {
            "id": public_id,
            "label": label,
            "binding": binding,
        }
        if "default_strength" in raw_option:
            default_strength = raw_option["default_strength"]
            if (
                isinstance(default_strength, bool)
                or not isinstance(default_strength, (int, float))
                or not math.isfinite(float(default_strength))
            ):
                raise ValueError(f"choice {public_id} default_strength must be a finite number")
            choice["default_strength"] = float(default_strength)

        public_ids.add(public_id)
        bindings.add(binding)
        choices.append(choice)

    return choices


def _inspect_reference_image(image_path: str) -> tuple[int, int]:
    try:
        with Image.open(image_path) as opened:
            image_format = str(opened.format or "").upper()
            width, height = opened.size
            frame_count = int(getattr(opened, "n_frames", 1))
            opened.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("reference image is not a valid image file") from exc

    if image_format not in ACCEPTED_IMAGE_FORMATS:
        raise ValueError("reference image must be PNG, JPEG, or WebP")
    if frame_count != 1:
        raise ValueError("reference image must contain exactly one static frame")
    return int(width), int(height)


class CIFImageFrontendInterface:
    """Common prompt-local controls with stable public output names."""

    CATEGORY = CATEGORY
    FUNCTION = "expose"
    RETURN_TYPES = ("STRING", "STRING", "INT", "INT", "INT", "INT", "BOOLEAN")
    RETURN_NAMES = (
        "positive_prompt",
        "negative_prompt",
        "seed",
        "width",
        "height",
        "batch_size",
        "enable_upscale",
    )
    OUTPUT_TOOLTIPS = (
        "Public parameter: positive_prompt",
        "Public parameter: negative_prompt",
        "Public parameter: seed",
        "Public parameter: width",
        "Public parameter: height",
        "Public parameter: batch_size",
        "Public parameter: enable_upscale; connect only to an execution-safe switch",
    )
    DESCRIPTION = (
        "Reusable control surface for comfyui-image-frontend. Only connected outputs "
        "should be advertised by an exported workflow interface. Values are prompt-local."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive_prompt": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "dynamicPrompts": True,
                    },
                ),
                "negative_prompt": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "dynamicPrompts": True,
                    },
                ),
                "seed": (
                    "INT",
                    {"default": 0, "min": 0, "max": MAX_SEED},
                ),
                "width": (
                    "INT",
                    {"default": 1024, "min": 16, "max": 16384, "step": 8},
                ),
                "height": (
                    "INT",
                    {"default": 1024, "min": 16, "max": 16384, "step": 8},
                ),
                "batch_size": (
                    "INT",
                    {"default": 1, "min": 1, "max": 4096},
                ),
                "enable_upscale": ("BOOLEAN", {"default": False}),
                "interface_id": (
                    "STRING",
                    {"default": "main", "multiline": False},
                ),
                "interface_version": (
                    "STRING",
                    {"default": "1.0.0", "multiline": False},
                ),
                "instance_uuid": (
                    "STRING",
                    {"default": "", "multiline": False},
                ),
            }
        }

    def expose(
        self,
        positive_prompt: str,
        negative_prompt: str,
        seed: int,
        width: int,
        height: int,
        batch_size: int,
        enable_upscale: bool,
        interface_id: str,
        interface_version: str,
        instance_uuid: str,
    ) -> tuple[str, str, int, int, int, int, bool]:
        _validate_public_id(interface_id, "interface_id")
        if not _SEMVER.fullmatch(str(interface_version).strip()):
            raise ValueError("interface_version must be a semantic version such as 1.0.0")
        _validate_uuid(instance_uuid, allow_empty=True)

        return (
            str(positive_prompt),
            str(negative_prompt),
            int(seed),
            int(width),
            int(height),
            int(batch_size),
            bool(enable_upscale),
        )


class CIFTextParameter:
    CATEGORY = CATEGORY
    FUNCTION = "expose"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("value",)
    DESCRIPTION = "Declares one reusable string parameter for comfyui-image-frontend."

    @classmethod
    def INPUT_TYPES(cls):
        inputs = {
            "value": (
                "STRING",
                {"default": "", "multiline": True, "dynamicPrompts": True},
            )
        }
        inputs.update(_common_parameter_inputs())
        return {"required": inputs}

    def expose(self, value: str, **kwargs: Any) -> tuple[str]:
        _validate_parameter_kwargs(kwargs)
        return (str(value),)


class CIFIntegerParameter:
    CATEGORY = CATEGORY
    FUNCTION = "expose"
    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("value",)
    DESCRIPTION = "Declares one reusable integer parameter for comfyui-image-frontend."

    @classmethod
    def INPUT_TYPES(cls):
        inputs = {
            "value": (
                "INT",
                {"default": 0, "min": MIN_INTEGER, "max": MAX_INTEGER},
            ),
            "minimum": (
                "INT",
                {"default": 0, "min": MIN_INTEGER, "max": MAX_INTEGER},
            ),
            "maximum": (
                "INT",
                {"default": 100, "min": MIN_INTEGER, "max": MAX_INTEGER},
            ),
            "step": (
                "INT",
                {"default": 1, "min": 1, "max": MAX_INTEGER},
            ),
        }
        inputs.update(_common_parameter_inputs())
        return {"required": inputs}

    def expose(
        self,
        value: int,
        minimum: int,
        maximum: int,
        step: int,
        **kwargs: Any,
    ) -> tuple[int]:
        _validate_parameter_kwargs(kwargs)
        _validate_number_contract(
            value=value,
            minimum=minimum,
            maximum=maximum,
            step=step,
        )
        return (int(value),)


class CIFDecimalParameter:
    CATEGORY = CATEGORY
    FUNCTION = "expose"
    RETURN_TYPES = ("FLOAT",)
    RETURN_NAMES = ("value",)
    DESCRIPTION = "Declares one reusable decimal parameter for comfyui-image-frontend."

    @classmethod
    def INPUT_TYPES(cls):
        inputs = {
            "value": (
                "FLOAT",
                {"default": 0.0, "min": -1.0e12, "max": 1.0e12, "step": 0.01},
            ),
            "minimum": (
                "FLOAT",
                {"default": 0.0, "min": -1.0e12, "max": 1.0e12},
            ),
            "maximum": (
                "FLOAT",
                {"default": 1.0, "min": -1.0e12, "max": 1.0e12},
            ),
            "step": (
                "FLOAT",
                {"default": 0.01, "min": 1.0e-12, "max": 1.0e12},
            ),
        }
        inputs.update(_common_parameter_inputs())
        return {"required": inputs}

    def expose(
        self,
        value: float,
        minimum: float,
        maximum: float,
        step: float,
        **kwargs: Any,
    ) -> tuple[float]:
        _validate_parameter_kwargs(kwargs)
        _validate_number_contract(
            value=value,
            minimum=minimum,
            maximum=maximum,
            step=step,
        )
        return (float(value),)


class CIFBooleanParameter:
    CATEGORY = CATEGORY
    FUNCTION = "expose"
    RETURN_TYPES = ("BOOLEAN",)
    RETURN_NAMES = ("value",)
    DESCRIPTION = "Declares one reusable Boolean parameter for comfyui-image-frontend."

    @classmethod
    def INPUT_TYPES(cls):
        inputs = {"value": ("BOOLEAN", {"default": False})}
        inputs.update(_common_parameter_inputs())
        return {"required": inputs}

    def expose(self, value: bool, **kwargs: Any) -> tuple[bool]:
        _validate_parameter_kwargs(kwargs)
        return (bool(value),)


class CIFSeedParameter:
    CATEGORY = CATEGORY
    FUNCTION = "expose"
    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("value",)
    DESCRIPTION = (
        "Declares one seed parameter. Runtime API prompts must always provide a concrete "
        "integer; default_mode is contract metadata for the frontend adapter."
    )

    @classmethod
    def INPUT_TYPES(cls):
        inputs = {
            "value": (
                "INT",
                {
                    "default": 0,
                    "min": 0,
                    "max": MAX_SEED,
                    "control_after_generate": True,
                },
            ),
            "minimum": ("INT", {"default": 0, "min": 0, "max": MAX_SEED}),
            "maximum": (
                "INT",
                {"default": MAX_SEED, "min": 0, "max": MAX_SEED},
            ),
            "step": ("INT", {"default": 1, "min": 1, "max": MAX_SEED}),
            "default_mode": (["random", "fixed"],),
        }
        inputs.update(_common_parameter_inputs())
        return {"required": inputs}

    def expose(
        self,
        value: int,
        minimum: int,
        maximum: int,
        step: int,
        default_mode: str,
        **kwargs: Any,
    ) -> tuple[int]:
        _validate_parameter_kwargs(kwargs)
        _validate_number_contract(
            value=value,
            minimum=minimum,
            maximum=maximum,
            step=step,
        )
        if default_mode not in {"fixed", "random"}:
            raise ValueError("default_mode must be fixed or random")
        return (int(value),)


class CIFChoiceParameter:
    CATEGORY = CATEGORY
    FUNCTION = "expose"
    # Legacy Comfy combo inputs are typed as their literal option arrays rather
    # than the symbolic string "COMBO". A validated wildcard transport is
    # required for one reusable mapped-choice node to connect to those inputs.
    # Publication verifies that every private binding is admitted by every
    # connected destination's installed combo definition.
    RETURN_TYPES = ("*",)
    RETURN_NAMES = ("value",)
    DESCRIPTION = (
        "Declares one finite public choice. The public value is a safe option ID; "
        "the output is its private destination-validated choice binding."
    )

    @classmethod
    def INPUT_TYPES(cls):
        inputs = {
            "value": (
                "STRING",
                {"default": "option", "multiline": False},
            ),
            "options_json": (
                "STRING",
                {
                    "default": json.dumps(
                        [
                            {
                                "id": "option",
                                "label": "Option",
                                "binding": "option",
                            }
                        ],
                        indent=2,
                    ),
                    "multiline": True,
                },
            ),
        }
        inputs.update(_common_parameter_inputs())
        return {"required": inputs}

    def expose(
        self,
        value: str,
        options_json: str,
        **kwargs: Any,
    ) -> tuple[str]:
        _validate_parameter_kwargs(kwargs)
        choices = _parse_choice_options(options_json)
        selected_id = _validate_public_id(value, "choice value")
        for choice in choices:
            if choice["id"] == selected_id:
                return (choice["binding"],)
        raise ValueError(f"choice value {selected_id!r} is not in options_json")


class CIFImageParameter:
    CATEGORY = CATEGORY
    FUNCTION = "load_image"
    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    DESCRIPTION = (
        "Declares one required reference-image parameter. Local authoring uses "
        "ComfyUI's input upload widget; API callers upload bytes through ComfyUI's "
        "native input route and the adapter patches this prompt-local image value."
    )
    ACCEPTED_MIME_TYPES = ("image/png", "image/jpeg", "image/webp")

    def __init__(self) -> None:
        self._load_image = comfy_nodes.LoadImage()

    @classmethod
    def INPUT_TYPES(cls):
        load_image_input = comfy_nodes.LoadImage.INPUT_TYPES()["required"]["image"]
        inputs = {
            "image": load_image_input,
            "max_bytes": (
                "INT",
                {
                    "default": 20 * 1024 * 1024,
                    "min": 1,
                    "max": 1024 * 1024 * 1024,
                    "step": 1024,
                },
            ),
            "max_width": (
                "INT",
                {"default": 8192, "min": 1, "max": 65536, "step": 1},
            ),
            "max_height": (
                "INT",
                {"default": 8192, "min": 1, "max": 65536, "step": 1},
            ),
        }
        inputs.update(_common_parameter_inputs())
        return {"required": inputs}

    @classmethod
    def VALIDATE_INPUTS(
        cls,
        image: str,
        max_bytes: int,
        max_width: int,
        max_height: int,
        **kwargs: Any,
    ) -> bool | str:
        validation = comfy_nodes.LoadImage.VALIDATE_INPUTS(image)
        if validation is not True:
            return validation
        if int(max_bytes) < 1 or int(max_width) < 1 or int(max_height) < 1:
            return "Image limits must be positive integers"
        image_path = folder_paths.get_annotated_filepath(image)
        if os.path.getsize(image_path) > int(max_bytes):
            return f"Image exceeds the declared {int(max_bytes)} byte limit"
        try:
            width, height = _inspect_reference_image(image_path)
        except ValueError as exc:
            return str(exc)
        if width > int(max_width) or height > int(max_height):
            return (
                f"reference image is {width}x{height}, exceeding the declared "
                f"{int(max_width)}x{int(max_height)} limit"
            )
        return True

    @classmethod
    def IS_CHANGED(cls, image: str, **kwargs: Any) -> str:
        return comfy_nodes.LoadImage.IS_CHANGED(image)

    def load_image(
        self,
        image: str,
        max_bytes: int,
        max_width: int,
        max_height: int,
        **kwargs: Any,
    ) -> tuple[Any, Any]:
        _validate_parameter_kwargs(kwargs)
        validation = self.VALIDATE_INPUTS(
            image=image,
            max_bytes=max_bytes,
            max_width=max_width,
            max_height=max_height,
        )
        if validation is not True:
            raise ValueError(str(validation))

        images, masks = self._load_image.load_image(image)
        shape = getattr(images, "shape", ())
        if len(shape) < 4:
            raise ValueError("reference image did not decode to a ComfyUI IMAGE batch")
        batch_size, height, width = (int(shape[0]), int(shape[1]), int(shape[2]))
        if batch_size != 1:
            raise ValueError("reference image must contain exactly one static frame")
        if width > int(max_width) or height > int(max_height):
            raise ValueError(
                f"reference image is {width}x{height}, exceeding the declared "
                f"{int(max_width)}x{int(max_height)} limit"
            )
        return images, masks


def _validate_output_declaration(
    *,
    output_id: str,
    instance_uuid: str,
    description: str,
) -> tuple[str, str]:
    normalized_id = _validate_public_id(output_id, "output_id")
    normalized_uuid = _validate_uuid(instance_uuid, allow_empty=True)
    if len(str(description)) > 1000:
        raise ValueError("description must not exceed 1000 characters")
    return normalized_id, normalized_uuid


def _safe_filename_prefix(value: str) -> str:
    normalized = str(value).strip().replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("filename_prefix must not be empty")
    if os.path.isabs(str(value)) or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError("filename_prefix must be a safe relative output path")
    return normalized


class CIFPublishImage:
    CATEGORY = CATEGORY
    FUNCTION = "publish"
    RETURN_TYPES = ()
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Saves every image in a batch through ComfyUI's built-in SaveImage implementation "
        "and publishes a namespaced comfyui-image-frontend result in prompt history."
    )

    def __init__(self) -> None:
        self._save_image = comfy_nodes.SaveImage()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"forceInput": True}),
                "output_id": (
                    "STRING",
                    {"default": "final", "multiline": False},
                ),
                "role": (OUTPUT_ROLES,),
                "description": ("STRING", {"default": "", "multiline": True}),
                "cardinality": (["many", "one"],),
                "filename_prefix": (
                    "STRING",
                    {
                        "default": "comfyui-image-frontend",
                        "multiline": False,
                    },
                ),
                "instance_uuid": (
                    "STRING",
                    {"default": "", "multiline": False},
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    def publish(
        self,
        images: Any,
        output_id: str,
        role: str,
        description: str,
        cardinality: str,
        filename_prefix: str,
        instance_uuid: str,
        prompt: dict[str, Any] | None = None,
        extra_pnginfo: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        output_id, instance_uuid = _validate_output_declaration(
            output_id=output_id,
            instance_uuid=instance_uuid,
            description=description,
        )
        prefix = _safe_filename_prefix(filename_prefix)

        try:
            batch_size = len(images)
        except TypeError as exc:
            raise ValueError("images must be a ComfyUI IMAGE batch") from exc

        if batch_size < 1:
            raise ValueError("images must contain at least one image")
        if cardinality == "one" and batch_size != 1:
            raise ValueError(
                f"output {output_id!r} declares cardinality one but received {batch_size} images"
            )

        saved = self._save_image.save_images(
            images,
            f"{prefix}/{output_id}",
            prompt,
            extra_pnginfo,
        )
        locators = list(saved.get("ui", {}).get("images", []))

        if len(locators) != batch_size:
            raise RuntimeError(
                f"SaveImage returned {len(locators)} file locators for {batch_size} images"
            )

        artifacts = [
            {"batch_index": index, **dict(locator)} for index, locator in enumerate(locators)
        ]
        payload = {
            "schema_version": CONTRACT_SCHEMA,
            "output_id": output_id,
            "instance_uuid": instance_uuid,
            "role": role,
            "kind": "image",
            "cardinality": cardinality,
            "description": str(description),
            "artifacts": artifacts,
        }

        return {
            "ui": {
                "images": locators,
                "comfyui_image_frontend": [payload],
            }
        }


class CIFPublishText:
    CATEGORY = CATEGORY
    FUNCTION = "publish"
    RETURN_TYPES = ()
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Publishes a text value and its comfyui-image-frontend declaration in prompt history."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"forceInput": True}),
                "output_id": (
                    "STRING",
                    {"default": "text", "multiline": False},
                ),
                "role": (OUTPUT_ROLES,),
                "description": ("STRING", {"default": "", "multiline": True}),
                "instance_uuid": (
                    "STRING",
                    {"default": "", "multiline": False},
                ),
            }
        }

    def publish(
        self,
        text: str,
        output_id: str,
        role: str,
        description: str,
        instance_uuid: str,
    ) -> dict[str, Any]:
        output_id, instance_uuid = _validate_output_declaration(
            output_id=output_id,
            instance_uuid=instance_uuid,
            description=description,
        )
        value = str(text)
        payload = {
            "schema_version": CONTRACT_SCHEMA,
            "output_id": output_id,
            "instance_uuid": instance_uuid,
            "role": role,
            "kind": "text",
            "cardinality": "one",
            "description": str(description),
            "value": value,
        }
        return {
            "ui": {
                "text": [value],
                "comfyui_image_frontend": [payload],
            }
        }


class CIFLoraStack:
    """Apply an ordered, model-only stack using a private, workflow-owned catalog."""

    CATEGORY = CATEGORY
    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "apply_stack"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "catalog_json": ("STRING", {"default": "[]", "multiline": True}),
                "value": ("STRING", {"default": "[]", "multiline": True}),
                "minimum": ("FLOAT", {"default": 0.0}),
                "maximum": ("FLOAT", {"default": 2.0}),
                "step": ("FLOAT", {"default": 0.05}),
                **_common_parameter_inputs(),
            }
        }

    def apply_stack(self, model, catalog_json, value, minimum, maximum, step, **kwargs):
        _validate_parameter_kwargs(kwargs)
        if not isinstance(catalog_json, str) or len(catalog_json) > 100000:
            raise ValueError("Invalid LoRA catalog.")
        if not isinstance(value, str) or len(value) > 20000:
            raise ValueError("Invalid ordered LoRA configuration.")
        catalog = json.loads(catalog_json)
        if not isinstance(catalog, list) or not 1 <= len(catalog) <= 100:
            raise ValueError("Publish between 1 and 100 LoRAs.")
        for item in catalog:
            if not isinstance(item, dict) or set(item) != {"id", "label", "filename"}:
                raise ValueError("Invalid private LoRA catalog entry.")
            filename = item["filename"]
            if not isinstance(filename, str) or not filename or len(filename) > 1000:
                raise ValueError("Invalid private LoRA filename.")
        declaration = {
            "items": [{"id": item["id"], "label": item["label"]} for item in catalog],
            "minimum": minimum,
            "maximum": maximum,
            "step": step,
        }
        configuration = validate_lora_stack(json.loads(value), declaration)
        filenames = {item["id"]: item["filename"] for item in catalog}
        # The loader and model clones belong to this invocation. Zero entries never
        # touch disk, and a subsequent request cannot inherit this request's patches.
        loader = None
        for entry in configuration:
            if entry["strength"] == 0:
                continue
            if loader is None:
                loader = comfy_nodes.LoraLoaderModelOnly()
            model = loader.load_lora_model_only(model, filenames[entry["id"]], entry["strength"])[0]
        return (model,)
