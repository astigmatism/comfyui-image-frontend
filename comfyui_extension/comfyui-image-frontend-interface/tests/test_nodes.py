from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
INSTANCE_UUID = "12345678-1234-5678-1234-567812345678"
CHOICE_OPTIONS = json.dumps(
    [
        {
            "id": "knp_v4_1",
            "label": "KNP v4.1",
            "binding": "Krea2/KNPV4.1_pre.safetensors",
            "default_strength": 1.0,
        },
        {
            "id": "knp_v3_1",
            "label": "KNP v3.1",
            "binding": "Krea2/KNPV3_1.safetensors",
            "default_strength": 0.5,
        },
    ]
)


class FakeSaveImage:
    def __init__(self):
        self.calls = []

    def save_images(self, images, filename_prefix, prompt, extra_pnginfo):
        self.calls.append((images, filename_prefix, prompt, extra_pnginfo))
        return {
            "ui": {
                "images": [
                    {
                        "filename": f"image_{index}.png",
                        "subfolder": "comfyui-image-frontend/final",
                        "type": "output",
                    }
                    for index, _ in enumerate(images)
                ]
            }
        }


class FakeImageBatch:
    def __init__(self, shape=(1, 480, 640, 3)):
        self.shape = shape


class FakeLoadImage:
    calls = []  # noqa: RUF012 - shared fake call recorder
    batch_shape = (1, 480, 640, 3)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": (["reference.png"], {"image_upload": True})}}

    @classmethod
    def VALIDATE_INPUTS(cls, image):
        return True if image == "reference.png" else f"Invalid image file: {image}"

    @classmethod
    def IS_CHANGED(cls, image):
        return f"hash:{image}"

    def load_image(self, image):
        self.calls.append(image)
        return FakeImageBatch(self.batch_shape), object()


