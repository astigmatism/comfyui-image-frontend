from __future__ import annotations

import copy

import pytest

from app.domain.contract import (
    ContractError,
    extract_manifest,
    normalized_ui_hash,
    sha256_json,
    validate_profile,
)
from tests.fake_services import build_valid_workflow_pair, build_workflow_files, object_info_fixture


def validate(ui: dict, api: dict):
    return validate_profile(
        basename="profiles/test",
        ui_document=ui,
        api_document=api,
        object_info=object_info_fixture(),
        runtime_capabilities={
            "assets": ["models/fake.safetensors"],
            "capabilities": {"websocket": True},
            "system": {"comfyui_version": "fake"},
        },
    )


def refresh_declared_hashes(ui: dict, api: dict) -> None:
    manifest = ui["nodes"][0]["properties"]["manifest"]
    manifest["workflow"]["api_graph_sha256"] = sha256_json(api)
    _, location = extract_manifest(ui)
    manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(ui, location)


def test_valid_profile_resolves_contract_and_exact_identity() -> None:
    ui, api = build_valid_workflow_pair()
    profile = validate(ui, api)

    assert profile.workflow_id == "fake-progressive-v1"
    assert profile.workflow_version == "1.0.0"
    assert profile.resolved_contract["controls"][0]["id"] == "prompt.text"
    assert profile.resolved_contract["stages"][0]["resolved_node_ids"] == ["4"]
    assert profile.resolved_contract["outputs"][1]["resolved_node_id"] == "5"
    assert len(profile.identity_key.split("|")) == 5


def test_contract_requires_exactly_one_prompt_text() -> None:
    ui, api = build_valid_workflow_pair()
    manifest = ui["nodes"][0]["properties"]["manifest"]
    _, location = extract_manifest(ui)
    manifest["controls"][0]["id"] = "prompt.other"
    manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(ui, location)

    with pytest.raises(ContractError) as exc:
        validate(ui, api)
    assert exc.value.code == "contract_invalid"
    assert "prompt.text" in exc.value.message


def test_selector_cannot_rely_on_node_id_alone() -> None:
    ui, api = build_valid_workflow_pair()
    manifest = ui["nodes"][0]["properties"]["manifest"]
    _, location = extract_manifest(ui)
    manifest["controls"][0]["bindings"][0]["selector"] = {"node_id": "1"}
    manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(ui, location)

    with pytest.raises(ContractError) as exc:
        validate(ui, api)
    assert exc.value.code == "contract_invalid"
    assert "node_id alone" in exc.value.message


@pytest.mark.parametrize(
    ("prefix", "expected_code"),
    [
        ("hash-mismatch", "workflow_hash_mismatch"),
        ("invalid-binding", "binding_not_found"),
        ("missing-dependency", "runtime_dependency_missing"),
    ],
)
def test_invalid_fixture_categories_fail_closed(prefix: str, expected_code: str) -> None:
    files = build_workflow_files()
    ui = copy.deepcopy(files[f"profiles/{prefix}.workflow.json"])
    api = copy.deepcopy(files[f"profiles/{prefix}.api.json"])
    with pytest.raises(ContractError) as exc:
        validate(ui, api)
    assert exc.value.code == expected_code


def test_unknown_top_level_manifest_field_is_rejected() -> None:
    ui, api = build_valid_workflow_pair()
    manifest = ui["nodes"][0]["properties"]["manifest"]
    _, location = extract_manifest(ui)
    manifest["unexpected"] = True
    manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(ui, location)
    with pytest.raises(ContractError) as exc:
        validate(ui, api)
    assert exc.value.code == "contract_invalid"
    assert "Unknown top-level" in exc.value.message


def test_required_asset_fails_closed_when_runtime_inventory_is_empty() -> None:
    ui, api = build_valid_workflow_pair()

    with pytest.raises(ContractError) as exc:
        validate_profile(
            basename="profiles/test",
            ui_document=ui,
            api_document=api,
            object_info={
                key: value
                for key, value in object_info_fixture().items()
                if key != "ModelLoader"
            }
            | {
                "ModelLoader": {
                    "input": {"required": {"model_name": ["STRING"]}}
                }
            },
            runtime_capabilities={"assets": [], "capabilities": {"websocket": True}},
        )

    assert exc.value.code == "asset_missing"


