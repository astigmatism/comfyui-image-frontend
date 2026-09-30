import copy
from types import SimpleNamespace

import pytest
from app.domain.compiler import WorkflowCompiler
from app.domain.prompt_generation import (
    LEGACY_DATASET_SEED_KEY,
    adapt_seed,
    collect_text,
)
from app.domain.publication import publication_kind
from app.errors import AppError, ContractError
from tests.publication_fixtures import build_publication_bundle
from tests.unit.test_compiler import publication

# The republished bundle the pinned adapter locked out: ComfyUI mints a new
# publication ID on every publish, and the filename is unchanged.
REPUBLISHED_PUBLICATION = "7c1d94af-2b60-4f0e-9c5a-1de2f7b04c88"
REPUBLISHED_SOURCE_ID = (
    "workflows/comfyui-image-frontend/prompt-generation/StableLlama Erotic Prompts v1.json"
)


def text_profile(source, contract=None):  # type: ignore[no-untyped-def]
    """Build a profile whose identity deliberately differs from any pinned revision."""

    return SimpleNamespace(
        publication_id=REPUBLISHED_PUBLICATION,
        source_id=REPUBLISHED_SOURCE_ID,
        ui_graph_sha256="1" * 64,
        api_graph_sha256="2" * 64,
        manifest_sha256="3" * 64,
        resolved_contract_json=source.private_contract if contract is None else contract,
    )


def recording_compiler(seed):  # type: ignore[no-untyped-def]
    bounds: list[tuple[int, int]] = []

    def resolve(lo: int, hi: int) -> int:
        bounds.append((lo, hi))
        return seed

    return WorkflowCompiler(seed_resolver=resolve), bounds


def compile_text(compiler, source, api_document):  # type: ignore[no-untyped-def]
    return compiler.compile(
        contract=source.private_contract,
        api_document=api_document,
        requested_controls={"subject_name": "Mira"},
    )


def test_text_contract_and_empty_subject_are_distinct_from_missing():
    source = publication(build_publication_bundle("text"))
    assert publication_kind(source.private_contract) == "text"
    compiler = WorkflowCompiler()
    for value in ["", "Mira"]:
        compiled = compiler.compile(
            contract=source.private_contract,
            api_document=source.api_document,
            requested_controls={"subject_name": value},
        )
        assert compiled.effective_controls["subject_name"] == value
    for parameters in [{}, {"subject_name": None}]:
        with pytest.raises(AppError):
            compiler.compile(
                contract=source.private_contract,
                api_document=source.api_document,
                requested_controls=parameters,
            )


@pytest.mark.parametrize("change", ["cardinality", "class", "required"])
def test_invalid_text_publications(change):
    def mutate(manifest, workflow, api):
        if change == "cardinality":
            manifest["interface"]["outputs"][0]["cardinality"] = "many"
        elif change == "class":
            api["130"]["class_type"] = "CIFPublishImage"
        else:
            manifest["interface"]["inputs"][0]["required"] = False

    with pytest.raises(ContractError):
        publication(build_publication_bundle("text", mutate_artifacts=mutate))


def test_adapter_patches_by_structure_regardless_of_publication_identity():
    """A republished bundle keeps working, and only the request-local graph changes."""

    source = publication(build_publication_bundle("text"))
    graph = copy.deepcopy(source.api_document)
    # Deliberately not node "909": recognition must not depend on a node ID.
    graph["950"] = {"class_type": "HFDatasetShuffle", "inputs": {"seed": 7}}
    profile = text_profile(source)
    hashes = []
    for seed in [0, 2**31 - 1]:
        compiler, bounds = recording_compiler(seed)
        compiled = compile_text(compiler, source, graph)
        hashes.append(adapt_seed(profile, compiled, compiler))
        assert bounds[-1] == (0, 2**31 - 1)
        assert compiled.compiled_graph["950"]["inputs"]["seed"] == seed
        assert compiled.resolved_seeds[LEGACY_DATASET_SEED_KEY] == str(seed)
    assert len(set(hashes)) == 2
    assert graph["950"]["inputs"]["seed"] == 7


