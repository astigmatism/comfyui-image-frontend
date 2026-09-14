"""Prepare Moody Krea2 Simple v31's editable graph; never save or execute remotely.

Usage: python scripts/lora/prepare_workflow.py ROLLBACK_DIRECTORY OUTPUT_DIRECTORY
The input directory must contain the current adjacent three-file publication.
The output is a candidate editable workflow and API graph for offline inspection.
Load the editable candidate in ComfyUI and use Save & Publish after installation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import uuid
from pathlib import Path

STEM = "Moody Krea2 Simple v31"
PUBLIC_LORAS = (
    ("spread", "Spread"),
    ("claire", "Claire"),
    ("nexblend", "NexBlend08"),
    ("tifa", "Tifa"),
)


def prepare(workflow, api):
    workflow, api = copy.deepcopy(workflow), copy.deepcopy(api)
    old = api["822"]
    if old["class_type"] != "Power Lora Loader (rgthree)":
        raise ValueError("Expected the original main rgthree loader at node 822.")
    node = next(node for node in workflow["nodes"] if node["id"] == 822)
    if node["outputs"][1].get("links"):
        raise ValueError("Main loader CLIP output is now used; review the graph before converting.")
    destinations = sorted(
        (key, name)
        for key, item in api.items()
        for name, value in item["inputs"].items()
        if value == ["822", 0]
    )
    if destinations != [("599", "model"), ("663", "model"), ("842", "model")]:
        raise ValueError("Main model destinations changed; review the graph before converting.")
    catalog = [
        {"id": public_id, "label": label, "filename": old["inputs"][f"lora_{index}"]["lora"]}
        for index, (public_id, label) in enumerate(PUBLIC_LORAS, 1)
    ]
    ui_filenames = [
        value["lora"]
        for value in node["widgets_values"]
        if isinstance(value, dict) and "lora" in value
    ]
    if ui_filenames != [entry["filename"] for entry in catalog]:
        raise ValueError("Editable and frozen LoRA catalogs differ.")
    values = {
        "catalog_json": json.dumps(catalog, ensure_ascii=False),
        "value": json.dumps([{"id": item["id"], "strength": 0} for item in catalog]),
        "minimum": 0.0,
        "maximum": 2.0,
        "step": 0.05,
        "parameter_id": "loras",
        "instance_uuid": str(uuid.uuid4()),
        "label": "LoRAs",
        "description": "Apply LoRAs from top to bottom. Zero strength skips a LoRA.",
        "semantic_role": "lora",
        "required": False,
        "advanced": False,
        "group": "LoRAs",
        "order": 150,
    }
    clip_link = next(item["link"] for item in node["inputs"] if item["name"] == "clip")
    for other in workflow["nodes"]:
        for output in other.get("outputs", []):
            if clip_link in (output.get("links") or []):
                output["links"].remove(clip_link)
    workflow["links"] = [link for link in workflow["links"] if link[0] != clip_link]
    model_input = next(item for item in node["inputs"] if item["name"] == "model")
    types = {
        "minimum": "FLOAT",
        "maximum": "FLOAT",
        "step": "FLOAT",
        "order": "INT",
        "semantic_role": "COMBO",
        "required": "BOOLEAN",
        "advanced": "BOOLEAN",
    }
    node.update(
        type="CIFLoraStack",
        title="CIF — Ordered LoRAs",
        size=[560, 850],
        inputs=[
            model_input,
            *[
                {
                    "name": key,
                    "type": types.get(key, "STRING"),
                    "widget": {"name": key},
                    "link": None,
                }
                for key in values
            ],
        ],
        outputs=[{**node["outputs"][0], "name": "model"}],
        widgets_values=list(values.values()),
        properties={
            "cif_contract_schema": "comfyui-image-frontend.interface/v1",
            "Node name for S&R": "CIFLoraStack",
        },
    )
    api["822"] = {
        "class_type": "CIFLoraStack",
        "inputs": {"model": old["inputs"]["model"], **values},
        "_meta": {"title": "CIF — Ordered LoRAs"},
    }
    return workflow, api


def main():
    source, destination = map(Path, sys.argv[1:])
    manifest = json.loads((source / f"{STEM}.interface.json").read_bytes())
    documents = []
    for suffix, key in ((".json", "workflow"), (".api.json", "api")):
        data = (source / f"{STEM}{suffix}").read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest[key]["sha256"]:
            raise ValueError(f"Rollback {key} bytes do not match their publication hash.")
        documents.append(json.loads(data))
    workflow, api = prepare(*documents)
    destination.mkdir(parents=True, exist_ok=True)
    for suffix, document in ((".json", workflow), (".api.json", api)):
        path = destination / f"{STEM}{suffix}"
        with path.open("x") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(
        "Prepared editable workflow and candidate API graph; no publication or execution performed."
    )


if __name__ == "__main__":
    main()
