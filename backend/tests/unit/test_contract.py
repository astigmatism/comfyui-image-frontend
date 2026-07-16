from __future__ import annotations

import hashlib

import pytest
from app.domain.publication import (
    EDITABLE_WORKFLOW_DRIFT_WARNING,
    FROZEN_API_DRIFT_WARNING,
    parse_json_object,
    validate_publication,
)
from app.domain.source_metadata import TECHNICAL_INVENTORY_COUNT_WARNING
from app.errors import ContractError
from tests.publication_fixtures import (
    KREA_PUBLICATION_ID,
    PublicationBundle,
    add_image_input,
    build_publication_bundle,
    object_info_fixture,
)


def test_required_image_input_is_additive_v1_and_keeps_bindings_private() -> None:
    publication = validate(build_publication_bundle(mutate_artifacts=add_image_input))

    image = publication.public_interface["inputs"][0]
    assert image == {
        "id": "reference_image",
        "type": "image",
        "label": "Reference Image",
        "description": "Required source image whose content and dimensions guide the edit.",
        "semantic_role": "reference_image",
        "required": True,
        "advanced": False,
        "group": "Basic",
        "order": 5,
        "media": {
            "upload_route": "/upload/image",
            "storage_type": "input",
            "accepted_mime_types": ["image/png", "image/jpeg", "image/webp"],
            "max_bytes": 20 * 1024 * 1024,
            "max_width": 8192,
            "max_height": 8192,
            "animated": False,
            "returns_mask": True,
        },
    }
    private = publication.private_contract["inputs"][0]
    assert private["bindings"] == [
        {"node_id": "18", "input": "image", "class_type": "CIFImageParameter"}
    ]
    assert "default" not in private


@pytest.mark.parametrize(
    "break_contract",
    [
        lambda image, api: image.__setitem__("required", False),
        lambda image, api: image.__setitem__("default", "fixture.png"),
        lambda image, api: image.__setitem__("semantic_role", "init_image"),
        lambda image, api: image["media"].__setitem__("upload_route", "/view"),
        lambda image, api: image["media"].__setitem__("accepted_mime_types", ["image/*"]),
        lambda image, api: image["media"].__setitem__("animated", True),
        lambda image, api: api["18"]["inputs"].__setitem__("max_width", 4096),
        lambda image, api: image["bindings"][0].__setitem__("input", "max_width"),
    ],
)
def test_image_input_rejects_malformed_or_untrusted_contracts(break_contract) -> None:  # type: ignore[no-untyped-def]
    def mutate(manifest, workflow, api) -> None:  # type: ignore[no-untyped-def]
        add_image_input(manifest, workflow, api)
        break_contract(manifest["interface"]["inputs"][0], api)

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_artifacts=mutate))

    assert exc.value.code == "manifest_invalid"


def validate(bundle: PublicationBundle, *, object_info=None, **limits):  # type: ignore[no-untyped-def]
    return validate_publication(
        instance_id="test-instance",
        manifest_path=bundle.manifest_path,
        manifest_bytes=bundle.manifest_bytes,
        workflow_bytes=bundle.workflow_bytes,
        api_bytes=bundle.api_bytes,
        object_info=object_info if object_info is not None else object_info_fixture(),
        manifest_max_bytes=limits.get("manifest_max_bytes", 1024 * 1024),
        workflow_max_bytes=limits.get("workflow_max_bytes", 1024 * 1024),
        api_max_bytes=limits.get("api_max_bytes", 1024 * 1024),
    )


