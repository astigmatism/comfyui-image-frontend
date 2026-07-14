from __future__ import annotations

import pytest
from app.domain.publication import validate_publication
from app.domain.results import (
    history_status_indicates_interruption,
    normalize_history,
    project_public_declared_outputs,
    project_public_result,
)
from app.services.generations import _public_raw_history
from tests.publication_fixtures import (
    NO_PUBLISHER_WARNING,
    PublicationBundle,
    build_publication_bundle,
    object_info_fixture,
)


def contract(bundle: PublicationBundle) -> dict[str, object]:
    publication = validate_publication(
        instance_id="test-instance",
        manifest_path=bundle.manifest_path,
        manifest_bytes=bundle.manifest_bytes,
        workflow_bytes=bundle.workflow_bytes,
        api_bytes=bundle.api_bytes,
        object_info=object_info_fixture(),
        manifest_max_bytes=1024 * 1024,
        workflow_max_bytes=1024 * 1024,
        api_max_bytes=1024 * 1024,
    )
    return publication.private_contract


def image(filename: str, *, subfolder: str = "", storage_type: str = "output") -> dict[str, str]:
    return {"filename": filename, "subfolder": subfolder, "type": storage_type}


def native_only_contract() -> dict[str, object]:
    return {"schema": "comfyui-image-frontend.interface/v1", "outputs": []}


def publisher_metadata(
    source_contract: dict[str, object],
    output_id: str,
    artifacts: list[dict[str, object]],
    **overrides: object,
) -> dict[str, object]:
    declaration = next(
        value
        for value in source_contract["outputs"]  # type: ignore[index]
        if isinstance(value, dict) and value.get("id") == output_id
    )
    result: dict[str, object] = {
        "schema_version": source_contract["schema"],
        "output_id": output_id,
        "instance_uuid": declaration["instance_uuid"],
        "role": declaration["role"],
        "kind": declaration["kind"],
        "cardinality": declaration.get("cardinality", "many"),
        "description": declaration.get("description", ""),
        "artifacts": artifacts,
    }
    result.update(overrides)
    return result


def test_historical_native_only_contract_preserves_complete_multi_node_history() -> None:
    source_contract = native_only_contract()
    history = {
        "outputs": {
            "900": {
                "images": [image("first.png"), image("second.png", subfolder="batch")],
                "text": ["native output metadata"],
            },
            "901": {
                "preview": {"files": [image("third.png", storage_type="temp")]},
                "ui": {"arbitrary": {"score": 0.875}},
            },
        },
        "status": {"status_str": "success", "completed": True},
    }

    result = normalize_history(
        history,
        contract=source_contract,
        warnings=[NO_PUBLISHER_WARNING],
    )

    assert result.declared_outputs == {}
    assert result.unmapped_outputs == history["outputs"]
    assert [(value.node_id, value.output_id, value.batch_index) for value in result.files] == [
        ("900", "native:900", 0),
        ("900", "native:900", 1),
        ("901", "native:901", 0),
    ]
    assert [value.reference["filename"] for value in result.files] == [
        "first.png",
        "second.png",
        "third.png",
    ]
    assert all(value.declared is False and value.role == "unmapped" for value in result.files)
    assert result.status == history["status"]
    assert result.errors == ()
    assert result.warnings == (NO_PUBLISHER_WARNING,)