def test_declared_seed_parameter_is_never_patched_again():
    source = publication(build_publication_bundle("text"))
    graph = copy.deepcopy(source.api_document)
    graph["909"] = {"class_type": "HFDatasetShuffle", "inputs": {"seed": 7}}
    # A published seed parameter owns this binding; the compiler resolved it already.
    profile = text_profile(
        source,
        contract={
            "inputs": [
                {
                    "id": "dataset_seed",
                    "type": "seed",
                    "bindings": [{"node_id": "909", "input": "seed"}],
                }
            ]
        },
    )
    compiler, bounds = recording_compiler(5)
    compiled = compile_text(compiler, source, graph)
    calls = len(bounds)
    assert adapt_seed(profile, compiled, compiler) == compiled.compiled_graph_hash
    assert compiled.compiled_graph["909"]["inputs"]["seed"] == 7
    assert LEGACY_DATASET_SEED_KEY not in compiled.resolved_seeds
    assert len(bounds) == calls


@pytest.mark.parametrize("value", [["905", 0], True, "7", None, 7.0])
def test_unwritable_sampling_seed_fails_visibly_with_actionable_detail(value):
    source = publication(build_publication_bundle("text"))
    graph = copy.deepcopy(source.api_document)
    graph["909"] = {"class_type": "HFDatasetShuffle", "inputs": {"seed": value}}
    profile = text_profile(source)
    compiler, _ = recording_compiler(5)
    compiled = compile_text(compiler, source, graph)
    with pytest.raises(AppError) as raised:
        adapt_seed(profile, compiled, compiler)
    assert raised.value.code == "prompt_adapter_mismatch"
    assert raised.value.status_code == 409
    assert raised.value.details == {
        "node_id": "909",
        "class_type": "HFDatasetShuffle",
        "input": "seed",
        "reason": "seed_input_not_literal",
    }
    assert "literal seed value" in raised.value.message
    assert "node 909 (HFDatasetShuffle)" in raised.value.message
    assert LEGACY_DATASET_SEED_KEY not in compiled.resolved_seeds


def test_text_source_without_a_recognized_sampling_node_is_left_alone():
    source = publication(build_publication_bundle("text"))
    profile = text_profile(source)
    compiler, _ = recording_compiler(5)
    compiled = compile_text(compiler, source, source.api_document)
    assert adapt_seed(profile, compiled, compiler) == compiled.compiled_graph_hash
    assert LEGACY_DATASET_SEED_KEY not in compiled.resolved_seeds


def test_every_recognized_node_is_seeded_under_a_distinct_recorded_key():
    source = publication(build_publication_bundle("text"))
    graph = copy.deepcopy(source.api_document)
    graph["909"] = {"class_type": "HFDatasetShuffle", "inputs": {"seed": 1}}
    graph["910"] = {"class_type": "HFDatasetShuffle", "inputs": {"seed": 2}}
    profile = text_profile(source)
    # The fixture's own declared seed resolves first, then both sampling nodes.
    issued = iter([99, 11, 12])
    bounds: list[tuple[int, int]] = []

    def resolve(lo: int, hi: int) -> int:
        bounds.append((lo, hi))
        return next(issued)

    compiler = WorkflowCompiler(seed_resolver=resolve)
    compiled = compiler.compile(
        contract=source.private_contract,
        api_document=graph,
        requested_controls={"subject_name": "Mira"},
    )
    adapt_seed(profile, compiled, compiler)
    assert bounds[-2:] == [(0, 2**31 - 1), (0, 2**31 - 1)]
    assert compiled.compiled_graph["909"]["inputs"]["seed"] == 11
    assert compiled.compiled_graph["910"]["inputs"]["seed"] == 12
    assert compiled.resolved_seeds[LEGACY_DATASET_SEED_KEY] == "11"
    assert compiled.resolved_seeds["hfdatasetshuffle.910.seed"] == "12"


@pytest.mark.parametrize("value", ["", "  ", 7, ["nested"], "x" * 100001])
def test_text_result_rejects_blank_malformed_and_oversized_values(value):
    source = publication(build_publication_bundle("text"))
    output = source.private_contract["outputs"][0]
    metadata = {
        "output_id": output["id"],
        "instance_uuid": output["instance_uuid"],
        "role": "final",
        "kind": "text",
        "cardinality": "one",
        "value": value,
    }
    with pytest.raises(AppError, match="declared text"):
        collect_text(
            source.private_contract,
            {"outputs": {"130": {"text": [value], "comfyui_image_frontend": [metadata]}}},
        )
