import json
import re

import pytest
from app.domain.checkpoint_identity import (
    checkpoint_identity_v1,
    generation_checkpoint_identity_v1,
)


def declaration(node="12", parameter="checkpoint"):
    return {
        "id": parameter,
        "type": "choice",
        "semantic_role": "model",
        "bindings": [{"node_id": node, "input": "value"}],
    }


def graph(node="12", alias="first", filename="models/portrait.safetensors"):
    return {
        node: {
            "inputs": {
                "options_json": json.dumps(
                    [
                        {"id": alias, "label": "Public display name", "binding": filename},
                    ]
                )
            }
        }
    }


def test_model_binding_shares_identity_across_workflows_aliases_and_node_ids():
    first = checkpoint_identity_v1(declaration(), "first", graph(), "workflow-one")
    second = checkpoint_identity_v1(
        declaration("42", "base_model"),
        "other-alias",
        graph("42", "other-alias", r"models\portrait.safetensors"),
        "workflow-two",
    )
    assert first == second
    assert re.fullmatch(r"cp1_[0-9a-f]{64}", first)
    assert "portrait" not in first


def test_identical_public_names_and_values_do_not_merge_different_models():
    first = checkpoint_identity_v1(declaration(), "first", graph(), "same-workflow")
    second = checkpoint_identity_v1(
        declaration(), "first", graph(filename="models/other.safetensors"), "same-workflow"
    )
    assert first != second


@pytest.mark.parametrize("broken", [None, {}, {"12": None}, {"12": {"inputs": None}}])
def test_unresolved_bindings_are_source_scoped(broken):
    first = checkpoint_identity_v1(declaration(), "first", broken, "one")
    assert first != checkpoint_identity_v1(declaration(), "first", broken, "two")
    assert first == checkpoint_identity_v1(declaration(), "first", broken, "one")


def test_frozen_compiled_graph_and_legacy_option_values_match_current_identity():
    compiled = graph()
    compiled["12"]["inputs"]["value"] = "first"
    options = json.loads(compiled["12"]["inputs"]["options_json"])
    options[0]["value"] = options[0].pop("id")
    compiled["12"]["inputs"]["options_json"] = json.dumps(options)
    assert generation_checkpoint_identity_v1(
        {"inputs": [declaration()]}, {"checkpoint": "first"}, compiled, "old-source"
    ) == checkpoint_identity_v1(declaration(), "first", graph(), "current-source")
    assert generation_checkpoint_identity_v1({"inputs": None}, {}, {}, "source") is None
