from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.domain.compiler import WorkflowCompiler
from app.domain.publication import canonical_json_bytes, validate_publication
from app.errors import AppError, ContractError
from tests.publication_fixtures import (
    PublicationBundle,
    build_publication_bundle,
    object_info_fixture,
)


def publication(bundle: PublicationBundle | None = None):  # type: ignore[no-untyped-def]
    selected = bundle or build_publication_bundle("krea")
    return validate_publication(
        instance_id="test-instance",
        manifest_path=selected.manifest_path,
        manifest_bytes=selected.manifest_bytes,
        workflow_bytes=selected.workflow_bytes,
        api_bytes=selected.api_bytes,
        object_info=object_info_fixture(),
        manifest_max_bytes=1024 * 1024,
        workflow_max_bytes=1024 * 1024,
        api_max_bytes=1024 * 1024,
    )


def required(prompt: str = "a quiet lake") -> dict[str, object]:
    return {"prompt": prompt, "width": 512, "height": 768}


def test_compile_applies_defaults_random_seed_and_every_trusted_binding_without_mutation() -> None:
    source = publication()
    original = canonical_json_bytes(source.api_document)
    compiler = WorkflowCompiler(seed_resolver=lambda minimum, maximum: 42)

    result = compiler.compile(
        contract=source.private_contract,
        api_document=source.api_document,
        requested_controls=required(),
    )

    assert result.requested_controls == required()
    assert result.effective_controls == {
        "prompt": "a quiet lake",
        "width": 512,
        "height": 768,
        "seed": "42",
        "enable_seedvr2_upscale": False,
        "lora": "knp_v4_1",
        "lora_strength": 1.0,
    }
    assert result.resolved_seeds == {"seed": "42"}
    assert result.final_prompt == "a quiet lake"
    assert result.compiled_graph["10"]["inputs"]["value"] == "a quiet lake"
    assert result.compiled_graph["11"]["inputs"]["value"] == 512
    assert result.compiled_graph["12"]["inputs"]["value"] == 768
    assert result.compiled_graph["13"]["inputs"]["value"] == 42
    assert result.compiled_graph["14"]["inputs"]["value"] is False
    assert result.compiled_graph["15"]["inputs"]["value"] == 1.0
    assert result.compiled_graph["202"]["inputs"]["value"] == "knp_v4_1"
    assert (
        result.compiled_graph["202"]["inputs"]["options_json"]
        == source.api_document["202"]["inputs"]["options_json"]
    )
    assert result.compiled_graph["20"]["inputs"]["lora_name"] == ["202", 0]
    assert canonical_json_bytes(source.api_document) == original
    assert result.compiled_graph is not source.api_document


@pytest.mark.parametrize("seed", [None, "", "random"])
def test_omitted_null_and_explicit_random_seed_resolve_to_one_concrete_value(seed) -> None:  # type: ignore[no-untyped-def]
    source = publication()
    controls = required()
    controls["seed"] = seed
    result = WorkflowCompiler(seed_resolver=lambda minimum, maximum: maximum).compile(
        contract=source.private_contract,
        api_document=source.api_document,
        requested_controls=controls,
    )
    assert result.effective_controls["seed"] == "1125899906842624"
    assert result.compiled_graph["13"]["inputs"]["value"] == 1125899906842624


def test_fixed_maximum_seed_round_trips_as_decimal_string_and_exact_graph_integer() -> None:
    source = publication()
    maximum = "1125899906842624"
    result = WorkflowCompiler().compile(
        contract=source.private_contract,
        api_document=source.api_document,
        requested_controls={**required(), "seed": maximum},
    )
    assert result.resolved_seeds == {"seed": maximum}
    assert result.effective_controls["seed"] == maximum
    assert result.compiled_graph["13"]["inputs"]["value"] == int(maximum)


@pytest.mark.parametrize(
    "controls",
    [
        {"width": 512, "height": 512},
        {"prompt": "x", "height": 512},
        {"prompt": "x", "width": 512},
    ],
)
def test_required_published_parameters_are_enforced(controls: dict[str, object]) -> None:
    source = publication()
    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=source.private_contract,
            api_document=source.api_document,
            requested_controls=controls,
        )
    assert exc.value.code == "parameter_validation_failed"
    assert exc.value.fields


@pytest.mark.parametrize("injected", ["node_id", "bindings", "graph", "userdata_path"])
def test_unknown_and_private_graph_shaped_parameters_are_rejected(injected: str) -> None:
    source = publication()
    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=source.private_contract,
            api_document=source.api_document,
            requested_controls={**required(), injected: {"10": {"inputs": {"value": "owned"}}}},
        )
    assert exc.value.code == "parameter_validation_failed"
    assert exc.value.fields == {injected: "Unknown published parameter."}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("width", 513),
        ("height", True),
        ("enable_seedvr2_upscale", 1),
        ("lora_strength", 2.05),
        ("seed", "01"),
    ],
)
def test_type_bounds_step_and_canonical_seed_validation_are_authoritative(
    field: str, value: object
) -> None:
    source = publication()
    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=source.private_contract,
            api_document=source.api_document,
            requested_controls={**required(), field: value},
        )
    assert exc.value.code == "parameter_validation_failed"
    assert field in exc.value.fields


