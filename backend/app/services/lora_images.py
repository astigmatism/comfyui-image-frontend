"""Shared LoRA thumbnails: one image per LoRA identity, shown in every workflow."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.lora_identity import item_identities
from ..domain.publication import source_key_for
from ..errors import AppError
from ..models import LoraLibraryImage, WorkflowProfile


def logical_workflow_key(profile: WorkflowProfile) -> str:
    return source_key_for("", profile.source_id) if profile.source_id else str(profile.source_key)


def _declaration(contract: Mapping[str, Any], control_id: str) -> Mapping[str, Any]:
    controls = [
        control
        for control in contract.get("inputs", [])
        if isinstance(control, Mapping)
        and control.get("id") == control_id
        and control.get("type") == "lora_stack"
    ]
    if not controls:
        raise AppError("lora_control_not_found", "LoRA control was not found.", status_code=404)
    return controls[0]


def control_identities(profile: WorkflowProfile, control_id: str) -> dict[str, str]:
    """Public item IDs of one published stack mapped to their shared identities."""

    declaration = _declaration(profile.resolved_contract_json, control_id)
    published = {
        item.get("id")
        for item in declaration.get("items", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    return {
        item_id: identity
        for item_id, identity in item_identities(declaration, profile.source_api_json).items()
        if item_id in published
    }


def image_version(secret: str, lora_identity: str, revision: int) -> str:
    value = f"lora-library-image\0{lora_identity}\0{revision}"
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def image_url(lora_identity: str, version: str) -> str:
    return f"/api/lora-library/images/{quote(lora_identity, safe='')}/content?v={version}"


def library_rows(session: Session, identities: Iterable[str]) -> dict[str, LoraLibraryImage]:
    wanted = sorted(set(identities))
    if not wanted:
        return {}
    return {
        row.lora_identity: row
        for row in session.scalars(
            select(LoraLibraryImage).where(LoraLibraryImage.lora_identity.in_(wanted))
        )
    }


def image_items(
    session: Session,
    *,
    profile: WorkflowProfile,
    control_id: str,
    secret: str,
) -> dict[str, list[dict[str, str | None]]]:
    identities = control_identities(profile, control_id)
    rows = library_rows(session, identities.values())
    items: list[dict[str, str | None]] = []
    for item_id, identity in identities.items():
        row = rows.get(identity)
        version = image_version(secret, identity, row.revision if row else 0)
        items.append(
            {
                "id": item_id,
                "lora_identity": identity,
                "version": version,
                "image_url": image_url(identity, version) if row and row.storage_path else None,
            }
        )
    return {"items": items}


def prune_library_images(session: Session, *, removed: set[str]) -> list[str]:
    """Hide images of removed LoRAs, keeping revision tombstones for stale editors."""

    obsolete_paths: list[str] = []
    for row in library_rows(session, removed).values():
        if row.storage_path:
            obsolete_paths.append(row.storage_path)
            row.storage_path = None
            row.revision += 1
    return obsolete_paths