def test_declared_publisher_is_mapped_without_discarding_unrelated_native_outputs() -> None:
    source_contract = contract(build_publication_bundle("generic"))
    logical_references = [
        {"batch_index": 4, **image("final-1.png")},
        {"batch_index": 1, **image("final-0.png")},
    ]
    publisher_output = {
        # Ordinary images are deliberately in a different order and include an extra locator.
        # Publisher artifacts, not recursive image discovery, are authoritative here.
        "images": [image("ignored.png"), image("final-0.png"), image("final-1.png")],
        "ui": {"timing": {"seconds": 2.4}},
        "comfyui_image_frontend": [
            publisher_metadata(source_contract, "final_image", logical_references)
        ],
    }
    native_output = {
        "images": [image("debug.png")],
        "text": ["unrelated node output"],
    }
    result = normalize_history(
        {
            "outputs": {"130": publisher_output, "900": native_output},
            "status": {"status_str": "success"},
        },
        contract=source_contract,
    )

    assert set(result.declared_outputs) == {"final_image"}
    declared = result.declared_outputs["final_image"]
    assert declared["role"] == "final"
    assert declared["cardinality"] == "many"
    assert declared["description"] == "Declared generic final image."
    assert declared["artifacts"] == logical_references
    assert "output" not in declared
    assert "node_id" not in declared
    assert result.unmapped_outputs == {"900": native_output}
    assert [
        (value.output_id, value.batch_index, value.reference["filename"], value.declared)
        for value in result.files
    ] == [
        ("final_image", 4, "final-1.png", True),
        ("final_image", 1, "final-0.png", True),
        ("native:900", 0, "debug.png", False),
    ]
    assert result.errors == ()


def test_multiple_publishers_follow_manifest_order_and_preserve_duplicate_physical_references() -> (
    None
):
    source_contract: dict[str, object] = {
        "schema": "comfyui-image-frontend.interface/v1",
        "outputs": [
            {
                "id": "preview",
                "node_id": "200",
                "instance_uuid": "00000000-0000-4000-8000-000000000200",
                "role": "preview",
                "kind": "image",
                "label": "Preview",
                "cardinality": "many",
                "description": "Earlier image",
            },
            {
                "id": "final",
                "node_id": "201",
                "instance_uuid": "00000000-0000-4000-8000-000000000201",
                "role": "final",
                "kind": "image",
                "label": "Final",
                "cardinality": "many",
                "description": "Authoritative image",
            },
        ],
    }
    same_locator = image("shared.png", subfolder="publisher")
    history = {
        # History order is deliberately the reverse of the frozen declaration order.
        "outputs": {
            "201": {
                "images": [same_locator],
                "comfyui_image_frontend": [
                    publisher_metadata(
                        source_contract,
                        "final",
                        [{"batch_index": 0, **same_locator}],
                    )
                ],
            },
            "200": {
                "images": [same_locator, same_locator],
                "comfyui_image_frontend": [
                    publisher_metadata(
                        source_contract,
                        "preview",
                        [
                            {"batch_index": 8, **same_locator},
                            {"batch_index": 9, **same_locator},
                        ],
                    )
                ],
            },
        }
    }

    result = normalize_history(history, contract=source_contract)

    assert list(result.declared_outputs) == ["preview", "final"]
    assert [value["batch_index"] for value in result.declared_outputs["preview"]["artifacts"]] == [
        8,
        9,
    ]
    assert [
        (value.output_id, value.sequence, value.batch_index, value.reference["filename"])
        for value in result.files
    ] == [
        ("preview", 0, 8, "shared.png"),
        ("preview", 0, 9, "shared.png"),
        ("final", 1, 0, "shared.png"),
    ]
    assert result.unmapped_outputs == {}
    assert result.errors == ()


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("schema_version", "comfyui-image-frontend.interface/v2"),
        ("instance_uuid", "00000000-0000-4000-8000-000000000999"),
        ("role", "preview"),
        ("kind", "text"),
        ("cardinality", "one"),
        ("description", "stale description"),
    ],
)
def test_publisher_metadata_must_match_every_frozen_semantic_field(
    field: str, wrong_value: str
) -> None:
    source_contract = contract(build_publication_bundle("generic"))
    metadata = publisher_metadata(
        source_contract,
        "final_image",
        [{"batch_index": 0, **image("final.png")}],
        **{field: wrong_value},
    )

    result = normalize_history(
        {
            "outputs": {
                "130": {
                    "images": [image("final.png")],
                    "comfyui_image_frontend": [metadata],
                }
            }
        },
        contract=source_contract,
    )

    assert result.declared_outputs == {}
    assert result.unmapped_outputs == {}
    assert [
        (value.output_id, value.reference["filename"], value.declared) for value in result.files
    ] == [("native:130", "final.png", False)]
    assert result.errors[0] == {
        "code": "publisher_metadata_mismatch",
        "message": "ComfyUI publisher metadata did not match the frozen declaration.",
        "output_id": "final_image",
        "field": field,
    }
    assert result.errors[-1]["code"] == "missing_publisher_output"


