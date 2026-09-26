from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.publication import source_key_for
from ..errors import AppError
from ..models import LoraImage, WorkflowProfile


def logical_workflow_key(profile: WorkflowProfile) -> str:
    return source_key_for("", profile.source_id) if profile.source_id else str(profile.source_key)


def catalog_bindings(
    contract: Mapping[str, Any], api_document: Mapping[str, Any]
) -> dict[tuple[str, str], str]:
    """Map public control/item IDs to hashes of their frozen private model filenames."""

    result: dict[tuple[str, str], str] = {}
    for control in contract.get("inputs", []):
        if not isinstance(control, Mapping) or control.get("type") != "lora_stack":
            continue
        control_id = control.get("id")
        bindings = control.get("bindings")
        if not isinstance(control_id, str) or not isinstance(bindings, list) or not bindings:
            continue
        first = bindings[0]
        if not isinstance(first, Mapping):
            continue
        node = api_document.get(str(first.get("node_id")))
        inputs = node.get("inputs") if isinstance(node, Mapping) else None
        raw_catalog = inputs.get("catalog_json") if isinstance(inputs, Mapping) else None
        if not isinstance(raw_catalog, str):
            continue
        try:
            catalog = json.loads(raw_catalog)
        except ValueError:
            continue
        if not isinstance(catalog, list):
            continue
        public_ids = {
            item.get("id")
            for item in control.get("items", [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        for item in catalog:
            if not isinstance(item, Mapping):
                continue
            item_id, filename = item.get("id"), item.get("filename")
            if item_id in public_ids and isinstance(item_id, str) and isinstance(filename, str):
                result[(control_id, item_id)] = hashlib.sha256(filename.encode()).hexdigest()
    return result


def control_bindings(profile: WorkflowProfile, control_id: str) -> dict[str, str]:
    controls = [
        control
        for control in profile.resolved_contract_json.get("inputs", [])
        if isinstance(control, dict)
        and control.get("id") == control_id
        and control.get("type") == "lora_stack"
    ]
    if not controls:
        raise AppError("lora_control_not_found", "LoRA control was not found.", status_code=404)
    full = catalog_bindings(profile.resolved_contract_json, profile.source_api_json)
    bindings = {
        item_id: binding
        for (candidate_control, item_id), binding in full.items()
        if candidate_control == control_id
    }
    if not bindings:
        raise AppError("lora_control_unavailable", "LoRA catalog is unavailable.", status_code=409)
    return bindings


def image_version(
    secret: str,
    workflow_key: str,
    control_id: str,
    item_id: str,
    revision: int,
    binding_hash: str,
) -> str:
    value = f"{workflow_key}\0{control_id}\0{item_id}\0{revision}\0{binding_hash}"
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def image_items(
    session: Session,
    *,
    profile: WorkflowProfile,
    control_id: str,
    secret: str,
) -> dict[str, list[dict[str, str | None]]]:
    bindings = control_bindings(profile, control_id)
    workflow_key = logical_workflow_key(profile)
    rows = {
        row.item_id: row
        for row in session.scalars(
            select(LoraImage).where(
                LoraImage.workflow_key == workflow_key,
                LoraImage.control_id == control_id,
            )
        )
    }
    items: list[dict[str, str | None]] = []
    for item_id, binding_hash in bindings.items():
        row = rows.get(item_id)
        version = image_version(
            secret,
            workflow_key,
            control_id,
            item_id,
            row.revision if row else 0,
            binding_hash,
        )
        has_image = bool(row and row.binding_hash == binding_hash and row.storage_path)
        items.append(
            {
                "id": item_id,
                "version": version,
                "image_url": (
                    f"/api/workflows/{quote(str(profile.source_key), safe='')}/lora-images/"
                    f"{quote(control_id, safe='')}/{quote(item_id, safe='')}/content?v={version}"
                    if has_image
                    else None
                ),
            }
        )
    return {"items": items}


def prune_lora_images(
    session: Session,
    *,
    workflow_key: str,
    current_bindings: Mapping[tuple[str, str], str],
) -> list[str]:
    """Hide removed or rebound images and retain revision tombstones for stale editors."""

    obsolete_paths: list[str] = []
    for row in session.scalars(select(LoraImage).where(LoraImage.workflow_key == workflow_key)):
        current = current_bindings.get((row.control_id, row.item_id))
        if current is None or current != row.binding_hash:
            if row.storage_path:
                obsolete_paths.append(row.storage_path)
                row.storage_path = None
                row.revision += 1
            if current is not None:
                row.binding_hash = current
    return obsolete_paths