def test_unsupported_contract_schema_line_is_rejected() -> None:
    ui, api = build_valid_workflow_pair()
    manifest = ui["nodes"][0]["properties"]["manifest"]
    _, location = extract_manifest(ui)
    manifest["contract_schema_version"] = "1.2.0"
    manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(ui, location)

    with pytest.raises(ContractError) as exc:
        validate(ui, api)

    assert exc.value.code == "contract_invalid"
    assert "supports 1.1.x" in exc.value.message


def test_required_runtime_feature_fails_closed() -> None:
    ui, api = build_valid_workflow_pair()
    manifest = ui["nodes"][0]["properties"]["manifest"]
    _, location = extract_manifest(ui)
    manifest["requirements"]["runtime"]["features"] = ["checkpoint_events"]
    manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(ui, location)

    with pytest.raises(ContractError) as exc:
        validate(ui, api)

    assert exc.value.code == "runtime_dependency_missing"
    assert "checkpoint_events" in exc.value.message


def test_interactive_branch_is_exposed_as_unavailable_when_off_by_default() -> None:
    ui, api = build_valid_workflow_pair()
    manifest = ui["nodes"][0]["properties"]["manifest"]
    _, location = extract_manifest(ui)
    manifest["branches"].append(
        {
            "id": "manual_inpaint",
            "label": "Manual inpaint",
            "strategy": "interaction_required",
            "default_enabled": False,
        }
    )
    manifest["controls"].append(
        {
            "id": "manual.inpaint",
            "label": "Manual inpaint",
            "type": "boolean",
            "default": False,
            "tier": "advanced",
            "bindings": [{"strategy": "select_branch", "branch_id": "manual_inpaint"}],
        }
    )
    manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(ui, location)

    profile = validate(ui, api)
    control = next(item for item in profile.resolved_contract["controls"] if item["id"] == "manual.inpaint")
    assert control["available"] is False
    assert "interactive workflow" in control["unavailable_reason"]
    assert profile.resolved_contract["branch_states"]["manual_inpaint"]["available"] is False


def test_unavailable_branch_enabled_by_default_rejects_profile() -> None:
    ui, api = build_valid_workflow_pair()
    manifest = ui["nodes"][0]["properties"]["manifest"]
    _, location = extract_manifest(ui)
    manifest["branches"].append(
        {
            "id": "manual_inpaint",
            "label": "Manual inpaint",
            "strategy": "interaction_required",
            "default_enabled": True,
        }
    )
    manifest["workflow"]["ui_graph_sha256"] = normalized_ui_hash(ui, location)

    with pytest.raises(ContractError) as exc:
        validate(ui, api)

    assert exc.value.code == "branch_compilation_failed"


def test_approved_graph_runtime_schema_is_validated_during_registration() -> None:
    ui, api = build_valid_workflow_pair()
    api["5"]["inputs"]["undeclared"] = True
    refresh_declared_hashes(ui, api)

    with pytest.raises(ContractError) as exc:
        validate(ui, api)

    assert exc.value.code == "contract_invalid"
    assert "not declared by the runtime schema" in exc.value.message


def test_every_precompiled_variant_must_preserve_contract_selectors() -> None:
    ui, default_graph = build_valid_workflow_pair()
    variant = copy.deepcopy(default_graph)
    variant["1"]["_meta"]["title"] = "Different Prompt"
    api = {"default": default_graph, "variants": {"alternate": variant}}
    manifest = ui["nodes"][0]["properties"]["manifest"]
    manifest["branches"].append(
        {
            "id": "alternate_graph",
            "label": "Alternate graph",
            "strategy": "precompiled_variant",
            "default_enabled": False,
            "variants": {"false": "alternate", "true": "alternate"},
        }
    )
    refresh_declared_hashes(ui, api)

    with pytest.raises(ContractError) as exc:
        validate(ui, api)

    assert exc.value.code == "binding_not_found"
    assert "API variant 'alternate'" in exc.value.message


def test_graph_transform_paths_are_simulated_and_validated_at_registration() -> None:
    ui, api = build_valid_workflow_pair()
    manifest = ui["nodes"][0]["properties"]["manifest"]
    manifest["branches"][0]["transforms"]["enable"][0]["input"] = "undeclared"
    refresh_declared_hashes(ui, api)

    with pytest.raises(ContractError) as exc:
        validate(ui, api)

    assert exc.value.code == "branch_compilation_failed"
    assert "not declared by the runtime schema" in exc.value.message