def test_one_public_seed_can_fan_out_to_multiple_trusted_parameter_nodes() -> None:
    def add_fanout(manifest, workflow, api) -> None:  # type: ignore[no-untyped-def]
        del workflow
        api["16"] = {"class_type": "CIFSeedParameter", "inputs": {"value": 0}}
        manifest["interface"]["inputs"][3]["bindings"].append(
            {"node_id": "16", "input": "value", "class_type": "CIFSeedParameter"}
        )

    source = publication(build_publication_bundle(mutate_artifacts=add_fanout))
    result = WorkflowCompiler().compile(
        contract=source.private_contract,
        api_document=source.api_document,
        requested_controls={**required(), "seed": "123456789"},
    )
    assert result.compiled_graph["13"]["inputs"]["value"] == 123456789
    assert result.compiled_graph["16"]["inputs"]["value"] == 123456789


@pytest.mark.parametrize("include_null", [False, True], ids=["omitted", "null"])
def test_omitted_and_null_optional_choice_resolve_to_public_default(
    include_null: bool,
) -> None:
    source = publication()
    controls = required()
    if include_null:
        controls["lora"] = None
    result = WorkflowCompiler().compile(
        contract=source.private_contract,
        api_document=source.api_document,
        requested_controls=controls,
    )

    assert result.effective_controls["lora"] == "knp_v4_1"
    assert result.effective_controls["lora_strength"] == 1.0
    assert result.compiled_graph["202"]["inputs"]["value"] == "knp_v4_1"


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "retired_option",
        "KNP v3.1",
        "Krea2/KNPV4.1_pre.safetensors",
        1,
    ],
)
def test_choice_accepts_only_current_public_ids_and_reports_only_allowed_ids(
    invalid: object,
) -> None:
    source = publication()
    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=source.private_contract,
            api_document=source.api_document,
            requested_controls={**required(), "lora": invalid},
        )

    allowed = "knp_v4_1, knp_v3_1, knp_v2, mysticxxx_krea2_v1"
    assert exc.value.code == "parameter_validation_failed"
    assert exc.value.fields == {"lora": f"Choose one of: {allowed}."}
    assert "KNP v3.1" not in exc.value.fields["lora"]
    assert ".safetensors" not in exc.value.fields["lora"]


@pytest.mark.parametrize(
    ("controls", "expected_choice", "expected_strength"),
    [
        ({}, "knp_v4_1", 1.0),
        ({"lora": "knp_v3_1"}, "knp_v3_1", 0.5),
        ({"lora": "knp_v3_1", "lora_strength": None}, "knp_v3_1", 0.5),
        ({"lora": "knp_v3_1", "lora_strength": 0.7}, "knp_v3_1", 0.7),
        ({"lora_strength": 0.8}, "knp_v4_1", 0.8),
    ],
)
def test_choice_companion_strength_resolution_precedence(
    controls: dict[str, object], expected_choice: str, expected_strength: float
) -> None:
    source = publication()
    result = WorkflowCompiler().compile(
        contract=source.private_contract,
        api_document=source.api_document,
        requested_controls={**required(), **controls},
    )

    assert result.effective_controls["lora"] == expected_choice
    assert result.effective_controls["lora_strength"] == expected_strength
    assert result.compiled_graph["202"]["inputs"]["value"] == expected_choice
    assert result.compiled_graph["15"]["inputs"]["value"] == expected_strength


def test_choice_without_strength_hint_uses_numeric_manifest_default() -> None:
    source = publication()
    contract = copy.deepcopy(source.private_contract)
    choice = next(value for value in contract["inputs"] if value["id"] == "lora")
    option = next(value for value in choice["choices"] if value["value"] == "mysticxxx_krea2_v1")
    option.pop("default_strength")
    strength = next(value for value in contract["inputs"] if value["id"] == "lora_strength")
    strength["default"] = 0.75

    result = WorkflowCompiler().compile(
        contract=contract,
        api_document=source.api_document,
        requested_controls={**required(), "lora": "mysticxxx_krea2_v1"},
    )
    assert result.effective_controls["lora_strength"] == 0.75
    assert result.compiled_graph["15"]["inputs"]["value"] == 0.75


def test_choice_compiler_refuses_a_binding_other_than_declaration_value() -> None:
    source = publication()
    contract = copy.deepcopy(source.private_contract)
    choice = next(value for value in contract["inputs"] if value["id"] == "lora")
    choice["bindings"][0]["input"] = "options_json"
    original = copy.deepcopy(source.api_document)

    with pytest.raises(ContractError) as exc:
        WorkflowCompiler().compile(
            contract=contract,
            api_document=source.api_document,
            requested_controls={**required(), "lora": "knp_v3_1"},
        )
    assert exc.value.code == "manifest_invalid"
    assert source.api_document == original


