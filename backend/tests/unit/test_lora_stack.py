from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from app.api.workflows import _public_interface
from app.domain.compiler import WorkflowCompiler
from app.domain.publication import validate_publication
from app.errors import AppError, ContractError
from tests.publication_fixtures import add_lora_stack, build_publication_bundle, object_info_fixture

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "comfyui_extension/comfyui-image-frontend-interface"


def source(mutate=None):
    def change(manifest, workflow, api):
        add_lora_stack(manifest, workflow, api)
        if mutate:
            mutate(manifest["interface"]["inputs"][-1], api["99"]["inputs"])

    bundle = build_publication_bundle(mutate_artifacts=change)
    return validate_publication(
        instance_id="test",
        manifest_path=bundle.manifest_path,
        manifest_bytes=bundle.manifest_bytes,
        workflow_bytes=bundle.workflow_bytes,
        api_bytes=bundle.api_bytes,
        object_info=object_info_fixture(),
        manifest_max_bytes=1024**2,
        workflow_max_bytes=1024**2,
        api_max_bytes=1024**2,
    )


def compile_stack(selected, **parameters):
    return WorkflowCompiler().compile(
        contract=selected.private_contract,
        api_document=selected.api_document,
        requested_controls={"prompt": "lake", "width": 512, "height": 768, **parameters},
    )


def test_order_default_private_projection_and_isolation():
    selected = source()
    default = [{"id": "a", "strength": 0}, {"id": "b", "strength": 0}]
    assert compile_stack(selected).effective_controls["loras"] == default
    requested = [{"id": "b", "strength": 2}, {"id": "a", "strength": 0.05}]
    before = copy.deepcopy(selected.api_document)
    with ThreadPoolExecutor() as pool:
        results = list(pool.map(lambda _: compile_stack(selected, loras=requested), range(8)))
    result = results[0]
    assert result.requested_controls["loras"] == requested
    assert result.effective_controls["loras"] == requested
    assert json.loads(result.compiled_graph["99"]["inputs"]["value"]) == requested
    result.effective_controls["loras"][0]["strength"] = 0
    assert results[1].effective_controls["loras"] == requested
    assert selected.api_document == before
    for interface in (selected.public_interface, _public_interface(selected.private_contract)):
        serialized = json.dumps(interface)
        assert "private/" not in serialized
        assert "catalog_json" not in serialized
        assert "bindings" not in serialized
        assert interface["inputs"][-1]["items"] == [
            {"id": "a", "label": "Alpha"},
            {"id": "b", "label": "Beta"},
        ]


@pytest.mark.parametrize(
    "value",
    [
        None,
        "[]",
        [],
        [{"id": "a", "strength": 1}],
        [{"id": "a", "strength": 0}, {"id": "a", "strength": 0}],
        [{"id": "unknown", "strength": 0}, {"id": "b", "strength": 0}],
        *[
            [{"id": "a", "strength": n}, {"id": "b", "strength": 0}]
            for n in (True, "1", float("nan"), float("inf"), -0.05, 2.05, 0.051)
        ],
        [{"id": "a", "strength": 0, "filename": "injected"}, {"id": "b", "strength": 0}],
    ],
)
def test_malformed_requests(value):
    with pytest.raises(AppError) as exc:
        compile_stack(source(), loras=value)
    assert "loras" in exc.value.fields


@pytest.mark.parametrize(
    "mutate",
    [
        lambda public, private: public["items"][0].update(filename="private"),
        lambda public, private: public["items"][0].update(label="Changed"),
        lambda public, private: public.update(step=0.1),
        lambda public, private: public["default"][0].update(strength=0.05),
        lambda public, private: public["bindings"][0].update(input="catalog_json"),
        lambda public, private: private.update(
            value='[{"id":"a","strength":1},{"id":"b","strength":0}]'
        ),
        lambda public, private: public.update(required=True),
    ],
)
def test_publication_rejects_drift_and_private_fields(mutate):
    with pytest.raises(ContractError):
        source(mutate)