def test_krea_like_publication_validates_exact_bytes_and_safe_public_interface() -> None:
    bundle = build_publication_bundle("krea")
    publication = validate(bundle)

    assert publication.publication_id == KREA_PUBLICATION_ID
    assert publication.source_id.endswith("Krea 2 NSFW V4.json")
    assert publication.display_name == "Krea 2 NSFW V4"
    assert publication.workflow_sha256 == hashlib.sha256(bundle.workflow_bytes).hexdigest()
    assert publication.api_sha256 == hashlib.sha256(bundle.api_bytes).hexdigest()
    assert publication.node_count == 11
    assert publication.readiness == "ready"
    assert publication.warnings == ()
    inputs = publication.public_interface["inputs"]
    assert [value["id"] for value in inputs] == [
        "prompt",
        "width",
        "height",
        "seed",
        "enable_seedvr2_upscale",
        "lora",
        "lora_strength",
    ]
    assert sum(not value["advanced"] for value in inputs) == 5
    assert inputs[-1]["advanced"] is True
    assert inputs[3]["default"] is None
    assert inputs[3]["maximum"] == 1125899906842624
    choice = inputs[-2]
    assert choice["default"] == "knp_v4_1"
    assert choice["choices"] == [
        {"value": "knp_v4_1", "label": "KNP v4.1", "default_strength": 1.0},
        {"value": "knp_v3_1", "label": "KNP v3.1", "default_strength": 0.5},
        {"value": "knp_v2", "label": "KNP v2", "default_strength": 1.0},
        {
            "value": "mysticxxx_krea2_v1",
            "label": "MysticXXX Krea2 v1",
            "default_strength": 1.0,
        },
    ]
    assert all(
        set(option) <= {"value", "label", "default_strength"} for option in choice["choices"]
    )
    assert "options_json" not in str(publication.public_interface)
    assert "safetensors" not in str(publication.public_interface)
    assert [
        (value["id"], value["role"], value["kind"], value["cardinality"])
        for value in publication.public_interface["outputs"]
    ] == [
        ("base", "preview", "image", "many"),
        ("second_pass", "comparison", "image", "many"),
        ("final", "final", "image", "many"),
    ]
    assert "native_outputs" not in publication.public_interface
    assert len(publication.private_contract["native_outputs"]) == 4
    assert all("bindings" not in value and "instance_uuid" not in value for value in inputs)


@pytest.mark.parametrize("size", [1, 100])
def test_choice_contract_accepts_nonempty_bounded_unique_public_options(size: int) -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        choice = manifest["interface"]["inputs"][-2]
        choice["choices"] = [
            {"value": f"option_{index}", "label": f"Option {index}"} for index in range(size)
        ]
        choice["default"] = "option_0"

    publication = validate(build_publication_bundle(mutate_manifest=mutate))

    choice = publication.public_interface["inputs"][-2]
    assert len(choice["choices"]) == size
    assert choice["default"] == "option_0"


@pytest.mark.parametrize("size", [0, 101])
def test_choice_contract_rejects_empty_or_oversized_option_lists(size: int) -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        choice = manifest["interface"]["inputs"][-2]
        choice["choices"] = [
            {"value": f"option_{index}", "label": f"Option {index}"} for index in range(size)
        ]

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert ".choices must contain 1 to 100 entries" in exc.value.message


def test_choice_contract_rejects_duplicate_public_values() -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        choices = manifest["interface"]["inputs"][-2]["choices"]
        choices[1]["value"] = choices[0]["value"]

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "duplicate value" in exc.value.message


@pytest.mark.parametrize(
    "value",
    ["KNP_v4_1", "1st_option", "knp-v4-1", "private/path", "a" * 65],
)
def test_choice_contract_rejects_unsafe_public_values(value: str) -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["inputs"][-2]["choices"][0]["value"] = value

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "valid public ID" in exc.value.message


@pytest.mark.parametrize("default", [None, 1, True, ["knp_v4_1"]])
def test_choice_contract_requires_a_string_default(default) -> None:  # type: ignore[no-untyped-def]
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["inputs"][-2]["default"] = default

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert ".default must be a string" in exc.value.message


@pytest.mark.parametrize("default", ["", "KNP v4.1", "missing_choice"])
def test_choice_contract_requires_default_membership(default: str) -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["inputs"][-2]["default"] = default

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "must match exactly one choice value" in exc.value.message


@pytest.mark.parametrize("label", ["", "  \t\n"])
def test_choice_contract_rejects_blank_labels(label: str) -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["inputs"][-2]["choices"][0]["label"] = label

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "label" in exc.value.message


@pytest.mark.parametrize("default_strength", [True, False, None, "0.5", float("inf")])
def test_choice_contract_rejects_nonfinite_boolean_or_nonnumeric_default_strength(
    default_strength,
) -> None:  # type: ignore[no-untyped-def]
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["inputs"][-2]["choices"][0]["default_strength"] = default_strength

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"


