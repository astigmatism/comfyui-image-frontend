"""Presentation-only shared groups; executable catalogs remain in ComfyUI."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import LoraGroup, LoraGroupingRevision, LoraGroupMembership

UNCATEGORIZED = "uncategorized"


def grouping(session: Session, library_key: str, identities: Iterable[str]) -> dict[str, Any]:
    groups = sorted(
        session.scalars(select(LoraGroup).where(LoraGroup.library_key == library_key)),
        key=lambda group: (group.name.casefold(), group.id),
    )
    memberships = {
        row.lora_identity: row.group_id
        for row in session.scalars(
            select(LoraGroupMembership).where(LoraGroupMembership.library_key == library_key)
        )
    }
    version = session.get(LoraGroupingRevision, library_key)
    return {
        "library_key": library_key,
        "revision": version.revision if version else 0,
        "groups": [{"id": group.id, "name": group.name, "system": False} for group in groups]
        + [{"id": UNCATEGORIZED, "name": "Uncategorized", "system": True}],
        "assignments": {
            identity: memberships.get(identity, UNCATEGORIZED) for identity in identities
        },
    }


def check_revision(session: Session, library_key: str, expected: int) -> None:
    # Call after BEGIN IMMEDIATE: the revision check and the complete edit are atomic.
    row = session.get(LoraGroupingRevision, library_key)
    if expected != (row.revision if row else 0):
        raise AppError(
            "lora_group_conflict",
            "Groups changed elsewhere. Review the latest groups and try again.",
            status_code=409,
        )
    if row is None:
        row = LoraGroupingRevision(library_key=library_key, revision=0)
        session.add(row)
    row.revision += 1


def named_group(session: Session, library_key: str, group_id: str) -> LoraGroup:
    group = session.get(LoraGroup, group_id)
    if group is None or group.library_key != library_key:
        raise AppError("lora_group_not_found", "Group was not found.", status_code=404)
    return group


def save_name(session: Session, library_key: str, name: str, group_id: str | None) -> None:
    name = name.strip()
    normalized = name.casefold()
    if not name or len(name) > 80 or normalized == UNCATEGORIZED:
        raise AppError(
            "lora_group_name_invalid",
            "Use a group name of 1-80 characters other than Uncategorized.",
            status_code=422,
        )
    duplicate = session.scalar(
        select(LoraGroup).where(
            LoraGroup.library_key == library_key, LoraGroup.name_normalized == normalized
        )
    )
    if duplicate is not None and duplicate.id != group_id:
        raise AppError("lora_group_name_taken", "That group name is already used.", status_code=409)
    if group_id is None:
        session.add(LoraGroup(library_key=library_key, name=name, name_normalized=normalized))
    else:
        group = named_group(session, library_key, group_id)
        group.name, group.name_normalized = name, normalized


def assign(session: Session, library_key: str, identity: str, group_id: str) -> None:
    row = session.get(LoraGroupMembership, (library_key, identity))
    if group_id == UNCATEGORIZED:
        if row is not None:
            session.delete(row)
        return
    named_group(session, library_key, group_id)
    if row is None:
        session.add(
            LoraGroupMembership(library_key=library_key, lora_identity=identity, group_id=group_id)
        )
    else:
        row.group_id = group_id


def prune_memberships(session: Session, identities: set[str]) -> None:
    affected = set(
        session.scalars(
            select(LoraGroupMembership.library_key).where(
                LoraGroupMembership.lora_identity.in_(identities)
            )
        )
    )
    session.execute(
        delete(LoraGroupMembership).where(LoraGroupMembership.lora_identity.in_(identities))
    )
    for key in affected:
        row = session.get(LoraGroupingRevision, key)
        if row is not None:
            row.revision += 1
