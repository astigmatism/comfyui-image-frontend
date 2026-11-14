from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.api.workflows import _public_interface
from app.domain.compiler import WorkflowCompiler
from app.domain.lora_stack import MAX_TRIGGER_WORD
from app.domain.publication import validate_publication
from app.errors import AppError, ContractError
from tests.publication_fixtures import add_lora_stack, build_publication_bundle, object_info_fixture

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "comfyui_extension/comfyui-image-frontend-interface"
# A character LoRA publishes its whole look as the trigger, not a single word.
TRIGGER_PHRASE = (
    "1oandve, short, straight black hair with bangs, pale skin, green eyes, "
    "accentuated with black winged eyeliner, light pink lipstick"
)


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


def test_empty_catalog_is_a_published_model_passthrough():
    def empty(public, private):
        public["items"] = []
        public["default"] = []
        private["catalog_json"] = "[]"
        private["value"] = "[]"

    selected = source(empty)
    assert selected.public_interface["inputs"][-1]["items"] == []
    compiled = compile_stack(selected)
    assert compiled.effective_controls["loras"] == []
    assert json.loads(compiled.compiled_graph["99"]["inputs"]["value"]) == []

    from app.domain.lora_stack import validate_lora_runtime

    validate_lora_runtime(selected.api_document, {"CIFLoraStack": {}})
    with pytest.raises(ValueError, match="LoRA Stack node"):
        validate_lora_runtime(selected.api_document, {})

    from app.services.lora_images import control_identities

    profile = SimpleNamespace(
        resolved_contract_json=selected.private_contract,
        source_api_json=selected.api_document,
    )
    assert control_identities(profile, "loras") == {}


def test_usage_description_is_public_and_does_not_change_execution():
    description = "Use: AlphaCharacter in your prompt (no angle brackets)."

    def update(public, private):
        public["items"][0]["description"] = description
        catalog = json.loads(private["catalog_json"])
        catalog[0]["description"] = description
        private["catalog_json"] = json.dumps(catalog)

    selected = source(update)
    for interface in (selected.public_interface, _public_interface(selected.private_contract)):
        item = interface["inputs"][-1]["items"][0]
        assert item == {"id": "a", "label": "Alpha", "description": description}
        assert "filename" not in json.dumps(item)
    compiled = compile_stack(selected)
    assert compiled.effective_controls["loras"] == [
        {"id": "a", "strength": 0},
        {"id": "b", "strength": 0},
    ]
    assert compiled.effective_controls["prompt"] == "lake"


def test_trigger_word_is_public_and_does_not_change_execution():
    def update(public, private):
        public["items"][0]["trigger_word"] = "AlphaCharacter"
        catalog = json.loads(private["catalog_json"])
        catalog[0]["trigger_word"] = "AlphaCharacter"
        private["catalog_json"] = json.dumps(catalog)

    selected = source(update)
    for interface in (selected.public_interface, _public_interface(selected.private_contract)):
        item = interface["inputs"][-1]["items"][0]
        assert item == {"id": "a", "label": "Alpha", "trigger_word": "AlphaCharacter"}
        assert "filename" not in json.dumps(item)
    compiled = compile_stack(selected)
    assert compiled.effective_controls["loras"] == [
        {"id": "a", "strength": 0},
        {"id": "b", "strength": 0},
    ]
    assert compiled.effective_controls["prompt"] == "lake"