@pytest.mark.parametrize(
    "private_field",
    ["binding", "bindings", "path", "prompt_path", "node_id", "lora_name", "options_json"],
)
def test_choice_contract_rejects_private_or_binding_fields(private_field: str) -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["inputs"][-2]["choices"][0][private_field] = "secret"

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "unsupported or private field" in exc.value.message
    assert "secret" not in exc.value.message


def test_choice_contract_rejects_nonobject_entries() -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["inputs"][-2]["choices"][0] = "knp_v4_1"

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "must be an object" in exc.value.message


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest, api: manifest["interface"]["inputs"][-2]["bindings"][0].__setitem__(
            "input", "options_json"
        ),
        lambda manifest, api: (
            manifest["interface"]["inputs"][-2]["bindings"][0].__setitem__("node_id", "20"),
            manifest["interface"]["inputs"][-2]["bindings"][0].__setitem__("input", "lora_name"),
        ),
        lambda manifest, api: (
            api["202"].__setitem__("class_type", "CIFTextParameter"),
            manifest["interface"]["inputs"][-2]["bindings"][0].__setitem__(
                "class_type", "CIFTextParameter"
            ),
        ),
    ],
)
def test_choice_binding_must_target_cif_choice_parameter_value(mutation) -> None:  # type: ignore[no-untyped-def]
    def mutate_artifacts(manifest, _workflow, api) -> None:  # type: ignore[no-untyped-def]
        mutation(manifest, api)

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_artifacts=mutate_artifacts))

    assert exc.value.code == "manifest_invalid"


def test_choice_binding_runtime_value_must_be_string_typed() -> None:
    object_info = object_info_fixture()
    object_info["CIFChoiceParameter"]["input"]["required"]["value"] = ["INT"]

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(), object_info=object_info)

    assert exc.value.code == "manifest_invalid"
    assert "runtime input type" in exc.value.message


def test_manifest_output_type_is_normalized_to_contract_kind() -> None:
    bundle = build_publication_bundle("generic")
    manifest = parse_json_object(
        bundle.manifest_bytes,
        context="Publication manifest",
        maximum_bytes=1024 * 1024,
    )
    manifest_output = manifest["interface"]["outputs"][0]

    assert manifest_output["type"] == "image"
    assert "kind" not in manifest_output

    publication = validate(bundle)
    for normalized_contract in (publication.private_contract, publication.public_interface):
        normalized_output = normalized_contract["outputs"][0]
        assert normalized_output["kind"] == "image"
        assert "type" not in normalized_output


def test_manifest_output_kind_is_not_a_substitute_for_required_type() -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        output = manifest["interface"]["outputs"][0]
        output["kind"] = output.pop("type")

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"


def test_second_generic_source_has_independent_contract_and_declared_output() -> None:
    publication = validate(build_publication_bundle("generic"))

    assert publication.display_name == "Generic Landscape"
    assert [value["id"] for value in publication.public_interface["inputs"]] == [
        "prompt",
        "iterations",
    ]
    assert publication.public_interface["outputs"] == [
        {
            "id": "final_image",
            "role": "final",
            "kind": "image",
            "cardinality": "many",
            "label": "Final image",
            "description": "Declared generic final image.",
        }
    ]
    assert publication.readiness == "ready"
    assert "native_outputs" not in publication.public_interface
    assert set(publication.private_contract["native_outputs"]) == {"120", "130"}
    assert all(value["type"] != "choice" for value in publication.public_interface["inputs"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda interface: interface.pop("outputs"),
        lambda interface: interface.__setitem__("outputs", []),
        lambda interface: interface["outputs"][0].__setitem__("type", "text"),
        lambda interface: interface["outputs"][0].pop("cardinality"),
        lambda interface: interface["outputs"][0].__setitem__("cardinality", "one"),
        lambda interface: interface.pop("unmapped_outputs_policy"),
        lambda interface: interface.__setitem__("unmapped_outputs_policy", "discard"),
    ],
)
def test_output_contract_requires_explicit_unique_image_publishers_and_collect_policy(
    mutation,
) -> None:  # type: ignore[no-untyped-def]
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        mutation(manifest["interface"])

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"


