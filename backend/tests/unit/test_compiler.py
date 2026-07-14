from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.domain.compiler import WorkflowCompiler
from app.domain.publication import canonical_json_bytes, validate_publication
from app.errors import AppError
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
        "knpv4_1_strength": 1.0,
    }
    assert result.resolved_seeds == {"seed": "42"}
    assert result.final_prompt == "a quiet lake"
    assert result.compiled_graph["10"]["inputs"]["value"] == "a quiet lake"
    assert result.compiled_graph["11"]["inputs"]["value"] == 512
    assert result.compiled_graph["12"]["inputs"]["value"] == 768
    assert result.compiled_graph["13"]["inputs"]["value"] == 42
    assert result.compiled_graph["14"]["inputs"]["value"] is False
    assert result.compiled_graph["15"]["inputs"]["value"] == 1.0
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
        ("knpv4_1_strength", 2.05),
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
                "knpv4_1_strength": round(index * 0.05, 2),
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
