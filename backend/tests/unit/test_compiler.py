from __future__ import annotations

import copy
import uuid

import pytest

from app.domain.compiler import WorkflowCompiler
from app.domain.contract import validate_profile
from app.errors import AppError
from tests.fake_services import build_valid_workflow_pair, object_info_fixture


def profile():
    ui, api = build_valid_workflow_pair()
    return validate_profile(
        basename="profiles/progressive",
        ui_document=ui,
        api_document=api,
        object_info=object_info_fixture(),
        runtime_capabilities={"assets": ["models/fake.safetensors"]},
    )


def test_compile_resolves_seed_derived_values_branch_and_upload_marker() -> None:
    validated = profile()
    compiler = WorkflowCompiler(seed_resolver=lambda minimum, maximum: 123456)
    upload_id = str(uuid.uuid4())
    result = compiler.compile(
        contract=validated.resolved_contract,
        api_document=validated.api_document,
        object_info=object_info_fixture(),
        requested_controls={
            "prompt.text": "a quiet lake",
            "generation.seed": "random",
            "size.resolution": {"width": 640, "height": 384},
            "post.enabled": False,
            "sampling.steps": 12,
            "source.image": upload_id,
            "model.asset": "models/fake.safetensors",
        },
    )

    assert result.resolved_seeds == {"generation.seed": 123456}
    assert result.effective_controls["generation.seed"] == 123456
    assert result.final_prompt == "a quiet lake"
    assert result.compiled_graph["1"]["inputs"]["text"] == "a quiet lake"
    assert result.compiled_graph["2"]["inputs"]["seed"] == 123456
    assert result.compiled_graph["4"]["inputs"]["width"] == 640
    assert result.compiled_graph["5"]["inputs"]["height"] == 384
    assert result.compiled_graph["5"]["inputs"]["enabled"] is False
    assert result.compiled_graph["6"]["inputs"]["image"]["__app_upload_id__"] == upload_id
    assert len(result.compiled_graph_hash) == 64


def test_compile_expands_preset_and_preserves_requested_controls() -> None:
    validated = profile()
    compiler = WorkflowCompiler(seed_resolver=lambda minimum, maximum: 7)
    result = compiler.compile(
        contract=validated.resolved_contract,
        api_document=validated.api_document,
        object_info=object_info_fixture(),
        requested_controls={"prompt.text": "preset test", "generation.seed": 55},
        preset_id="quick",
    )
    assert result.requested_controls == {"prompt.text": "preset test", "generation.seed": 55}
    assert result.effective_controls["sampling.steps"] == 4
    assert result.effective_controls["size.resolution"] == {"width": 384, "height": 384}
    assert result.resolved_seeds["generation.seed"] == 55


def test_unknown_controls_and_outputs_are_rejected() -> None:
    validated = profile()
    compiler = WorkflowCompiler()
    with pytest.raises(AppError) as control_error:
        compiler.compile(
            contract=validated.resolved_contract,
            api_document=validated.api_document,
            object_info=object_info_fixture(),
            requested_controls={"prompt.text": "x", "unknown.control": True},
        )
    assert control_error.value.code == "control_validation_failed"
    assert control_error.value.fields == {"unknown.control": "Unknown control."}

    with pytest.raises(AppError) as output_error:
        compiler.compile(
            contract=validated.resolved_contract,
            api_document=validated.api_document,
            object_info=object_info_fixture(),
            requested_controls={"prompt.text": "x", "generation.seed": 1},
            requested_outputs=["undeclared"],
        )
    assert output_error.value.code == "control_validation_failed"


def test_seed_out_of_range_is_field_error() -> None:
    validated = profile()
    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=validated.resolved_contract,
            api_document=validated.api_document,
            object_info=object_info_fixture(),
            requested_controls={"prompt.text": "x", "generation.seed": 2**40},
        )
    assert exc.value.code == "control_validation_failed"
    assert "generation.seed" in exc.value.fields


def test_compiled_graph_rejects_unknown_runtime_input_and_dangling_link() -> None:
    validated = profile()
    compiler = WorkflowCompiler(seed_resolver=lambda minimum, maximum: 7)

    graph_with_unknown = copy.deepcopy(validated.api_document)
    graph_with_unknown["1"]["inputs"]["not_in_runtime_schema"] = "x"
    with pytest.raises(AppError) as unknown_exc:
        compiler.compile(
            contract=validated.resolved_contract,
            api_document=graph_with_unknown,
            object_info=object_info_fixture(),
            requested_controls={"prompt.text": "x", "generation.seed": 1},
        )
    assert unknown_exc.value.code == "branch_compilation_failed"

    graph_with_dangling_link = copy.deepcopy(validated.api_document)
    graph_with_dangling_link["4"]["inputs"]["prompt"] = ["999", 0]
    with pytest.raises(AppError) as link_exc:
        compiler.compile(
            contract=validated.resolved_contract,
            api_document=graph_with_dangling_link,
            object_info=object_info_fixture(),
            requested_controls={"prompt.text": "x", "generation.seed": 1},
        )
    assert link_exc.value.code == "branch_compilation_failed"
    assert "missing node 999" in link_exc.value.message


def test_compiled_graph_rejects_missing_required_runtime_input() -> None:
    validated = profile()
    runtime = copy.deepcopy(object_info_fixture())
    runtime["ModelLoader"]["input"]["required"]["runtime_required"] = ["STRING"]

    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=validated.resolved_contract,
            api_document=validated.api_document,
            object_info=runtime,
            requested_controls={"prompt.text": "x", "generation.seed": 1},
        )

    assert exc.value.code == "branch_compilation_failed"
    assert "missing required inputs" in exc.value.message


def test_unavailable_interactive_branch_cannot_be_selected() -> None:
    ui, api = build_valid_workflow_pair()
    manifest = ui["nodes"][0]["properties"]["manifest"]
    from app.domain.contract import extract_manifest, normalized_ui_hash

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
    validated = validate_profile(
        basename="profiles/manual",
        ui_document=ui,
        api_document=api,
        object_info=object_info_fixture(),
        runtime_capabilities={"assets": ["models/fake.safetensors"]},
    )

    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=validated.resolved_contract,
            api_document=validated.api_document,
            object_info=object_info_fixture(),
            requested_controls={
                "prompt.text": "x",
                "generation.seed": 1,
                "manual.inpaint": True,
            },
        )

    assert exc.value.code == "control_validation_failed"
    assert "manual.inpaint" in exc.value.fields