@pytest.mark.parametrize(
    ("duplicate", "message"),
    [
        ("id", "Duplicate public output ID"),
        ("uuid", "Declaration instance UUIDs must be unique"),
        ("node", "unique publisher node"),
    ],
)
def test_output_ids_uuids_and_publisher_node_bindings_are_independently_unique(
    duplicate: str, message: str
) -> None:
    def mutate(manifest, _workflow, api) -> None:  # type: ignore[no-untyped-def]
        first, second = manifest["interface"]["outputs"][:2]
        if duplicate == "id":
            second["id"] = first["id"]
            api["200"]["inputs"]["output_id"] = first["id"]
        elif duplicate == "uuid":
            second["instance_uuid"] = first["instance_uuid"]
            api["200"]["inputs"]["instance_uuid"] = first["instance_uuid"]
        else:
            second["node_id"] = first["node_id"]
            for field in (
                "output_id",
                "instance_uuid",
                "role",
                "kind",
                "cardinality",
                "description",
            ):
                api["199"]["inputs"].pop(field, None)

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_artifacts=mutate))

    assert exc.value.code == "manifest_invalid"
    assert message in exc.value.message


@pytest.mark.parametrize("final_count", [0, 2])
def test_exactly_one_final_image_output_is_required(final_count: int) -> None:
    def mutate(manifest, _workflow, api) -> None:  # type: ignore[no-untyped-def]
        outputs = manifest["interface"]["outputs"]
        outputs[2]["role"] = "auxiliary" if final_count == 0 else "final"
        api["201"]["inputs"]["role"] = outputs[2]["role"]
        if final_count == 2:
            outputs[0]["role"] = "final"
            api["199"]["inputs"]["role"] = "final"

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_artifacts=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "exactly one final image" in exc.value.message


@pytest.mark.parametrize(
    "connection",
    [None, "not-a-connection", ["999", 0], ["20", -1], ["201", 0]],
)
def test_image_publisher_must_have_a_valid_connected_source(connection) -> None:  # type: ignore[no-untyped-def]
    def mutate(_manifest, _workflow, api) -> None:  # type: ignore[no-untyped-def]
        if connection is None:
            api["201"]["inputs"].pop("images")
        else:
            api["201"]["inputs"]["images"] = connection

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_artifacts=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "image" in exc.value.message


def test_output_must_reference_a_cif_publish_image_node() -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["outputs"][2]["node_id"] = "20"

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "publisher type" in exc.value.message


def test_output_description_must_match_frozen_publisher_node() -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["outputs"][2]["description"] = "Divergent public description."

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"
    assert "description" in exc.value.message
    assert "frozen publisher node" in exc.value.message


@pytest.mark.parametrize(
    "inventory",
    [
        None,
        [],
        [{"opaque": "diagnostic-without-a-node"}],
        [{"node_id": "999", "class_type": "FakeImageOutput"}],
        [{"node_id": "20", "class_type": "WrongClass"}],
        [
            {"node_id": "20", "class_type": "FakeImageOutput"},
            {"node_id": "20", "class_type": "FakeImageOutput"},
        ],
        [{"node_id": "20", "class_type": "FakeImageOutput"}],
    ],
)
def test_native_output_inventory_rejects_missing_or_false_recognizable_claims(
    inventory,
) -> None:  # type: ignore[no-untyped-def]
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        interface = manifest["interface"]
        if inventory is None:
            interface.pop("native_outputs")
        else:
            interface["native_outputs"] = inventory

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))

    assert exc.value.code == "manifest_invalid"


def test_native_output_inventory_accepts_compatible_diagnostic_container_shapes() -> None:
    bundle = build_publication_bundle(
        mutate_manifest=lambda manifest: manifest["interface"].__setitem__(
            "native_outputs",
            {
                "format": "vendor-diagnostic/v2",
                "items": manifest["interface"]["native_outputs"],
                "additional_metadata": {"producer": "fixture"},
            },
        )
    )

    publication = validate(bundle)

    assert publication.private_contract["native_outputs"]["format"] == "vendor-diagnostic/v2"
    assert "native_outputs" not in publication.public_interface


