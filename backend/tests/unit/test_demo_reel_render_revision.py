"""What the Demo Reel considers an executable workflow change.

A recipe holds the render revision of the workflow it was saved against, and a mismatch
discards that family's images. Publication naming and widget ranges used to count, so every
republish — ComfyUI stamps the publish date into Save-node output names — cost the whole
model family its pictures, even though the rendered pixels cannot differ.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.models import WorkflowProfile
from app.services.demo_reels import render_revision


def graph() -> dict[str, Any]:
    return {
        "732": {
            "class_type": "SaveImage",
            "_meta": {"title": "Save Image"},
            "inputs": {"images": ["905", 0], "filename_prefix": "image/2026-10-17/Krea2-022532"},
        },
        "906": {
            "class_type": "CIFLoraStack",
            "inputs": {
                "label": "LoRA",
                "description": "Applied in order",
                "group": "LoRA",
                "minimum": 0,
                "maximum": 2,
                "step": 0.05,
                "catalog_json": '[{"id": "lora_old"}]',
                "value": '[{"id": "lora_old", "strength": 0}]',
                "checkpoint": ["900", 0],
            },
        },
        "907": {
            "class_type": "CIFIntParameter",
            "inputs": {"label": "Steps", "minimum": 1, "maximum": 60, "step": 1, "value": 28},
        },
        "908": {"class_type": "KSampler", "inputs": {"steps": 28, "seed": 1234}},
    }


def revision(mutate: Callable[[dict[str, Any]], None] | None = None) -> str:
    source = graph()
    if mutate is not None:
        mutate(source)
    return render_revision(WorkflowProfile(source_api_json=source))


def test_republish_naming_and_widget_ranges_are_not_executable_changes():
    def republished(source: dict[str, Any]) -> None:
        source["732"]["inputs"]["filename_prefix"] = "image/2026-11-14/Krea2-025129"
        source["732"]["_meta"] = {"title": "Renamed on the canvas"}

    def subtracting(source: dict[str, Any]) -> None:
        source["906"]["inputs"].update({"minimum": -1, "maximum": 1, "step": 0.1, "advanced": True})

    def regrown_catalog(source: dict[str, Any]) -> None:
        source["906"]["inputs"]["catalog_json"] = '[{"id": "lora_old"}, {"id": "lora_new"}]'
        source["906"]["inputs"]["value"] = '[{"id": "lora_new", "strength": -1}]'
        source["907"]["inputs"].update({"minimum": 0, "maximum": 120, "step": 2, "label": "Steps"})

    unchanged = revision()
    assert revision(republished) == unchanged
    assert revision(subtracting) == unchanged
    assert revision(regrown_catalog) == unchanged


def test_bindings_and_executable_inputs_still_change_the_render_revision():
    def rewired(source: dict[str, Any]) -> None:
        source["732"]["inputs"]["images"] = ["906", 0]

    def shallower(source: dict[str, Any]) -> None:
        source["908"]["inputs"]["steps"] = 20

    def rebound(source: dict[str, Any]) -> None:
        source["906"]["inputs"]["checkpoint"] = ["901", 0]

    def extended(source: dict[str, Any]) -> None:
        source["909"] = {"class_type": "EmptyLatentImage", "inputs": {"width": 1024}}

    def renamed_class(source: dict[str, Any]) -> None:
        source["908"]["class_type"] = "KSamplerAdvanced"

    unchanged = revision()
    for mutate in (rewired, shallower, rebound, extended, renamed_class):
        assert revision(mutate) != unchanged