def test_publisher_without_valid_namespaced_artifacts_archives_native_fallback_only() -> None:
    source_contract = contract(build_publication_bundle("generic"))
    publisher_output = {
        "images": [image("partial.png", storage_type="temp")],
        "text": ["publisher partial result remains only in raw history"],
        "comfyui_image_frontend": [
            publisher_metadata(
                source_contract,
                "final_image",
                [
                    {
                        "batch_index": "invalid",
                        **image("partial.png", storage_type="temp"),
                    }
                ],
            )
        ],
    }

    result = normalize_history(
        {"outputs": {"130": publisher_output}},
        contract=source_contract,
    )

    assert result.unmapped_outputs == {}
    assert result.declared_outputs["final_image"]["artifacts"] == []
    assert [
        (value.output_id, value.batch_index, value.reference, value.declared)
        for value in result.files
    ] == [
        (
            "native:130",
            0,
            image("partial.png", storage_type="temp"),
            False,
        )
    ]
    assert result.errors == (
        {
            "code": "publisher_artifact_invalid",
            "message": "ComfyUI returned a malformed publisher artifact reference.",
            "output_id": "final_image",
            "artifact_index": 0,
        },
    )


def test_nested_file_discovery_is_allowlisted_deduplicated_and_ordered() -> None:
    repeated = image("same.png", subfolder="nested")
    output = {
        "images": [repeated, {"filename": "unsafe.png", "type": "external"}],
        "nested": {"again": repeated, "preview": image("preview.png", storage_type="temp")},
    }

    result = normalize_history(
        {"outputs": {42: output}},
        contract=contract(build_publication_bundle("krea")),
    )

    assert [value.reference for value in result.files] == [
        image("same.png", subfolder="nested"),
        image("preview.png", storage_type="temp"),
    ]
    assert result.unmapped_outputs == {"42": output}


def test_unmapped_generic_file_locator_is_not_misclassified_as_an_image() -> None:
    output = {
        "files": [image("metadata.json")],
        "images": [image("preview.png", storage_type="temp")],
    }

    result = normalize_history(
        {"outputs": {42: output}},
        contract=contract(build_publication_bundle("krea")),
    )

    assert [(value.reference["filename"], value.kind) for value in result.files] == [
        ("metadata.json", "file"),
        ("preview.png", "image"),
    ]


def test_undeclared_publisher_metadata_is_an_error_but_native_payload_remains_recoverable() -> None:
    output = {
        "images": [image("recoverable.png")],
        "comfyui_image_frontend": {"output_id": "private_or_stale_output"},
    }

    result = normalize_history(
        {"outputs": {"777": output}, "status": {"status_str": "success"}},
        contract=contract(build_publication_bundle("generic")),
    )

    assert result.declared_outputs == {}
    assert result.unmapped_outputs == {"777": output}
    assert result.files[0].reference["filename"] == "recoverable.png"
    assert result.errors[0] == {
        "code": "undeclared_publisher_output",
        "message": "ComfyUI returned publisher metadata for an undeclared output.",
    }
    assert result.errors[-1]["code"] == "missing_publisher_output"


def test_declared_publisher_metadata_from_wrong_node_remains_unmapped() -> None:
    output = {
        "images": [image("recoverable.png")],
        "ui": {"comfyui_image_frontend": {"output_id": "final_image"}},
    }

    result = normalize_history(
        {"outputs": {"777": output}, "status": {"status_str": "success"}},
        contract=contract(build_publication_bundle("generic")),
    )

    assert result.declared_outputs == {}
    assert result.unmapped_outputs == {"777": output}
    assert result.files[0].output_id == "native:777"
    assert result.files[0].reference["filename"] == "recoverable.png"
    assert result.errors[0] == {
        "code": "publisher_binding_mismatch",
        "message": "ComfyUI returned declared publisher metadata from an unexpected output node.",
        "output_id": "final_image",
    }
    assert result.errors[-1]["code"] == "missing_publisher_output"