def test_native_output_inventory_has_a_dedicated_entry_bound() -> None:
    oversized = [{"opaque": index} for index in range(10_001)]
    bundle = build_publication_bundle(
        mutate_manifest=lambda manifest: manifest["interface"].__setitem__(
            "native_outputs", oversized
        )
    )

    with pytest.raises(ContractError) as exc:
        validate(bundle)

    assert exc.value.code == "manifest_invalid"
    assert "bounded" in exc.value.message


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "schema_version",
            "comfyui-image-frontend.publication/v2",
            "unsupported_publication_schema",
        ),
        ("contract_schema", "comfyui-image-frontend.interface/v2", "unsupported_contract_schema"),
    ],
)
def test_unsupported_schema_lines_are_rejected(field: str, value: str, code: str) -> None:
    bundle = build_publication_bundle(
        mutate_manifest=lambda manifest: manifest.__setitem__(field, value)
    )
    with pytest.raises(ContractError) as exc:
        validate(bundle)
    assert exc.value.code == code


def test_strict_json_and_size_limits_fail_before_contract_validation() -> None:
    bundle = build_publication_bundle()
    malformed = PublicationBundle(
        **{**bundle.__dict__, "manifest_bytes": b'{"schema_version":NaN}'}
    )
    with pytest.raises(ContractError) as malformed_exc:
        validate(malformed)
    assert malformed_exc.value.code == "manifest_invalid"

    with pytest.raises(ContractError) as size_exc:
        validate(bundle, manifest_max_bytes=len(bundle.manifest_bytes) - 1)
    assert size_exc.value.code == "artifact_too_large"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"value":1e9999}',
        b'{"value":"\\ud800"}',
        b'{"value":18446744073709551616}',
        b'{"value":' + (b"[" * 200) + b"0" + (b"]" * 200) + b"}",
    ],
)
def test_strict_json_rejects_nonfinite_invalid_unicode_and_pathological_values(
    raw: bytes,
) -> None:
    with pytest.raises(ContractError) as exc:
        parse_json_object(raw, context="Fixture artifact", maximum_bytes=1024 * 1024)

    assert exc.value.code == "manifest_invalid"


@pytest.mark.parametrize(
    "unsafe",
    [
        "/workflows/escape.json",
        "workflows/../escape.json",
        "workflows\\escape.json",
        "workflows//escape.json",
        "workflows%2Fescape.json",
    ],
)
def test_source_paths_reject_absolute_traversal_and_ambiguous_separators(unsafe: str) -> None:
    bundle = build_publication_bundle(
        mutate_manifest=lambda manifest: manifest.__setitem__("source_id", unsafe)
    )
    with pytest.raises(ContractError) as exc:
        validate(bundle)
    assert exc.value.code == "manifest_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest.__setitem__("source_id", "workflows/other/source.json"),
        lambda manifest: manifest["api"].__setitem__("path", "workflows/other/source.api.json"),
        lambda manifest: manifest["manifest"].__setitem__(
            "path", "workflows/other/source.interface.json"
        ),
    ],
)
def test_source_id_and_adjacent_stems_must_match_listing(mutation) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutation))
    assert exc.value.code == "manifest_invalid"


def test_editable_workflow_hash_drift_is_a_nonfatal_structured_warning() -> None:
    bundle = build_publication_bundle(corrupt="workflow")
    # A trailing newline preserves valid JSON but changes the authoritative exact bytes.

    publication = validate(bundle)

    assert publication.readiness == "ready_with_warnings"
    assert publication.warnings == (EDITABLE_WORKFLOW_DRIFT_WARNING,)
    assert publication.private_contract["warnings"] == [EDITABLE_WORKFLOW_DRIFT_WARNING]
    assert publication.workflow_sha256 == bundle.manifest()["workflow"]["sha256"]
    assert publication.observed_workflow_sha256 == hashlib.sha256(bundle.workflow_bytes).hexdigest()
    assert publication.observed_workflow_sha256 != publication.workflow_sha256
    assert publication.editable_workflow_drifted is True
    assert publication.api_document == bundle.api()


def test_editable_workflow_drift_is_appended_to_valid_publisher_warnings() -> None:
    bundle = build_publication_bundle(
        corrupt="workflow",
        mutate_manifest=lambda manifest: manifest.__setitem__(
            "warnings", [{"message": "Publisher-authored warning."}]
        ),
    )

    publication = validate(bundle)

    assert publication.warnings == (
        "Publisher-authored warning.",
        EDITABLE_WORKFLOW_DRIFT_WARNING,
    )


