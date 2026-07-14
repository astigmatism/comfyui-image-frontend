from __future__ import annotations

import hashlib

import pytest
from app.domain.publication import parse_json_object, validate_publication
from app.errors import ContractError
from tests.publication_fixtures import (
    KREA_PUBLICATION_ID,
    PublicationBundle,
    build_publication_bundle,
    object_info_fixture,
)


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
    assert publication.node_count == 10
    assert publication.readiness == "ready"
    assert publication.warnings == ()
    inputs = publication.public_interface["inputs"]
    assert [value["id"] for value in inputs] == [
        "prompt",
        "width",
        "height",
        "seed",
        "enable_seedvr2_upscale",
        "knpv4_1_strength",
    ]
    assert sum(not value["advanced"] for value in inputs) == 5
    assert inputs[-1]["advanced"] is True
    assert inputs[3]["default"] is None
    assert inputs[3]["maximum"] == 1125899906842624
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


@pytest.mark.parametrize(
    ("artifact", "code"),
    [("workflow", "workflow_hash_mismatch"), ("api", "api_hash_mismatch")],
)
def test_hashes_cover_exact_raw_artifact_bytes(artifact: str, code: str) -> None:
    bundle = build_publication_bundle(corrupt=artifact)
    # A trailing newline preserves valid JSON but changes the authoritative exact bytes.
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