def test_public_projection_preserves_messages_and_node_keys_but_redacts_private_data() -> None:
    private = {
        "prompt": [0, "prompt-id", {"191": {"inputs": {"value": "private graph"}}}],
        "extra_data": {"extra_pnginfo": {"workflow": {"nodes": [{"id": 191}]}}},
        "outputs": {
            "156:155": {
                "images": [image("safe.png", subfolder="CivitAI")],
                "text": ["safe native output"],
                "path": "/workspace/ComfyUI/output/CivitAI/safe.png",
                "ui": {
                    "timing": {"seconds": 1.5},
                    "comfyui_image_frontend": {
                        "output_id": "final_image",
                        "publisher_instance": "00000000-0000-4000-8000-000000000130",
                    },
                },
            }
        },
        "status": {
            "status_str": "error",
            "completed": False,
            "messages": [
                [
                    "execution_error",
                    {
                        "prompt_id": "prompt-id",
                        "exception_type": "RuntimeError",
                        "exception_message": (
                            "The node could not execute at /workspace/ComfyUI/private.py or "
                            "C:\\ComfyUI\\private.py"
                        ),
                        "node_id": "191",
                        "node_type": "PrivateNode",
                        "traceback": ["File /workspace/ComfyUI/custom_nodes/private.py"],
                        "current_inputs": {"authorization": "Bearer secret"},
                        "current_outputs": {"graph": {"private": True}},
                        "userdata_path": "workflows/private/source.json",
                    },
                ]
            ],
        },
    }

    projected = project_public_result(private)

    assert set(projected) == {"outputs", "status"}
    assert set(projected["outputs"]) == {"156:155"}
    assert projected["outputs"]["156:155"] == {
        "images": [image("safe.png", subfolder="CivitAI")],
        "text": ["safe native output"],
        "ui": {"timing": {"seconds": 1.5}},
    }
    assert projected["status"]["status_str"] == "error"
    assert projected["status"]["completed"] is False
    message_type, message = projected["status"]["messages"][0]
    assert message_type == "execution_error"
    assert message == {
        "prompt_id": "prompt-id",
        "exception_type": "RuntimeError",
        "exception_message": ("The node could not execute at [redacted path] or [redacted path]"),
    }
    assert "/workspace/" not in str(projected)
    assert "Bearer secret" not in str(projected)


def test_public_declared_output_projection_is_ordered_and_enriches_logical_references() -> None:
    projected = project_public_declared_outputs(
        {
            "final_image": {
                "schema_version": "comfyui-image-frontend.interface/v1",
                "output_id": "final_image",
                "role": "final",
                "kind": "image",
                "description": "Final image",
                "cardinality": "many",
                "artifacts": [{"batch_index": 7, **image("final.png")}],
                "node_id": "130",
                "metadata": {"publisher_instance": "private-uuid"},
                "output": {
                    "images": [image("final.png")],
                    "ui": {
                        "comfyui_image_frontend": {
                            "output_id": "final_image",
                            "publisher_instance": "private-uuid",
                        }
                    },
                },
            },
            "preview": {
                "output_id": "preview",
                "role": "preview",
                "kind": "image",
                "description": "Earlier pass",
                "cardinality": "many",
                "artifacts": [{"batch_index": 0, **image("preview.png")}],
            },
        },
        output_order=["preview", "final_image"],
        artifacts=[
            {
                "id": "artifact-final",
                "output_id": "final_image",
                "role": "final",
                "kind": "image",
                "state": "final",
                "sequence": 2,
                "batch_index": 7,
                "canonical": True,
                "best_available": True,
                "content_url": "/api/artifacts/artifact-final/content",
                "thumbnail_url": "/api/artifacts/artifact-final/thumbnail",
                "available_at": "2026-07-13T12:00:00Z",
            }
        ],
    )

    assert [value["output_id"] for value in projected] == ["preview", "final_image"]
    assert projected[0]["artifacts"] == [
        {
            "batch_index": 0,
            **image("preview.png"),
            "artifact": None,
        }
    ]
    assert projected[1] == {
        "schema_version": "comfyui-image-frontend.interface/v1",
        "id": "final_image",
        "output_id": "final_image",
        "role": "final",
        "kind": "image",
        "cardinality": "many",
        "description": "Final image",
        "artifacts": [
            {
                "batch_index": 7,
                **image("final.png"),
                "artifact": {
                    "id": "artifact-final",
                    "output_id": "final_image",
                    "role": "final",
                    "kind": "image",
                    "state": "final",
                    "sequence": 2,
                    "batch_index": 7,
                    "canonical": True,
                    "best_available": True,
                    "content_url": "/api/artifacts/artifact-final/content",
                    "thumbnail_url": "/api/artifacts/artifact-final/thumbnail",
                    "available_at": "2026-07-13T12:00:00Z",
                },
            }
        ],
    }