def test_api_hash_mismatch_is_nonfatal_and_execution_uses_the_observed_hash() -> None:
    bundle = build_publication_bundle(corrupt="api")

    publication = validate(bundle)

    assert publication.readiness == "ready_with_warnings"
    assert publication.warnings == (FROZEN_API_DRIFT_WARNING,)
    assert publication.recorded_api_sha256 == bundle.manifest()["api"]["sha256"]
    assert publication.api_sha256 == hashlib.sha256(bundle.api_bytes).hexdigest()
    assert publication.api_sha256 != publication.recorded_api_sha256
    assert publication.api_drifted is True
    assert publication.api_document == bundle.api()


def test_workflow_and_api_hash_mismatches_are_both_nonfatal_and_diagnostic() -> None:
    workflow_drifted = build_publication_bundle(corrupt="workflow")
    both_drifted = PublicationBundle(
        **{**workflow_drifted.__dict__, "api_bytes": workflow_drifted.api_bytes + b"\n"}
    )

    publication = validate(both_drifted)

    assert publication.warnings == (
        EDITABLE_WORKFLOW_DRIFT_WARNING,
        FROZEN_API_DRIFT_WARNING,
    )
    assert publication.editable_workflow_drifted is True
    assert publication.api_drifted is True


def test_v1_generation_source_and_inventory_are_recognized_losslessly() -> None:
    def add_fixed_lora(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["technical_inventory"]["loras"].insert(
            0,
            {
                "artifact": "detail-style.safetensors",
                "strength": 0.75,
                "usage": "fixed_active",
            },
        )

    publication = validate(build_publication_bundle("krea", mutate_manifest=add_fixed_lora))

    assert publication.metadata_diagnostics == ()
    assert publication.generation_source is not None
    assert publication.generation_source["generation_type"] == "text_to_image"
    assert publication.generation_source["base_model"]["architecture"] == "krea2"
    assert publication.technical_inventory is not None
    counts = publication.technical_inventory["node_counts"]
    assert list(counts) == [
        "compiled_api",
        "compiled_orphans",
        "editable_root",
        "editable_subgraph_nodes",
        "output_reachable",
        "subgraph_definitions",
    ]
    assert counts["output_reachable"] + counts["compiled_orphans"] == counts["compiled_api"]
    fixed, public_choice = publication.technical_inventory["loras"]
    assert fixed == {
        "artifact": "detail-style.safetensors",
        "strength": 0.75,
        "usage": "fixed_active",
    }
    assert public_choice["usage"] == "public_choice"
    assert public_choice["parameter_id"] == "lora"
    assert "artifact" not in public_choice
    assert "binding" not in str(public_choice)


def test_older_publication_without_metadata_remains_discoverable() -> None:
    def remove_metadata(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest.pop("generation_source")
        manifest.pop("technical_inventory")

    publication = validate(build_publication_bundle("krea", mutate_manifest=remove_metadata))

    assert publication.readiness == "ready"
    assert publication.generation_source is None
    assert publication.technical_inventory is None
    assert publication.metadata_diagnostics == ()


def test_unknown_metadata_values_entries_warnings_and_fields_are_preserved() -> None:
    def add_future_metadata(manifest) -> None:  # type: ignore[no-untyped-def]
        source = manifest["generation_source"]
        source["generation_type"] = "spatial_remix"
        source["future_profile"] = {"rank": 7}
        source["technologies"].append(
            {"id": "future_tech", "label": "Future Tech", "category": "future"}
        )
        inventory = manifest["technical_inventory"]
        inventory["loras"].append(
            {"usage": "future_dynamic", "future_binding_id": "safe-public-id"}
        )
        inventory["technologies"].append(
            {
                "id": "future_tech",
                "label": "Future Tech",
                "category": "future",
                "future_hint": True,
            }
        )
        inventory["warnings"].append("future_inventory_warning")
        inventory["unclassified_loaders"].append(
            {"class_type": "SomeCustomModelProvider", "future_hint": True}
        )

    publication = validate(build_publication_bundle("krea", mutate_manifest=add_future_metadata))

    assert publication.metadata_diagnostics == ()
    assert publication.generation_source is not None
    assert publication.generation_source["generation_type"] == "spatial_remix"
    assert publication.generation_source["future_profile"] == {"rank": 7}
    assert publication.generation_source["technologies"][-1]["id"] == "future_tech"
    assert publication.technical_inventory is not None
    assert publication.technical_inventory["loras"][-1] == {
        "usage": "future_dynamic",
        "future_binding_id": "safe-public-id",
    }
    assert publication.technical_inventory["technologies"][-1]["future_hint"] is True
    assert publication.technical_inventory["warnings"] == ["future_inventory_warning"]
    assert publication.technical_inventory["unclassified_loaders"][-1] == {
        "class_type": "SomeCustomModelProvider",
        "future_hint": True,
    }


def test_inventory_node_count_inconsistency_is_nonfatal_and_diagnostic() -> None:
    def break_arithmetic(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["technical_inventory"]["node_counts"]["compiled_orphans"] = 1

    publication = validate(build_publication_bundle("krea", mutate_manifest=break_arithmetic))

    assert publication.technical_inventory is not None
    assert publication.metadata_diagnostics == (
        "technical_inventory_node_count_arithmetic_mismatch",
    )
    assert publication.warnings == (TECHNICAL_INVENTORY_COUNT_WARNING,)
    assert publication.readiness == "ready_with_warnings"


def test_public_choice_inventory_cannot_expose_private_filename_bindings() -> None:
    def add_private_binding(manifest) -> None:  # type: ignore[no-untyped-def]
        public_choice = manifest["technical_inventory"]["loras"][0]
        public_choice["artifact"] = "private-choice-binding.safetensors"

    publication = validate(build_publication_bundle("krea", mutate_manifest=add_private_binding))

    assert publication.generation_source is not None
    assert publication.technical_inventory is None
    assert publication.metadata_diagnostics == ("technical_inventory_invalid",)
    assert publication.readiness == "ready_with_warnings"
    assert "private-choice-binding" not in str(publication.public_interface)


def test_malformed_known_metadata_is_omitted_without_rejecting_the_source() -> None:
    def break_known_field_type(manifest) -> None:  # type: ignore[no-untyped-def]
        option = manifest["technical_inventory"]["loras"][0]["options"][0]
        option["default_strength"] = "maximum"

    publication = validate(build_publication_bundle("krea", mutate_manifest=break_known_field_type))

    assert publication.technical_inventory is None
    assert publication.metadata_diagnostics == ("technical_inventory_invalid",)
    assert publication.readiness == "ready_with_warnings"
    assert publication.api_document


@pytest.mark.parametrize(
    ("mutate_artifacts", "mutate_manifest", "code"),
    [
        (
            lambda manifest, workflow, api: api["202"].pop("inputs"),
            None,
            "manifest_invalid",
        ),
        (
            None,
            lambda manifest: manifest["api"].__setitem__("node_count", 999),
            "api_node_count_mismatch",
        ),
        (
            None,
            lambda manifest: manifest["interface"]["inputs"][-2]["bindings"][0].__setitem__(
                "node_id", "999"
            ),
            "manifest_invalid",
        ),
        (
            None,
            lambda manifest: manifest["interface"]["inputs"][-2]["choices"].__setitem__(0, {}),
            "manifest_invalid",
        ),
        (
            None,
            lambda manifest: manifest["interface"]["inputs"][-2].__setitem__(
                "type", "future-choice"
            ),
            "manifest_invalid",
        ),
        (
            None,
            lambda manifest: manifest["interface"]["outputs"][-1].__setitem__("type", "text"),
            "manifest_invalid",
        ),
        (
            None,
            lambda manifest: manifest["dependencies"]["class_types"].remove("CIFChoiceParameter"),
            "manifest_invalid",
        ),
        (
            None,
            lambda manifest: manifest.__setitem__("source_id", "workflows/other/source.json"),
            "manifest_invalid",
        ),
        (
            None,
            lambda manifest: manifest.__setitem__(
                "contract_schema", "comfyui-image-frontend.interface/v2"
            ),
            "unsupported_contract_schema",
        ),
    ],
)
def test_editable_workflow_drift_does_not_relax_frozen_contract_validation(
    mutate_artifacts, mutate_manifest, code: str
) -> None:  # type: ignore[no-untyped-def]
    bundle = build_publication_bundle(
        corrupt="workflow",
        mutate_artifacts=mutate_artifacts,
        mutate_manifest=mutate_manifest,
    )

    with pytest.raises(ContractError) as exc:
        validate(bundle)

    assert exc.value.code == code


def test_api_node_count_mismatch_is_rejected() -> None:
    bundle = build_publication_bundle(
        mutate_manifest=lambda manifest: manifest["api"].__setitem__("node_count", 999)
    )
    with pytest.raises(ContractError) as exc:
        validate(bundle)
    assert exc.value.code == "api_node_count_mismatch"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest["interface"]["inputs"][1].__setitem__("id", "prompt"),
        lambda manifest: manifest["interface"]["inputs"][1].__setitem__("id", "Width.Invalid"),
        lambda manifest: manifest["interface"]["inputs"][1].__setitem__("step", 0),
        lambda manifest: manifest["interface"]["inputs"][1].__setitem__("default", 17),
    ],
)
def test_duplicate_invalid_ids_and_invalid_numeric_contracts_are_rejected(mutation) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutation))
    assert exc.value.code == "manifest_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest["interface"]["inputs"][1].__setitem__("minimum", -(2**53)),
        lambda manifest: manifest["interface"]["inputs"][1].__setitem__("maximum", 2**53),
        lambda manifest: manifest["interface"]["inputs"][1].__setitem__("step", 2**53),
        lambda manifest: (
            manifest["interface"]["inputs"][1].__setitem__("maximum", 2**53),
            manifest["interface"]["inputs"][1].__setitem__("default", 2**53),
        ),
    ],
)
def test_ordinary_integer_contracts_must_remain_browser_safe(mutation) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutation))

    assert exc.value.code == "manifest_invalid"
    assert "browser-safe integer" in exc.value.message


