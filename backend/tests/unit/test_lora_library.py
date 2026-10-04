from __future__ import annotations

import json
from types import SimpleNamespace

from app.domain.lora_identity import generation_lora_usage_v1, lora_identity_v1
from app.services.lora_library import LibraryMember, _canonical, _member, library_key


def _profile(source_id: str, catalog: list[dict], *, published_at: str = "2026-10-01T00:00:00Z"):
    declaration = {
        "id": "loras",
        "type": "lora_stack",
        "items": [{"id": item["id"], "label": item["label"]} for item in catalog],
        "bindings": [{"node_id": "906", "input": "value"}],
    }
    return SimpleNamespace(
        source_id=source_id,
        source_key=source_id.replace("/", "_")[:64],
        published_at=published_at,
        resolved_contract_json={
            "inputs": [declaration],
            "outputs": [{"id": "final", "role": "final", "kind": "image"}],
        },
        source_api_json={
            "906": {"class_type": "CIFLoraStack", "inputs": {"catalog_json": json.dumps(catalog)}}
        },
        manifest_json={"generation_source": {"base_model": {"family": "krea2"}}},
    )


def _members(*profiles) -> list[LibraryMember]:
    members = [_member(profile) for profile in profiles]
    assert all(member is not None for member in members)
    return members  # type: ignore[return-value]


A = {"id": "a", "label": "Alpha", "filename": "cif-managed/a.safetensors"}
B = {"id": "b", "label": "Beta", "filename": "cif-managed/b.safetensors"}
C = {"id": "c", "label": "Gamma", "filename": "cif-managed/c.safetensors"}


def test_identity_is_the_normalized_private_filename():
    assert lora_identity_v1("cif-managed/a.safetensors") == lora_identity_v1(
        "cif-managed\\a.safetensors"
    )
    assert lora_identity_v1("cif-managed/a.safetensors") != lora_identity_v1("a.safetensors")
    assert lora_identity_v1("") is None


def test_canonical_catalog_is_an_ordered_union_led_by_the_largest_member():
    advanced = _profile("workflows/advanced.json", [A, B, C])
    minimal = _profile("workflows/minimal.json", [A], published_at="2026-10-04T00:00:00Z")
    canonical, conflicts = _canonical(_members(minimal, advanced))
    assert conflicts == []
    assert [item["id"] for item in canonical] == ["a", "b", "c"]


def test_newest_publication_supplies_text_and_file_conflicts_are_reported():
    renamed = {**A, "label": "Alpha renamed", "trigger_word": "alpha"}
    older = _profile("workflows/a.json", [A, B], published_at="2026-09-01T00:00:00Z")
    newer = _profile("workflows/b.json", [renamed, B], published_at="2026-10-01T00:00:00Z")
    canonical, conflicts = _canonical(_members(older, newer))
    assert conflicts == []
    assert canonical[0] == renamed
    moved = _profile("workflows/c.json", [{**B, "filename": "other.safetensors"}])
    _, conflicts = _canonical(_members(older, moved))
    assert conflicts == ["LoRA 'Beta' uses different files in different workflows."]
    aliased = _profile("workflows/d.json", [{**A, "id": "alias"}])
    _, conflicts = _canonical(_members(older, aliased))
    assert len(conflicts) == 1 and "One LoRA file" in conflicts[0]


def test_family_scopes_libraries_and_unknown_families_are_grouped():
    assert library_key({"generation_source": {"base_model": {"family": "krea2"}}}) == (
        "krea2",
        "krea2",
    )
    assert library_key({})[0] == "other"
    assert library_key({"generation_source": {"base_model": {"family": "Bad Family"}}})[0] == (
        "other"
    )


def test_generation_usage_keeps_enabled_loras_in_application_order():
    profile = _profile("workflows/advanced.json", [A, B, C])
    usage = generation_lora_usage_v1(
        profile.resolved_contract_json,
        {
            "loras": [
                {"id": "c", "strength": 0.5},
                {"id": "a", "strength": 0},
                {"id": "b", "strength": 1},
            ]
        },
        profile.source_api_json,
    )
    assert [(u.position, u.label, u.strength) for u in usage] == [
        (0, "Gamma", 0.5),
        (1, "Beta", 1.0),
    ]
    assert usage[0].lora_identity == lora_identity_v1(C["filename"])