def test_public_history_removes_only_root_graph_envelopes_and_preserves_results_untouched() -> None:
    history = {
        "prompt": [0, "prompt-id", {"191": {"inputs": {"secret": "graph"}}}],
        "extra_data": {"extra_pnginfo": {"workflow": {"nodes": []}}},
        "workflow": {"nodes": [{"id": 191}]},
        "outputs": {
            "191": {
                "prompt": "custom UI prompt text",
                "graph": {"score": 0.9},
                "bindings": [{"label": "custom binding metadata"}],
                "token_count": 123,
                "path": "/workspace/custom/output.txt",
                "content_url": "/api/artifacts/a/content",
                "comfyui_image_frontend": [
                    {
                        "schema_version": "comfyui-image-frontend.interface/v1",
                        "output_id": "final",
                        "instance_uuid": "publisher-instance",
                    }
                ],
            }
        },
        "status": {
            "status_str": "error",
            "messages": [
                [
                    "execution_error",
                    {
                        "node_id": "191",
                        "traceback": ["File /workspace/custom/node.py"],
                        "current_inputs": {"arbitrary": True},
                    },
                ]
            ],
        },
        "executed": ["191"],
    }

    projected = _public_raw_history(history)

    assert set(projected) == {"outputs", "status", "executed"}
    assert projected["outputs"] == history["outputs"]
    assert projected["status"] == history["status"]
    assert projected["executed"] == ["191"]
    assert projected["outputs"] is not history["outputs"]


def test_failure_and_interruption_statuses_preserve_partial_outputs_and_add_structured_errors() -> (
    None
):
    source_contract = native_only_contract()
    partial = {"images": [image("partial.png")], "text": ["partial"]}

    failed = normalize_history(
        {"outputs": {"900": partial}, "status": {"status_str": "error", "messages": []}},
        contract=source_contract,
    )
    interrupted = normalize_history(
        {
            "outputs": {"900": partial},
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [["execution_interrupted", {"prompt_id": "native-prompt"}]],
            },
        },
        contract=source_contract,
    )

    assert failed.unmapped_outputs == {"900": partial}
    assert failed.files[0].reference["filename"] == "partial.png"
    assert failed.errors[-1]["code"] == "comfyui_execution_failed"
    assert interrupted.unmapped_outputs == {"900": partial}
    assert interrupted.files[0].reference["filename"] == "partial.png"
    assert interrupted.errors[-1]["code"] == "comfyui_execution_interrupted"
    assert not any(value["code"] == "comfyui_execution_failed" for value in interrupted.errors)


def test_real_comfyui_interruption_message_shapes_override_error_status() -> None:
    for message in (
        ["execution_interrupted", {"prompt_id": "native-prompt"}],
        {"type": "execution_cancelled", "prompt_id": "native-prompt"},
        "execution_canceled",
    ):
        assert history_status_indicates_interruption(
            {"status_str": "error", "completed": False, "messages": [message]}
        )