@pytest.mark.parametrize("include_null", [False, True], ids=["omitted", "null"])
def test_required_choice_rejects_omission_and_null(include_null: bool) -> None:
    source = publication()
    contract = copy.deepcopy(source.private_contract)
    declaration = next(value for value in contract["inputs"] if value["id"] == "lora")
    declaration["required"] = True
    controls = required()
    if include_null:
        controls["lora"] = None

    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=contract,
            api_document=source.api_document,
            requested_controls=controls,
        )
    assert exc.value.fields == {"lora": "This published parameter is required."}


def test_empty_required_choice_is_an_invalid_id_not_an_omission() -> None:
    source = publication()
    contract = copy.deepcopy(source.private_contract)
    declaration = next(value for value in contract["inputs"] if value["id"] == "lora")
    declaration["required"] = True

    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=contract,
            api_document=source.api_document,
            requested_controls={**required(), "lora": ""},
        )
    assert exc.value.fields["lora"].startswith("Choose one of:")


def test_unique_semantic_role_companion_is_used_without_id_convention() -> None:
    source = publication()
    contract = copy.deepcopy(source.private_contract)
    strength = next(value for value in contract["inputs"] if value["id"] == "lora_strength")
    strength["id"] = "model_influence"

    result = WorkflowCompiler().compile(
        contract=contract,
        api_document=source.api_document,
        requested_controls={**required(), "lora": "knp_v3_1"},
    )
    assert result.effective_controls["model_influence"] == 0.5
    assert result.compiled_graph["15"]["inputs"]["value"] == 0.5


def test_exact_companion_does_not_cross_couple_another_numeric_with_same_role() -> None:
    source = publication()
    contract = copy.deepcopy(source.private_contract)
    graph = copy.deepcopy(source.api_document)
    strength = next(value for value in contract["inputs"] if value["id"] == "lora_strength")
    other_strength = copy.deepcopy(strength)
    other_strength.update(
        {
            "id": "secondary_lora_control",
            "instance_uuid": "00000000-0000-4000-8000-000000000203",
            "default": 0.9,
            "bindings": [
                {
                    "node_id": "203",
                    "input": "value",
                    "class_type": "CIFDecimalParameter",
                }
            ],
        }
    )
    contract["inputs"].append(other_strength)
    graph["203"] = {"class_type": "CIFDecimalParameter", "inputs": {"value": 0.9}}

    result = WorkflowCompiler().compile(
        contract=contract,
        api_document=graph,
        requested_controls={**required(), "lora": "knp_v3_1"},
    )
    assert result.effective_controls["lora_strength"] == 0.5
    assert result.effective_controls["secondary_lora_control"] == 0.9
    assert result.compiled_graph["15"]["inputs"]["value"] == 0.5
    assert result.compiled_graph["203"]["inputs"]["value"] == 0.9


def test_old_source_without_choices_compiles_unchanged() -> None:
    source = publication(build_publication_bundle("generic"))
    result = WorkflowCompiler().compile(
        contract=source.private_contract,
        api_document=source.api_document,
        requested_controls={"prompt": "an old compatible source"},
    )
    assert result.effective_controls == {
        "prompt": "an old compatible source",
        "iterations": 1,
    }
    assert result.compiled_graph["110"]["inputs"]["value"] == "an old compatible source"


def test_simultaneous_compilations_are_isolated_and_cached_graph_stays_identical() -> None:
    source = publication()
    original = copy.deepcopy(source.api_document)

    def compile_one(index: int):  # type: ignore[no-untyped-def]
        return WorkflowCompiler().compile(
            contract=source.private_contract,
            api_document=source.api_document,
            requested_controls={
                **required(f"prompt {index}"),
                "width": 512 + index * 8,
                "seed": str(1000 + index),
                "lora": "knp_v3_1" if index % 2 else "knp_v2",
                "lora_strength": round(index * 0.05, 2),
            },
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(compile_one, range(8)))

    assert source.api_document == original
    assert len({id(value.compiled_graph) for value in results}) == 8
    for index, result in enumerate(results):
        assert result.compiled_graph["10"]["inputs"]["value"] == f"prompt {index}"
        assert result.compiled_graph["11"]["inputs"]["value"] == 512 + index * 8
        assert result.compiled_graph["13"]["inputs"]["value"] == 1000 + index
        expected_choice = "knp_v3_1" if index % 2 else "knp_v2"
        assert result.effective_controls["lora"] == expected_choice
        assert result.compiled_graph["202"]["inputs"]["value"] == expected_choice
        assert result.compiled_graph["15"]["inputs"]["value"] == round(index * 0.05, 2)
        assert (
            result.compiled_graph["202"]["inputs"]["options_json"]
            == source.api_document["202"]["inputs"]["options_json"]
        )
        assert result.compiled_graph["20"]["inputs"]["lora_name"] == ["202", 0]


def test_preset_and_caller_selected_outputs_are_rejected_for_publications() -> None:
    source = publication()
    with pytest.raises(AppError) as exc:
        WorkflowCompiler().compile(
            contract=source.private_contract,
            api_document=source.api_document,
            requested_controls=required(),
            preset_id="legacy",
            requested_outputs=["final_image"],
        )
    assert exc.value.code == "parameter_validation_failed"
    assert set(exc.value.fields) == {"preset_id", "requested_outputs"}