def test_seed_contracts_can_exceed_browser_safe_range_for_decimal_string_transport() -> None:
    seed_maximum = 2**53

    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["interface"]["inputs"][3]["maximum"] = seed_maximum

    publication = validate(build_publication_bundle(mutate_manifest=mutate))

    seed = publication.public_interface["inputs"][3]
    assert seed["type"] == "seed"
    assert seed["maximum"] == seed_maximum


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest["interface"]["inputs"][0]["bindings"][0].__setitem__(
            "node_id", "999"
        ),
        lambda manifest: manifest["interface"]["inputs"][0]["bindings"][0].__setitem__(
            "input", "missing"
        ),
        lambda manifest: manifest["interface"]["inputs"][0]["bindings"][0].__setitem__(
            "class_type", "CIFIntegerParameter"
        ),
    ],
)
def test_private_binding_target_and_class_must_match_frozen_graph(mutation) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutation))
    assert exc.value.code == "manifest_invalid"


def test_typed_parameter_binding_must_patch_value_even_if_another_input_exists() -> None:
    def mutate(manifest, _workflow, api) -> None:  # type: ignore[no-untyped-def]
        api["10"]["inputs"]["label"] = "Prompt"
        manifest["interface"]["inputs"][0]["bindings"][0]["input"] = "label"

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_artifacts=mutate))
    assert exc.value.code == "manifest_invalid"


