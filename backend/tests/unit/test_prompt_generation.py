import copy
from types import SimpleNamespace

import pytest
from app.domain.compiler import WorkflowCompiler
from app.domain.prompt_generation import (
    STABLELLAMA_HASHES,
    STABLELLAMA_PUBLICATION,
    adapt_seed,
    collect_text,
)
from app.domain.publication import publication_kind
from app.errors import AppError, ContractError
from tests.publication_fixtures import build_publication_bundle
from tests.unit.test_compiler import publication


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


def test_adapter_is_pinned_and_changes_only_request_local_seed():
    source = publication(build_publication_bundle("text"))
    graph = copy.deepcopy(source.api_document)
    graph["909"] = {"class_type": "HFDatasetShuffle", "inputs": {"seed": 7}}
    profile = SimpleNamespace(
        publication_id=STABLELLAMA_PUBLICATION,
        source_id="fixture",
        ui_graph_sha256=STABLELLAMA_HASHES[0],
        api_graph_sha256=STABLELLAMA_HASHES[1],
        manifest_sha256=STABLELLAMA_HASHES[2],
    )
    hashes = []
    for seed in [0, 2**31 - 1]:
        bounds = []

        def resolve(lo, hi, selected=seed, recorded=bounds):
            recorded.append((lo, hi))
            return selected

        compiler = WorkflowCompiler(seed_resolver=resolve)
        compiled = compiler.compile(
            contract=source.private_contract,
            api_document=graph,
            requested_controls={"subject_name": "Mira"},
        )
        hashes.append(adapt_seed(profile, compiled, compiler))
        assert bounds[-1] == (0, 2**31 - 1)
        assert compiled.compiled_graph["909"]["inputs"]["seed"] == seed
        assert compiled.resolved_seeds["stablellama.dataset_seed"] == str(seed)
    assert len(set(hashes)) == 2
    assert graph["909"]["inputs"]["seed"] == 7
    for field in ["ui_graph_sha256", "api_graph_sha256", "manifest_sha256"]:
        changed = copy.copy(profile)
        setattr(changed, field, "0" * 64)
        with pytest.raises(AppError, match="seed adapter"):
            adapt_seed(changed, compiled, compiler)
    compiled.compiled_graph["909"]["class_type"] = "UnexpectedNode"
    with pytest.raises(AppError, match="seed adapter"):
        adapt_seed(profile, compiled, compiler)


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