def test_companion_contract_is_identical_and_legacy_nodes_pass():
    backend = (
        (ROOT / "backend/app/domain/lora_stack.py")
        .read_text()
        .split("\n\ndef validate_lora_runtime")[0]
        .rstrip()
    )
    assert (PACKAGE / "lora_stack.py").read_text().rstrip() == backend
    subprocess.run(  # noqa: S603 - repository-owned tests in a separate interpreter
        [sys.executable, "-m", "unittest", "discover", "-s", str(PACKAGE / "tests")], check=True
    )


@pytest.fixture
def node_module(monkeypatch):
    package = types.ModuleType("cif_lora_test")
    package.__path__ = [str(PACKAGE)]
    monkeypatch.setitem(sys.modules, "cif_lora_test", package)
    monkeypatch.setitem(sys.modules, "folder_paths", types.ModuleType("folder_paths"))
    fake = types.ModuleType("nodes")
    monkeypatch.setitem(sys.modules, "nodes", fake)
    spec = importlib.util.spec_from_file_location("cif_lora_test.nodes", PACKAGE / "nodes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, fake


@pytest.mark.parametrize(
    "value,expected",
    [
        ([{"id": "a", "strength": 0}, {"id": "b", "strength": 0}], []),
        ([{"id": "a", "strength": 0.5}, {"id": "b", "strength": 0}], [("a", 0.5)]),
        ([{"id": "b", "strength": 2}, {"id": "a", "strength": 0.05}], [("b", 2), ("a", 0.05)]),
        ([{"id": "a", "strength": 1}, {"id": "b", "strength": 2}], [("a", 1), ("b", 2)]),
    ],
)
def test_native_loader_order_zero_and_request_isolation(node_module, value, expected):
    module, fake = node_module
    creations = []

    class Loader:
        def __init__(self):
            creations.append(self)

        def load_lora_model_only(self, model, filename, strength):
            return ([*model, (filename, strength)],)

    fake.LoraLoaderModelOnly = Loader
    selected = source()
    inputs = copy.deepcopy(selected.api_document["99"]["inputs"])
    inputs.pop("model")
    inputs["catalog_json"] = json.dumps(
        [{"id": key, "label": key, "filename": key} for key in ("a", "b")]
    )
    inputs["value"] = json.dumps(value)
    model = []
    node = module.CIFLoraStack()
    for _ in range(2):
        result = node.apply_stack(model, **inputs)[0]
        assert result == expected
        if not expected:
            assert result is model
        assert model == []
    assert len(creations) == (2 if expected else 0)


def test_selected_runtime_requires_node_and_every_catalog_file():
    from app.domain.lora_stack import validate_lora_runtime

    graph = source().api_document
    info = object_info_fixture()
    validate_lora_runtime(graph, info)
    for name in ("CIFLoraStack", "LoraLoaderModelOnly"):
        incomplete = copy.deepcopy(info)
        incomplete.pop(name)
        with pytest.raises(ValueError):
            validate_lora_runtime(graph, incomplete)
    info["LoraLoaderModelOnly"]["input"]["required"]["lora_name"] = [["private/a.safetensors"]]
    with pytest.raises(ValueError, match="missing files"):
        validate_lora_runtime(graph, info)
    validate_lora_runtime({}, {})  # Existing publications have no new prerequisite.


def test_configurable_inventory_excludes_private_fields():
    from app.domain.source_metadata import _public_choice_loras_are_safe

    declaration = source().public_interface["inputs"][-1]
    stack = {
        "usage": "public_stack",
        "parameter_id": "loras",
        **{key: declaration[key] for key in ("items", "default", "minimum", "maximum", "step")},
    }
    assert _public_choice_loras_are_safe({"loras": [stack]})
    for mutate in (
        lambda item: item.update(filename="private"),
        lambda item: item.update(parameter_id={"filename": "private"}),
        lambda item: item["items"][0].update(filename="private"),
        lambda item: item["default"][0].update(binding="private"),
    ):
        malformed = copy.deepcopy(stack)
        mutate(malformed)
        assert not _public_choice_loras_are_safe({"loras": [malformed]})