def load_module():
    fake_nodes = types.ModuleType("nodes")
    fake_nodes.SaveImage = FakeSaveImage
    fake_nodes.LoadImage = FakeLoadImage
    sys.modules["nodes"] = fake_nodes

    fake_folder_paths = types.ModuleType("folder_paths")
    fake_folder_paths.get_annotated_filepath = lambda image: __file__
    sys.modules["folder_paths"] = fake_folder_paths

    fake_pil = types.ModuleType("PIL")
    fake_pil.Image = object()
    fake_pil.UnidentifiedImageError = OSError
    sys.modules["PIL"] = fake_pil

    package = types.ModuleType("cif_nodes_under_test")
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules["cif_nodes_under_test"] = package
    spec = importlib.util.spec_from_file_location(
        "cif_nodes_under_test.nodes",
        PACKAGE_ROOT / "nodes.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


nodes = load_module()
nodes._inspect_reference_image = lambda image_path: (640, 480)


def declaration(**overrides):
    values = {
        "parameter_id": "steps",
        "instance_uuid": INSTANCE_UUID,
        "label": "Steps",
        "description": "Sampling steps",
        "semantic_role": "steps",
        "required": False,
        "advanced": True,
        "group": "Advanced",
        "order": 100,
    }
    values.update(overrides)
    return values


class InterfaceTests(unittest.TestCase):
    def test_common_interface_is_prompt_local_passthrough(self):
        result = nodes.CIFImageFrontendInterface().expose(
            "positive",
            "negative",
            42,
            1024,
            1536,
            2,
            False,
            "main",
            "1.0.0",
            INSTANCE_UUID,
        )
        self.assertEqual(
            result,
            ("positive", "negative", 42, 1024, 1536, 2, False),
        )

    def test_parameter_id_must_be_safe_public_name(self):
        with self.assertRaisesRegex(ValueError, "parameter_id"):
            nodes.CIFTextParameter().expose(
                "value",
                **declaration(parameter_id="private.node.42"),
            )

    def test_blank_authoring_uuid_does_not_block_execution(self):
        result = nodes.CIFTextParameter().expose(
            "a default prompt",
            **declaration(instance_uuid="", parameter_id="prompt"),
        )
        self.assertEqual(result, ("a default prompt",))

    def test_malformed_nonempty_uuid_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "instance_uuid"):
            nodes.CIFTextParameter().expose(
                "a default prompt",
                **declaration(instance_uuid="not-a-uuid", parameter_id="prompt"),
            )

    def test_integer_contract_enforces_bounds(self):
        with self.assertRaisesRegex(ValueError, "between minimum and maximum"):
            nodes.CIFIntegerParameter().expose(
                101,
                0,
                100,
                1,
                **declaration(),
            )

    def test_seed_parameter_declares_numeric_contract(self):
        result = nodes.CIFSeedParameter().expose(
            42,
            0,
            nodes.MAX_SEED,
            1,
            "random",
            **declaration(parameter_id="seed", semantic_role="seed"),
        )
        self.assertEqual(result, (42,))

    def test_seed_parameter_defaults_to_native_local_randomization(self):
        required = nodes.CIFSeedParameter.INPUT_TYPES()["required"]
        self.assertTrue(required["value"][1]["control_after_generate"])
        self.assertEqual(required["default_mode"][0][0], "random")

    def test_choice_parameter_maps_public_id_to_private_combo_binding(self):
        result = nodes.CIFChoiceParameter().expose(
            "knp_v3_1",
            CHOICE_OPTIONS,
            **declaration(parameter_id="lora", semantic_role="lora"),
        )
        self.assertEqual(result, ("Krea2/KNPV3_1.safetensors",))
        self.assertEqual(nodes.CIFChoiceParameter.RETURN_TYPES, ("*",))

    def test_choice_parameter_rejects_undeclared_public_value(self):
        with self.assertRaisesRegex(ValueError, "not in options_json"):
            nodes.CIFChoiceParameter().expose(
                "unknown_lora",
                CHOICE_OPTIONS,
                **declaration(parameter_id="lora", semantic_role="lora"),
            )

    def test_choice_parameter_rejects_duplicate_choice_ids(self):
        duplicate_options = json.dumps(
            [
                {"id": "same", "label": "First", "binding": "one"},
                {"id": "same", "label": "Second", "binding": "two"},
            ]
        )
        with self.assertRaisesRegex(ValueError, "duplicate choice id"):
            nodes.CIFChoiceParameter().expose(
                "same",
                duplicate_options,
                **declaration(parameter_id="choice"),
            )

    def test_choice_parameter_rejects_nonfinite_default_strength(self):
        invalid_options = (
            '[{"id":"choice","label":"Choice","binding":"value","default_strength":1e999}]'
        )
        with self.assertRaisesRegex(ValueError, "default_strength"):
            nodes.CIFChoiceParameter().expose(
                "choice",
                invalid_options,
                **declaration(parameter_id="choice"),
            )

    def test_image_parameter_reuses_native_upload_widget_and_returns_image_mask(self):
        image_input = nodes.CIFImageParameter.INPUT_TYPES()["required"]["image"]
        self.assertTrue(image_input[1]["image_upload"])
        result = nodes.CIFImageParameter().load_image(
            "reference.png",
            1024 * 1024,
            1024,
            1024,
            **declaration(
                parameter_id="reference_image",
                semantic_role="reference_image",
                required=True,
            ),
        )
        self.assertEqual(result[0].shape, (1, 480, 640, 3))
        self.assertEqual(nodes.CIFImageParameter.RETURN_TYPES, ("IMAGE", "MASK"))

    def test_image_parameter_rejects_multiple_frames(self):
        original_shape = FakeLoadImage.batch_shape
        FakeLoadImage.batch_shape = (2, 480, 640, 3)
        try:
            with self.assertRaisesRegex(ValueError, "exactly one static frame"):
                nodes.CIFImageParameter().load_image(
                    "reference.png",
                    1024 * 1024,
                    1024,
                    1024,
                    **declaration(
                        parameter_id="reference_image",
                        semantic_role="reference_image",
                        required=True,
                    ),
                )
        finally:
            FakeLoadImage.batch_shape = original_shape

    def test_image_parameter_rejects_excess_dimensions(self):
        with self.assertRaisesRegex(ValueError, "exceeding the declared"):
            nodes.CIFImageParameter().load_image(
                "reference.png",
                1024 * 1024,
                320,
                320,
                **declaration(
                    parameter_id="reference_image",
                    semantic_role="reference_image",
                    required=True,
                ),
            )

    def test_image_publisher_uses_builtin_saver_and_keeps_batch(self):
        publisher = nodes.CIFPublishImage()
        result = publisher.publish(
            [object(), object()],
            "final",
            "final",
            "Final render",
            "many",
            "comfyui-image-frontend",
            INSTANCE_UUID,
            {"node": "prompt"},
            {"workflow": {"version": 0.4}},
        )

        self.assertEqual(
            publisher._save_image.calls[0][1],
            "comfyui-image-frontend/final",
        )
        ui = result["ui"]
        self.assertEqual(len(ui["images"]), 2)
        contract = ui["comfyui_image_frontend"][0]
        self.assertEqual(contract["schema_version"], nodes.CONTRACT_SCHEMA)
        self.assertEqual(contract["output_id"], "final")
        self.assertEqual(
            [artifact["batch_index"] for artifact in contract["artifacts"]],
            [0, 1],
        )

    def test_one_cardinality_rejects_batch(self):
        with self.assertRaisesRegex(ValueError, "cardinality one"):
            nodes.CIFPublishImage().publish(
                [object(), object()],
                "final",
                "final",
                "Final render",
                "one",
                "comfyui-image-frontend",
                INSTANCE_UUID,
            )

    def test_text_publisher_emits_namespaced_history_value(self):
        result = nodes.CIFPublishText().publish(
            "hello",
            "caption",
            "auxiliary",
            "Generated caption",
            INSTANCE_UUID,
        )
        self.assertEqual(result["ui"]["text"], ["hello"])
        self.assertEqual(
            result["ui"]["comfyui_image_frontend"][0]["value"],
            "hello",
        )


if __name__ == "__main__":
    unittest.main()