def test_moody_candidate_only_claims_the_verified_tifa_trigger():
    spec = importlib.util.spec_from_file_location(
        "prepare_lora_workflow", ROOT / "scripts/lora/prepare_workflow.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    filenames = [f"private/{name}.safetensors" for name in ("spread", "claire", "nexblend", "tifa")]
    workflow = {
        "nodes": [
            {
                "id": 822,
                "inputs": [{"name": "model", "link": None}, {"name": "clip", "link": 100}],
                "outputs": [{"name": "model", "links": []}, {"name": "clip", "links": []}],
                "widgets_values": [{"lora": name} for name in filenames],
            },
            {"id": 30, "outputs": [{"links": [100]}]},
        ],
        "links": [[100, 30, 0, 822, 1]],
    }
    api = {
        "822": {
            "class_type": "Power Lora Loader (rgthree)",
            "inputs": {
                "model": ["30", 0],
                **{f"lora_{index}": {"lora": name} for index, name in enumerate(filenames, 1)},
            },
        },
        **{
            node_id: {"class_type": "Fake", "inputs": {"model": ["822", 0]}}
            for node_id in ("599", "663", "842")
        },
    }

    _, candidate_api = module.prepare(workflow, api)
    catalog = json.loads(candidate_api["822"]["inputs"]["catalog_json"])
    assert [item.get("trigger_word") for item in catalog] == [None, None, None, "TifaLockhart"]


@pytest.mark.parametrize("description", [None, "", " ", 1, {}, "x" * 1001])
def test_invalid_usage_description_is_rejected(description):
    def update(public, private):
        public["items"][0]["description"] = description
        catalog = json.loads(private["catalog_json"])
        catalog[0]["description"] = description
        private["catalog_json"] = json.dumps(catalog)

    with pytest.raises(ContractError):
        source(update)


@pytest.mark.parametrize("trigger_word", [None, "", " ", 1, {}, "x" * (MAX_TRIGGER_WORD + 1)])
def test_invalid_trigger_word_is_rejected(trigger_word):
    def update(public, private):
        public["items"][0]["trigger_word"] = trigger_word
        catalog = json.loads(private["catalog_json"])
        catalog[0]["trigger_word"] = trigger_word
        private["catalog_json"] = json.dumps(catalog)

    with pytest.raises(ContractError):
        source(update)


@pytest.mark.parametrize("trigger_word", [TRIGGER_PHRASE, "x" * MAX_TRIGGER_WORD])
def test_trigger_phrase_is_published_verbatim(trigger_word):
    def update(public, private):
        public["items"][0]["trigger_word"] = trigger_word
        catalog = json.loads(private["catalog_json"])
        catalog[0]["trigger_word"] = trigger_word
        private["catalog_json"] = json.dumps(catalog)

    selected = source(update)
    for interface in (selected.public_interface, _public_interface(selected.private_contract)):
        assert interface["inputs"][-1]["items"][0]["trigger_word"] == trigger_word
    compiled = compile_stack(selected)
    assert compiled.effective_controls["loras"] == [
        {"id": "a", "strength": 0},
        {"id": "b", "strength": 0},
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
        lambda public, private: public["items"][0].update(description="Unpublished usage"),
        lambda public, private: public["items"][0].update(trigger_word="UnpublishedTrigger"),
        lambda public, private: private.update(
            catalog_json=private["catalog_json"].replace(
                '"label": "Alpha"', '"label": "Alpha", "trigger_word": "Secret"'
            )
        ),
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


def test_a_full_library_of_trigger_phrases_still_loads(node_module):
    """100 maximum-length trigger phrases serialize past the old 100 KB catalog budget."""

    module, _fake = node_module
    catalog = [
        {
            "id": f"lora_{index:02d}",
            "label": f"LoRA {index}",
            "filename": f"private/lora_{index:02d}.safetensors",
            "trigger_word": "x" * MAX_TRIGGER_WORD,
        }
        for index in range(100)
    ]
    inputs = copy.deepcopy(source().api_document["99"]["inputs"])
    inputs.pop("model")
    inputs["catalog_json"] = json.dumps(catalog)
    inputs["value"] = json.dumps([{"id": item["id"], "strength": 0} for item in catalog])
    assert len(inputs["catalog_json"]) > 100_000
    model = []
    assert module.CIFLoraStack().apply_stack(model, **inputs)[0] is model


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


def test_a_range_below_zero_needs_a_companion_that_can_enter_it():
    from app.domain.lora_stack import validate_lora_runtime

    graph = source(lambda public, private: wetness(public, private)).api_document
    info = object_info_fixture()
    validate_lora_runtime(graph, info)

    def declare(value):
        given = copy.deepcopy(info)
        given["CIFLoraStack"]["input"]["required"]["minimum"] = value
        return given

    # A companion predating the declared bounds clamps its minimum widget at zero, so
    # this stack was never publishable on it — the same holds for a secondary runtime
    # that has not reloaded the companion package yet.
    clamped = declare(["FLOAT", {"default": 0}])
    with pytest.raises(ValueError, match="below zero"):
        validate_lora_runtime(graph, clamped)
    # The guard is about the floor the author would have to enter, not about the age of
    # the companion: a floor as low as the published one is enough, and a stack that
    # starts at zero still runs everywhere it always did.
    validate_lora_runtime(graph, declare(["FLOAT", {"default": 0.0, "min": -1.0, "max": 1e12}]))
    validate_lora_runtime(source().api_document, clamped)
    # An empty catalog still declares a range, so it is refused just the same.
    empty = copy.deepcopy(graph)
    empty["99"]["inputs"]["catalog_json"] = "[]"
    with pytest.raises(ValueError, match="below zero"):
        validate_lora_runtime(empty, clamped)


def test_configurable_inventory_excludes_private_fields():
    from app.domain.source_metadata import _public_choice_loras_are_safe

    declaration = source().public_interface["inputs"][-1]
    stack = {
        "usage": "public_stack",
        "parameter_id": "loras",
        **{key: declaration[key] for key in ("items", "default", "minimum", "maximum", "step")},
    }
    assert _public_choice_loras_are_safe({"loras": [stack]})
    stack["items"][0]["trigger_word"] = "AlphaCharacter"
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


# A LoRA strength is signed. Zero is the only value that leaves the model alone, so a
# published range may reach below zero: the Krea 2 wetness slider is loaded at -1.0 to
# take the wetness off, not at 0 to leave it in.
def wetness(public: dict, private: dict, *, preferred: bool = False) -> None:
    public.update(minimum=-1.0)
    private.update(minimum=-1.0)
    if preferred:
        public["items"][0]["preferred_strength"] = -1.0
        catalog = json.loads(private["catalog_json"])
        catalog[0]["preferred_strength"] = -1.0
        private["catalog_json"] = json.dumps(catalog)


def test_a_range_below_zero_publishes_and_reaches_the_compiled_prompt():
    selected = source(lambda public, private: wetness(public, private))
    for interface in (selected.public_interface, _public_interface(selected.private_contract)):
        declaration = interface["inputs"][-1]
        assert declaration["minimum"] == -1.0
        # Zero stays the published default, so it stays the universal "not loaded".
        assert declaration["default"] == [{"id": "a", "strength": 0}, {"id": "b", "strength": 0}]
    requested = [{"id": "a", "strength": -1.0}, {"id": "b", "strength": 0}]
    compiled = compile_stack(selected, loras=requested)
    assert compiled.requested_controls["loras"] == requested
    assert compiled.effective_controls["loras"] == requested
    value = compiled.compiled_graph["99"]["inputs"]["value"]
    assert json.loads(value) == requested
    # The compiled graph carries compact JSON, which is what a prompt inspection sees.
    assert '"id":"a","strength":-1.0' in value


@pytest.mark.parametrize(
    ("strength", "reason"),
    [
        (-1.05, "from -1.0 to 2"),
        (-0.73, "increments of 0.05"),
        (-0.02, "increments of 0.05"),
        (3, "from -1.0 to 2"),
    ],
)
def test_a_range_below_zero_still_rejects_what_it_always_rejected(strength, reason):
    with pytest.raises(AppError) as exc:
        compile_stack(
            source(lambda public, private: wetness(public, private)),
            loras=[{"id": "a", "strength": strength}, {"id": "b", "strength": 0}],
        )
    assert "loras" in exc.value.fields
    assert reason in exc.value.fields["loras"]


def test_negative_zero_is_as_off_as_zero_ever_was():
    from app.domain.lora_stack import validate_lora_stack

    declaration = {
        "items": [{"id": "a", "label": "A"}],
        "minimum": -1.0,
        "maximum": 2.0,
        "step": 0.05,
    }
    assert validate_lora_stack([{"id": "a", "strength": -0.0}], declaration) == [
        {"id": "a", "strength": -0.0}
    ]
    compiled = compile_stack(
        source(lambda public, private: wetness(public, private)),
        loras=[{"id": "a", "strength": -0.0}, {"id": "b", "strength": 0}],
    )
    assert compiled.effective_controls["loras"] == [
        {"id": "a", "strength": 0},
        {"id": "b", "strength": 0},
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        # A floor above zero would put this contract's own all-zero defaults out of range.
        lambda public, private: (public.update(minimum=0.5), private.update(minimum=0.5)),
        # A floor off the step grid fails for the same reason: zero is not on that grid.
        lambda public, private: (public.update(minimum=-1.02), private.update(minimum=-1.02)),
        lambda public, private: (public.update(maximum=0), private.update(maximum=0)),
        lambda public, private: (
            public.update(minimum=-1, maximum=-0.05),
            private.update(minimum=-1, maximum=-0.05),
        ),
        lambda public, private: (public.update(step=0), private.update(step=0)),
    ],
)
def test_constraints_must_span_zero(mutate):
    with pytest.raises(ContractError):
        source(mutate)


def test_an_empty_catalog_still_needs_a_range_that_spans_zero():
    def empty(public, private):
        public["items"] = []
        public["default"] = []
        private["catalog_json"] = "[]"
        private["value"] = "[]"
        public.update(minimum=0.5)
        private.update(minimum=0.5)

    with pytest.raises(ContractError, match="span zero"):
        source(empty)


@pytest.mark.parametrize(
    ("minimum", "maximum", "step", "strength", "accepted"),
    [
        (0, 2, 0.05, 0, True),
        (0, 2, 0.05, -0.05, False),
        (-1, 2, 0.05, -1, True),
        (-1, 2, 0.05, -0.05, True),
        (-1, 2, 0.05, -0.75, True),
        (-1, 2, 0.05, -1.05, False),
        (-1, 2, 0.05, 2, True),
        (-1, 2, 0.05, 2.05, False),
        (-2.5, 2.5, 0.5, -2.5, True),
        (-2.5, 2.5, 0.5, -2, True),
        (-1, 1, 1, -1, True),
        (-0.2, 0.2, 0.1, -0.2, True),
        (-0.2, 0.2, 0.1, 0.3, False),
    ],
)
def test_a_signed_strength_is_validated_on_the_published_grid(
    minimum, maximum, step, strength, accepted
):
    from app.domain.lora_stack import validate_lora_stack

    declaration = {
        "items": [{"id": "a", "label": "A"}],
        "minimum": minimum,
        "maximum": maximum,
        "step": step,
    }
    value = [{"id": "a", "strength": strength}]
    if accepted:
        assert validate_lora_stack(value, declaration) == value
    else:
        with pytest.raises(ValueError):
            validate_lora_stack(value, declaration)


@pytest.mark.parametrize(
    ("minimum", "maximum", "step"),
    [
        (0.5, 2, 0.05),  # zero, and so every published default, would be out of range
        (1, 2, 0.05),
        (0, 0, 0.05),  # nothing above zero left to apply
        (-1, -0.05, 0.05),  # a range that is only ever pulling the other way
        (0, 2, 0),
        (0, 2, -0.05),
        (0, float("nan"), 0.05),
        (float("inf"), 2, 0.05),
        (None, 2, 0.05),
        ("0", 2, 0.05),
        (True, 2, 0.05),
    ],
)
def test_constraints_outside_the_rule_are_rejected(minimum, maximum, step):
    from app.domain.lora_stack import validate_lora_stack

    with pytest.raises(ValueError):
        validate_lora_stack([], {"items": [], "minimum": minimum, "maximum": maximum, "step": step})


def test_a_preferred_strength_is_public_and_changes_nothing():
    selected = source(lambda public, private: wetness(public, private, preferred=True))
    for interface in (selected.public_interface, _public_interface(selected.private_contract)):
        declaration = interface["inputs"][-1]
        assert declaration["minimum"] == -1.0
        assert declaration["items"][0] == {"id": "a", "label": "Alpha", "preferred_strength": -1.0}
        assert "filename" not in json.dumps(declaration["items"])
        assert "catalog_json" not in json.dumps(declaration)
    compiled = compile_stack(selected)
    assert compiled.effective_controls["loras"] == [
        {"id": "a", "strength": 0},
        {"id": "b", "strength": 0},
    ]


@pytest.mark.parametrize(
    "preferred_strength",
    [None, "", {}, "x", -1.05, 2.05, -0.73, 3, float("nan"), float("inf"), True],
)
def test_a_preferred_strength_must_be_one_this_stack_accepts(preferred_strength):
    def update(public, private):
        wetness(public, private)
        public["items"][0]["preferred_strength"] = preferred_strength
        catalog = json.loads(private["catalog_json"])
        catalog[0]["preferred_strength"] = preferred_strength
        private["catalog_json"] = json.dumps(catalog)

    with pytest.raises(ContractError):
        source(update)


@pytest.mark.parametrize(
    "value,expected",
    [
        ([{"id": "a", "strength": -1}, {"id": "b", "strength": 0}], [("a", -1)]),
        ([{"id": "a", "strength": -1}, {"id": "b", "strength": 2}], [("a", -1), ("b", 2)]),
        (
            [{"id": "b", "strength": -0.05}, {"id": "a", "strength": 1.5}],
            [("b", -0.05), ("a", 1.5)],
        ),
        ([{"id": "a", "strength": -0.0}, {"id": "b", "strength": 0}], []),
    ],
)
def test_the_native_loader_subtracts_a_negative_strength(node_module, value, expected):
    module, fake = node_module
    creations = []

    class Loader:
        def __init__(self):
            creations.append(self)

        def load_lora_model_only(self, model, filename, strength):
            return ([*model, (filename, strength)],)

    fake.LoraLoaderModelOnly = Loader
    inputs = copy.deepcopy(
        source(lambda public, private: wetness(public, private)).api_document["99"]
    )
    inputs = {key: given for key, given in inputs["inputs"].items() if key != "model"}
    inputs["catalog_json"] = json.dumps(
        [{"id": key, "label": key, "filename": key} for key in ("a", "b")]
    )
    inputs["value"] = json.dumps(value)
    model = []
    node = module.CIFLoraStack()
    for _ in range(2):
        result = node.apply_stack(model, **inputs)[0]
        # The loader is called in application order with the signed strength, and the
        # model each entry returns is the one the next entry is handed.
        assert result == [(key, strength) for key, strength in expected]
        assert model == []
    assert len(creations) == (2 if expected else 0)


def test_a_preferred_strength_need_not_be_negative():
    def update(public, private):
        public["items"][0]["preferred_strength"] = 1.5
        catalog = json.loads(private["catalog_json"])
        catalog[0]["preferred_strength"] = 1.5
        private["catalog_json"] = json.dumps(catalog)

    selected = source(update)
    declaration = selected.public_interface["inputs"][-1]
    assert declaration["minimum"] == 0
    assert declaration["items"][0]["preferred_strength"] == 1.5
