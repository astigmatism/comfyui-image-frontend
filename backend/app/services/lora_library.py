"""The shared LoRA library: every LoRA-capable image workflow carries one catalog.

Each publication's frozen ``CIFLoraStack.catalog_json`` stays authoritative for its own
executable graph. The library is the agreement between those catalogs: members of one
base-model family must list the same items (ID, title, trigger, description, file) in the
same order. Library operations change every member together.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.lora_identity import lora_identity_v1, stack_catalog
from ..domain.publication import publication_kind
from ..models import WorkflowProfile, WorkflowState

CATALOG_KEYS = ("id", "label", "filename", "trigger_word", "description")
LIBRARY_KEY = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
MAX_LIBRARY_ITEMS = 100


def revision_of(profile: WorkflowProfile) -> dict[str, str]:
    return {
        "publication_id": str(profile.publication_id),
        "workflow_sha256": profile.ui_graph_sha256,
        "api_sha256": profile.api_graph_sha256,
        "manifest_sha256": str(profile.manifest_sha256),
    }


def library_key(manifest: Any) -> tuple[str, str]:
    """Base-model family of a publication, so LoRAs never cross model families."""

    source = manifest.get("generation_source") if isinstance(manifest, Mapping) else None
    base = source.get("base_model") if isinstance(source, Mapping) else None
    family = base.get("family") if isinstance(base, Mapping) else None
    label = base.get("family_label") if isinstance(base, Mapping) else None
    if isinstance(family, str) and LIBRARY_KEY.fullmatch(family):
        return family, label if isinstance(label, str) and label.strip() else family
    return "other", "Other models"


def normalized_catalog(catalog: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in catalog:
        entry = {key: item[key] for key in CATALOG_KEYS if item.get(key) not in (None, "")}
        result.append(entry)
    return result


@dataclass
class LibraryMember:
    profile: WorkflowProfile
    control: dict[str, Any]
    catalog: list[dict[str, Any]]

    @property
    def source_key(self) -> str:
        return str(self.profile.source_key)

    @property
    def source_id(self) -> str:
        return str(self.profile.source_id)

    @property
    def revision(self) -> dict[str, str]:
        return revision_of(self.profile)


@dataclass
class Library:
    key: str
    label: str
    members: list[LibraryMember] = field(default_factory=list)
    canonical: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return not self.conflicts and all(m.catalog == self.canonical for m in self.members)

    def out_of_sync(self) -> list[LibraryMember]:
        return [member for member in self.members if member.catalog != self.canonical]

    def item(self, lora_id: str) -> dict[str, Any] | None:
        return next((item for item in self.canonical if item["id"] == lora_id), None)

    def expected(self) -> list[dict[str, Any]]:
        return [
            {"source_key": member.source_key, "revision": member.revision}
            for member in self.members
        ]


def _member(profile: WorkflowProfile) -> LibraryMember | None:
    contract = profile.resolved_contract_json
    if publication_kind(contract) != "image" or not profile.source_id:
        return None
    controls = [
        item
        for item in contract.get("inputs", [])
        if isinstance(item, Mapping) and item.get("type") == "lora_stack"
    ]
    if len(controls) != 1:
        return None
    catalog = stack_catalog(controls[0], profile.source_api_json)
    if catalog is None:
        return None
    return LibraryMember(profile, dict(controls[0]), normalized_catalog(catalog))


def _ordered(members: list[LibraryMember]) -> list[LibraryMember]:
    """Largest catalog first, then the newest publication, then a stable path order."""

    ordered = sorted(members, key=lambda member: member.source_id)
    ordered = sorted(
        ordered, key=lambda member: str(member.profile.published_at or ""), reverse=True
    )
    return sorted(ordered, key=lambda member: len(member.catalog), reverse=True)


def _canonical(members: list[LibraryMember]) -> tuple[list[dict[str, Any]], list[str]]:
    conflicts: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    file_owner: dict[str, str] = {}
    order: list[str] = []
    newest = sorted(members, key=lambda m: str(m.profile.published_at or ""), reverse=True)
    newest_rank = {id(member): index for index, member in enumerate(newest)}
    chosen_rank: dict[str, int] = {}
    for member in _ordered(members):
        for item in member.catalog:
            lora_id, filename = item["id"], item.get("filename")
            if lora_id not in by_id:
                by_id[lora_id] = dict(item)
                chosen_rank[lora_id] = newest_rank[id(member)]
                order.append(lora_id)
            elif by_id[lora_id].get("filename") != filename:
                conflicts.append(
                    f"LoRA {by_id[lora_id]['label']!r} uses different files in different workflows."
                )
                continue
            elif newest_rank[id(member)] < chosen_rank[lora_id]:
                # The newest publication supplies the title, trigger and description.
                by_id[lora_id] = dict(item)
                chosen_rank[lora_id] = newest_rank[id(member)]
            if isinstance(filename, str):
                owner = file_owner.setdefault(filename, lora_id)
                if owner != lora_id:
                    conflicts.append(
                        f"One LoRA file is listed as {by_id[owner]['label']!r} and "
                        f"{item['label']!r} in different workflows."
                    )
    canonical = [by_id[lora_id] for lora_id in order]
    if len(canonical) > MAX_LIBRARY_ITEMS:
        conflicts.append(f"The library would exceed {MAX_LIBRARY_ITEMS} LoRAs.")
    return canonical, list(dict.fromkeys(conflicts))


def libraries(session: Session, instance_id: str) -> dict[str, Library]:
    """Current LoRA libraries on the authoritative image instance, by family key."""

    profiles = session.scalars(
        select(WorkflowProfile)
        .where(
            WorkflowProfile.is_current.is_(True),
            WorkflowProfile.state == WorkflowState.VALID,
            WorkflowProfile.source_key.is_not(None),
            WorkflowProfile.instance_id == instance_id,
        )
        .order_by(WorkflowProfile.display_name, WorkflowProfile.source_key)
    )
    result: dict[str, Library] = {}
    for profile in profiles:
        member = _member(profile)
        if member is None:
            continue
        key, label = library_key(profile.manifest_json)
        result.setdefault(key, Library(key=key, label=label)).members.append(member)
    for library in result.values():
        library.canonical, library.conflicts = _canonical(library.members)
    return result


def catalog_identities(catalog: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Public item ID to shared identity for one normalized catalog."""

    result: dict[str, str] = {}
    for item in catalog:
        identity = lora_identity_v1(item.get("filename"))
        if identity:
            result[str(item["id"])] = identity
    return result