def test_binding_runtime_input_type_must_match_declared_parameter_type() -> None:
    object_info = object_info_fixture()
    object_info["CIFTextParameter"]["input"]["required"]["value"] = ["INT"]

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(), object_info=object_info)
    assert exc.value.code == "manifest_invalid"


@pytest.mark.parametrize("positive_count", [0, 2])
def test_exactly_one_positive_prompt_semantic_role_is_required(positive_count: int) -> None:
    def mutate(manifest) -> None:  # type: ignore[no-untyped-def]
        inputs = manifest["interface"]["inputs"]
        inputs[0]["semantic_role"] = "description" if positive_count == 0 else "positive_prompt"
        inputs[1]["semantic_role"] = "positive_prompt" if positive_count == 2 else "width"

    with pytest.raises(ContractError) as exc:
        validate(build_publication_bundle(mutate_manifest=mutate))
    assert exc.value.code == "manifest_invalid"
    assert "positive_prompt" in exc.value.message


def test_missing_runtime_dependency_marks_source_unavailable_without_contract_rejection() -> None:
    object_info = object_info_fixture()
    object_info.pop("FakeImageOutput")
    publication = validate(build_publication_bundle(), object_info=object_info)

    assert publication.readiness == "dependency_missing"
    assert publication.missing_dependencies == ("FakeImageOutput",)
