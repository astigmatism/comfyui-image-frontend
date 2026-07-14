from __future__ import annotations

from pathlib import Path
from typing import Any

from app.main import create_app
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.helpers import (
    create_generation,
    generation_payload,
    provision_user,
    wait_for_generation,
    wait_for_status,
)
from tests.publication_fixtures import NO_PUBLISHER_WARNING, build_publication_bundle

TERMINAL = {
    "succeeded",
    "cancelled_with_artifacts",
    "cancelled_without_artifacts",
    "failed_with_artifacts",
    "failed_without_artifacts",
    "interrupted",
}


def _multi_publisher_bundle():  # type: ignore[no-untyped-def]
    def add_publishers(
        manifest: dict[str, Any], workflow: dict[str, Any], api: dict[str, Any]
    ) -> None:
        publishers = [
            (
                "130",
                "base",
                "preview",
                "Base prototype",
                "Fixture base-stage prototype.",
                "00000000-0000-4000-8000-000000000130",
            ),
            (
                "131",
                "second_pass",
                "comparison",
                "Second pass",
                "Fixture comparison-stage image.",
                "00000000-0000-4000-8000-000000000131",
            ),
            (
                "132",
                "final",
                "final",
                "Final image",
                "Fixture authored final image.",
                "00000000-0000-4000-8000-000000000132",
            ),
        ]
        workflow["nodes"] = [
            node
            for node in workflow["nodes"]
            if not (isinstance(node, dict) and node.get("type") == "CIFPublishImage")
        ]
        manifest_outputs: list[dict[str, Any]] = []
        for node_id, output_id, role, label, description, instance_uuid in publishers:
            workflow["nodes"].append(
                {"id": int(node_id), "type": "CIFPublishImage", "widgets_values": []}
            )
            api[node_id] = {
                "class_type": "CIFPublishImage",
                "inputs": {
                    "images": ["120", 0],
                    "output_id": output_id,
                    "instance_uuid": instance_uuid,
                    "role": role,
                    "cardinality": "many",
                    "description": description,
                },
            }
            manifest_outputs.append(
                {
                    "id": output_id,
                    "instance_uuid": instance_uuid,
                    "label": label,
                    "description": description,
                    "role": role,
                    "type": "image",
                    "cardinality": "many",
                    "node_id": node_id,
                }
            )
        manifest["interface"]["outputs"] = manifest_outputs
        manifest["interface"]["native_outputs"] = {
            "120": {"class_type": "FakeImageOutput", "title": "Native image"},
            **{
                node_id: {"class_type": "CIFPublishImage", "title": label}
                for node_id, _, _, label, _, _ in publishers
            },
        }

    return build_publication_bundle("generic", mutate_artifacts=add_publishers)


def _declared_output_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def test_progressive_success_multiple_outputs_and_exact_recall(
    settings_factory, fake_state
) -> None:
    # Leave enough deterministic fake-runtime time for the worker to subscribe before the
    # progressive event, even on a heavily loaded full-suite run.
    fake_state.initial_event_delay = 0.5
    fake_state.default_stage_delay = 0.5
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client)
        accepted = create_generation(client, "multi gallery image", seed="random")
        generation_id = accepted["id"]
        assert accepted["status"] == "queued"

        progressive = wait_for_generation(
            client,
            generation_id,
            lambda item: item["artifact_count"] >= 1 and item["status"] not in TERMINAL,
        )
        assert progressive["canonical_artifact_id"] is None
        assert progressive["best_available_artifact_id"] is not None

        complete = wait_for_status(client, generation_id, "succeeded")
        assert complete["artifact_count"] == 8
        assert complete["final_artifact_count"] == 2
        assert complete["canonical_artifact_id"] is not None
        assert complete["best_available_artifact_id"] is not None
        assert sum(item["canonical"] for item in complete["artifacts"]) == 2
        assert sum(item["best_available"] for item in complete["artifacts"]) == 1
        assert {item["output_id"] for item in complete["artifacts"]} == {
            "native:900",
            "native:901",
            "base",
            "second_pass",
            "final",
        }

        gallery = client.get("/api/generations").json()["items"]
        assert [item["id"] for item in gallery].count(generation_id) == 1
        assert gallery[0]["display_artifact"]["state"] == "final"

        detail = client.get(f"/api/generations/{generation_id}").json()
        seed = detail["resolved_seeds"]["seed"]
        assert isinstance(seed, str) and seed.isdecimal()
        assert detail["final_prompt"] == "multi gallery image"
        assert {item["id"] for item in detail["input_definitions"]} == set(
            detail["effective_parameters"]
        )
        assert detail["input_definitions"][0]["label"] == "Prompt"
        assert all("bindings" not in item for item in detail["input_definitions"])
        assert set(detail["unmapped_outputs"]) == {"900", "901"}
        assert detail["unmapped_outputs"]["901"]["text"] == ["complete native text result"]
        assert [
            item.get("output_id", item.get("id"))
            for item in _declared_output_list(detail["declared_outputs"])
        ] == ["base", "second_pass", "final"]
        assert NO_PUBLISHER_WARNING not in detail["warnings"]
        assert detail["comfyui_status"]["status_str"] == "success"
        assert "prompt" not in detail["raw_history"]
        assert "extra_data" not in detail["raw_history"]
        assert set(detail["raw_history"]["outputs"]) == {
            "900",
            "901",
            "199",
            "200",
            "201",
        }
        progress_events = [
            event for event in detail["events"] if event["type"] == "generation.progress"
        ]
        assert progress_events
        assert all(set(event["payload"]) == {"value", "maximum"} for event in progress_events)
        assert fake_state.submitted[-1]["extra_data"] == {
            "extra_pnginfo": {"workflow": build_publication_bundle("krea").workflow()}
        }
        container = client.app.state.container
        with container.db.session_factory() as session:
            from app.models import Generation

            stored = session.get(Generation, generation_id)
            assert stored is not None
            assert "prompt" in stored.raw_history_json
            assert "extra_data" in stored.raw_history_json
            raw_outputs = dict(stored.raw_history_json["outputs"])
            raw_outputs["900"] = {
                **raw_outputs["900"],
                "path": "/workspace/ComfyUI/output/private.png",
                "bindings": [{"node_id": "900", "input": "value"}],
            }
            stored.raw_history_json = {
                **stored.raw_history_json,
                "outputs": raw_outputs,
            }
            unmapped = dict(stored.unmapped_outputs_json)
            unmapped["900"] = {
                **unmapped["900"],
                "path": "/workspace/ComfyUI/output/private.png",
                "bindings": [{"node_id": "900", "input": "value"}],
            }
            stored.unmapped_outputs_json = unmapped
            session.commit()

        projected = client.get(f"/api/generations/{generation_id}").json()
        assert set(projected["unmapped_outputs"]) == {"900", "901"}
        assert projected["unmapped_outputs"]["900"]["path"] == (
            "/workspace/ComfyUI/output/private.png"
        )
        assert projected["unmapped_outputs"]["900"]["bindings"] == [
            {"node_id": "900", "input": "value"}
        ]
        assert projected["raw_history"]["status"] == projected["comfyui_status"]
        assert projected["raw_history"]["outputs"]["900"]["path"] == (
            "/workspace/ComfyUI/output/private.png"
        )
        assert projected["raw_history"]["outputs"]["900"]["bindings"] == [
            {"node_id": "900", "input": "value"}
        ]
        assert "prompt" not in projected["raw_history"]
        assert "extra_data" not in projected["raw_history"]
        progress_events = [
            event for event in projected["events"] if event["type"] == "generation.progress"
        ]
        assert progress_events
        assert all("node" not in event["payload"] for event in progress_events)
        recall = client.get(f"/api/generations/{generation_id}/recall")
        assert recall.status_code == 200
        recalled = recall.json()
        assert recalled["available"] is True
        assert recalled["parameters"]["seed"] == seed
        assert recalled["parameters"]["prompt"] == "multi gallery image"
        assert recalled["source_key"] == detail["source_key"]
        assert recalled["revision"]["publication_id"] == detail["publication_id"]

        artifact = next(item for item in complete["artifacts"] if item["best_available"])
        content = client.get(artifact["content_url"])
        thumbnail = client.get(artifact["thumbnail_url"])
        assert content.status_code == 200 and content.headers["content-type"] == "image/png"
        assert thumbnail.status_code == 200 and thumbnail.headers["content-type"] == "image/webp"


def test_concurrent_choice_submissions_apply_public_values_and_option_strength_hints(
    settings_factory, fake_state
) -> None:
    fake_state.slow_stage_delay = 2.0
    settings = settings_factory(enable_background_worker=True, comfyui_concurrency=2)
    source_api = build_publication_bundle("krea").api()
    private_choice_node = source_api["202"]

    with TestClient(create_app(settings)) as client:
        provision_user(client, username="choice.isolation")
        requests = (
            ("slow choice v3", "knp_v3_1", 0.5, 601),
            ("slow choice v2", "knp_v2", 1.0, 602),
        )
        accepted: dict[str, dict[str, Any]] = {}
        for prompt, choice, _, seed in requests:
            payload = generation_payload(client, prompt, seed=seed)
            payload["parameters"]["lora"] = choice
            assert "lora_strength" not in payload["parameters"]
            response = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json=payload,
            )
            assert response.status_code == 201, response.text
            accepted[prompt] = response.json()

        # Slow fake executions plus two worker slots ensure the differently selected choices
        # are materialized at the same time, exercising per-generation graph isolation.
        for prompt, _, _, _ in requests:
            wait_for_generation(
                client,
                accepted[prompt]["id"],
                lambda item: item["status"] == "running",
            )
        assert all(
            client.get(f"/api/generations/{accepted[prompt]['id']}").json()["status"] == "running"
            for prompt, _, _, _ in requests
        )

        completed = {
            prompt: wait_for_status(client, accepted[prompt]["id"], "succeeded", timeout=10)
            for prompt, _, _, _ in requests
        }
        submitted = {item["prompt"]: item for item in fake_state.submitted}
        assert set(submitted) == {prompt for prompt, _, _, _ in requests}

        for prompt, choice, expected_strength, _ in requests:
            detail = completed[prompt]
            assert detail["requested_controls"]["lora"] == choice
            assert "lora_strength" not in detail["requested_controls"]
            assert detail["effective_controls"]["lora"] == choice
            assert detail["effective_controls"]["lora_strength"] == expected_strength

            submission = submitted[prompt]
            assert submission["choice"] == choice
            assert submission["strength"] == expected_strength
            assert submission["graph"]["202"] == {
                **private_choice_node,
                "inputs": {**private_choice_node["inputs"], "value": choice},
            }
            assert (
                submission["graph"]["202"]["inputs"]["options_json"]
                == private_choice_node["inputs"]["options_json"]
            )
            assert submission["graph"]["20"] == source_api["20"]
            assert submission["graph"]["20"]["inputs"]["lora_name"] == ["202", 0]

            assert detail["artifact_count"] == 5
            assert {item["output_id"] for item in detail["artifacts"]} == {
                "native:900",
                "native:901",
                "base",
                "second_pass",
                "final",
            }
            assert [
                item.get("output_id", item.get("id"))
                for item in _declared_output_list(detail["declared_outputs"])
            ] == ["base", "second_pass", "final"]
            assert set(detail["unmapped_outputs"]) == {"900", "901"}

        assert submitted["slow choice v3"]["graph"] is not submitted["slow choice v2"]["graph"]
        assert submitted["slow choice v3"]["graph"]["202"]["inputs"]["value"] == "knp_v3_1"
        assert submitted["slow choice v2"]["graph"]["202"]["inputs"]["value"] == "knp_v2"
        assert private_choice_node["inputs"]["value"] == "knp_v4_1"


def test_authored_multi_publisher_batches_and_native_history_are_exhaustive(
    settings_factory, fake_state
) -> None:
    publication = _multi_publisher_bundle()
    fake_state.workflow_files.update(publication.files)
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="authored.outputs")
        source = next(
            item
            for item in client.get("/api/workflows").json()
            if item["display_name"] == "Generic Landscape"
        )
        response = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json={
                "source_key": source["source_key"],
                "revision": source["revision"],
                "parameters": {
                    "prompt": "multi authored output inventory",
                    "iterations": 1,
                },
            },
        )
        assert response.status_code == 201, response.text
        complete = wait_for_status(client, response.json()["id"], "succeeded", timeout=10)

        declared = _declared_output_list(complete["declared_outputs"])
        assert [item.get("output_id", item.get("id")) for item in declared] == [
            "base",
            "second_pass",
            "final",
        ]
        assert [item["role"] for item in declared] == ["preview", "comparison", "final"]
        assert all(item["cardinality"] == "many" for item in declared)
        assert all(
            [artifact["batch_index"] for artifact in item["artifacts"]] == [0, 1]
            for item in declared
        )

        declared_artifacts = [
            artifact
            for artifact in complete["artifacts"]
            if artifact["output_id"] in {"base", "second_pass", "final"}
        ]
        assert len(declared_artifacts) == 6
        assert {
            (artifact["output_id"], artifact["role"], artifact["batch_index"])
            for artifact in declared_artifacts
        } == {
            ("base", "preview", 0),
            ("base", "preview", 1),
            ("second_pass", "comparison", 0),
            ("second_pass", "comparison", 1),
            ("final", "final", 0),
            ("final", "final", 1),
        }
        assert complete["final_artifact_count"] == 2
        assert all(
            client.get(artifact["content_url"]).status_code == 200
            for artifact in declared_artifacts
        )

        assert set(complete["unmapped_outputs"]) == {"900", "901"}
        assert complete["unmapped_outputs"]["901"] == {
            "images": complete["raw_history"]["outputs"]["901"]["images"],
            "text": ["complete native text result"],
            "hashes": {"asset_sha256": "f" * 64, "model_sha256": "e" * 64},
            "dimensions": {"width": 96, "height": 72},
            "custom_ui": {
                "palette": ["indigo", "gold"],
                "quality": {"score": 0.875, "accepted": True},
            },
        }
        assert set(complete["raw_history"]["outputs"]) == {"900", "901", "130", "131", "132"}
        assert complete["unmapped_outputs"]["900"] == complete["raw_history"]["outputs"]["900"]
        assert complete["unmapped_outputs"]["901"] == complete["raw_history"]["outputs"]["901"]
        assert len(complete["raw_history"]["outputs"]["130"]["images"]) == 2

        container = client.app.state.container
        with container.db.session_factory() as session:
            from app.models import Generation

            stored = session.get(Generation, complete["id"])
            assert stored is not None
            private_outputs = stored.raw_history_json["outputs"]
            for sequence, (node_id, output_id, role, instance_uuid, description) in enumerate(
                (
                    (
                        "130",
                        "base",
                        "preview",
                        "00000000-0000-4000-8000-000000000130",
                        "Fixture base-stage prototype.",
                    ),
                    (
                        "131",
                        "second_pass",
                        "comparison",
                        "00000000-0000-4000-8000-000000000131",
                        "Fixture comparison-stage image.",
                    ),
                    (
                        "132",
                        "final",
                        "final",
                        "00000000-0000-4000-8000-000000000132",
                        "Fixture authored final image.",
                    ),
                )
            ):
                native_output = private_outputs[node_id]
                assert len(native_output["images"]) == 2
                metadata = native_output["comfyui_image_frontend"]
                assert metadata == [
                    {
                        "schema_version": "comfyui-image-frontend.interface/v1",
                        "output_id": output_id,
                        "instance_uuid": instance_uuid,
                        "role": role,
                        "kind": "image",
                        "cardinality": "many",
                        "description": description,
                        "artifacts": [
                            {"batch_index": batch_index, **reference}
                            for batch_index, reference in enumerate(native_output["images"])
                        ],
                    }
                ]
                assert native_output["ui"]["publisher_timing"]["sequence"] == sequence


def test_terminal_websocket_event_retries_until_delayed_history_is_persisted(
    settings_factory, fake_state
) -> None:
    fake_state.history_delay_polls = 8
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="delayed.history")
        accepted = create_generation(client, "delayed history result", seed=501)

        complete = wait_for_status(client, accepted["id"], "succeeded", timeout=10)
        prompt_id = complete["prompt_id"]
        assert prompt_id
        client_id = fake_state.prompts[prompt_id]["client_id"]
        assert any(
            event["type"] == "execution_success" for event in fake_state.event_log[str(client_id)]
        )
        assert fake_state.history_calls[prompt_id] > fake_state.history_delay_polls

        assert complete["artifact_count"] == 5
        assert {item["output_id"] for item in complete["artifacts"]} == {
            "native:900",
            "native:901",
            "base",
            "second_pass",
            "final",
        }
        assert set(complete["raw_history"]["outputs"]) == {
            "900",
            "901",
            "199",
            "200",
            "201",
        }
        assert complete["comfyui_status"]["status_str"] == "success"


def test_cached_execution_reconciles_complete_history_without_ordinary_events(
    settings_factory, fake_state
) -> None:
    fake_state.emit_cached_only = True
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="cached.execution")
        accepted = create_generation(client, "cached history result", seed=502)

        complete = wait_for_status(client, accepted["id"], "succeeded", timeout=10)
        prompt_id = complete["prompt_id"]
        assert prompt_id
        client_id = str(fake_state.prompts[prompt_id]["client_id"])
        assert [event["type"] for event in fake_state.event_log[client_id]] == ["execution_cached"]

        assert complete["artifact_count"] == 5
        assert {item["output_id"] for item in complete["artifacts"]} == {
            "native:900",
            "native:901",
            "base",
            "second_pass",
            "final",
        }
        assert [
            item.get("output_id", item.get("id"))
            for item in _declared_output_list(complete["declared_outputs"])
        ] == ["base", "second_pass", "final"]
        assert set(complete["unmapped_outputs"]) == {"900", "901"}
        assert set(complete["raw_history"]["outputs"]) == {
            "900",
            "901",
            "199",
            "200",
            "201",
        }
        assert complete["comfyui_status"]["status_str"] == "success"


def test_websocket_success_without_history_becomes_interrupted_not_succeeded(
    settings_factory, fake_state
) -> None:
    fake_state.hide_history = True
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="missing.history")
        accepted = create_generation(client, "history never persisted", seed=504)

        interrupted = wait_for_status(client, accepted["id"], "interrupted", timeout=10)
        prompt_id = interrupted["prompt_id"]
        assert prompt_id
        client_id = str(fake_state.prompts[prompt_id]["client_id"])
        assert any(
            event["type"] == "execution_success" for event in fake_state.event_log[client_id]
        )
        assert interrupted["raw_history"] == {}
        assert interrupted["comfyui_status"] == {}
        assert interrupted["error_code"] == "execution_interrupted"


def test_cached_hint_with_nonterminal_history_never_authorizes_success(
    settings_factory, fake_state
) -> None:
    fake_state.emit_cached_only = True
    fake_state.force_nonterminal_history = True
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="cached.nonterminal")
        accepted = create_generation(client, "cached history remains partial", seed=505)

        interrupted = wait_for_status(client, accepted["id"], "interrupted", timeout=10)
        prompt_id = interrupted["prompt_id"]
        assert prompt_id
        client_id = str(fake_state.prompts[prompt_id]["client_id"])
        assert [event["type"] for event in fake_state.event_log[client_id]] == ["execution_cached"]
        assert interrupted["comfyui_status"] == {
            "status_str": "running",
            "completed": False,
            "messages": [],
        }
        assert set(interrupted["raw_history"]["outputs"]) == {
            "900",
            "901",
            "199",
            "200",
            "201",
        }
        assert interrupted["error_code"] == "execution_interrupted"


def test_terminal_success_history_overrides_websocket_execution_error(
    settings_factory, fake_state
) -> None:
    fake_state.terminal_event_type = "execution_error"
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="history.overrides.websocket")
        accepted = create_generation(client, "history truth wins", seed=506)

        succeeded = wait_for_status(client, accepted["id"], "succeeded", timeout=10)
        assert succeeded["comfyui_status"]["status_str"] == "success"
        assert succeeded["error_code"] is None
        assert succeeded["error_message"] is None
        assert not any(
            isinstance(error, dict) and error.get("code") == "execution_failed"
            for error in succeeded["errors"]
        )
        assert any(
            isinstance(warning, dict) and warning.get("code") == "websocket_outcome_overridden"
            for warning in succeeded["warnings"]
        )
        container = client.app.state.container
        with container.db.session_factory() as session:
            from app.models import Generation

            stored = session.get(Generation, accepted["id"])
            assert stored is not None
            assert stored.internal_diagnostics_json["comfyui_execution_error"] == {
                "node_id": "201",
                "node_type": "FakeImageOutput",
                "exception_type": "FakeWebSocketOnlyFailure",
            }


def test_prompt_rejection_retains_safe_terminal_error_and_internal_node_errors(
    settings_factory, fake_state
) -> None:
    fake_state.reject_prompts = True
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="prompt.rejection")
        accepted = create_generation(client, "rejected by queue validation", seed=503)

        failed = wait_for_status(client, accepted["id"], "failed_without_artifacts")
        assert failed["prompt_id"] is None
        assert failed["artifact_count"] == 0
        assert failed["error_code"] == "comfyui_prompt_rejected"
        assert failed["error_message"] == "ComfyUI rejected the compiled workflow request."
        assert failed["errors"] == [
            {
                "code": "comfyui_prompt_rejected",
                "message": "ComfyUI rejected the compiled workflow request.",
            }
        ]
        assert failed["raw_history"] == {}
        assert any(item["type"] == "generation.terminal" for item in failed["events"])
        assert "node_errors" not in str(failed)

        from app.models import Generation

        container = client.app.state.container
        with container.db.session_factory() as session:
            stored = session.get(Generation, accepted["id"])
            assert stored is not None
            assert stored.internal_diagnostics_json["queue_validation"] == {
                "status": 400,
                "response": {
                    "error": {"type": "prompt_outputs_failed_validation"},
                    "node_errors": {"20": {"errors": [{"message": "fake validation error"}]}},
                },
            }


def test_cancel_after_checkpoint_and_failure_keep_best_available_and_recall(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client)
        cancellable = create_generation(client, "slow cancellation sample", seed=101)
        checkpoint = wait_for_generation(
            client,
            cancellable["id"],
            lambda item: item["artifact_count"] >= 1 and item["status"] == "running",
        )
        assert checkpoint["canonical_artifact_id"] is None
        assert checkpoint["cancel_allowed"] is True
        cancel = client.post(
            f"/api/generations/{cancellable['id']}/cancel",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancel_requested"
        assert cancel.json()["cancel_allowed"] is False
        cancelled = wait_for_status(client, cancellable["id"], "cancelled_with_artifacts")
        assert cancelled["canonical_artifact_id"] is None
        assert cancelled["best_available_artifact_id"] is not None
        best = next(item for item in cancelled["artifacts"] if item["best_available"])
        assert best["state"] == "best_available"
        assert (
            client.get(f"/api/generations/{cancellable['id']}/recall").json()["available"] is True
        )

        failed_attempt = create_generation(client, "please fail after checkpoint", seed=202)
        failed = wait_for_status(client, failed_attempt["id"], "failed_with_artifacts")
        assert failed["canonical_artifact_id"] is None
        assert failed["best_available_artifact_id"] is not None
        assert failed["error_code"] == "execution_failed"
        assert (
            client.get(f"/api/generations/{failed_attempt['id']}/recall").json()["available"]
            is True
        )

        page = client.get("/api/generations").json()["items"]
        statuses = {item["id"]: item["status"] for item in page}
        assert statuses[cancellable["id"]] == "cancelled_with_artifacts"
        assert statuses[failed_attempt["id"]] == "failed_with_artifacts"


def test_gallery_summary_exposes_target_dimensions_and_queued_cancel_deletes_record(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        provision_user(client)
        payload = generation_payload(client, "portrait placeholder", seed=404)
        payload["parameters"]["width"] = 640
        payload["parameters"]["height"] = 960
        response = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=payload,
        )
        assert response.status_code == 201, response.text
        accepted = response.json()
        assert (accepted["expected_width"], accepted["expected_height"]) == (640, 960)
        assert accepted["cancel_allowed"] is True

        cancelled = client.post(
            f"/api/generations/{accepted['id']}/cancel",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert cancelled.status_code == 204, cancelled.text
        assert cancelled.content == b""
        assert client.get(f"/api/generations/{accepted['id']}").status_code == 404
        assert client.get("/api/generations").json()["items"] == []
        assert fake_state.submitted == []


def test_validation_rejection_creates_no_record_and_rapid_submissions_are_distinct(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        provision_user(client)
        invalid = generation_payload(client, "bad size", seed=1)
        invalid["parameters"]["width"] = 4096
        response = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=invalid,
        )
        assert response.status_code == 422
        assert client.get("/api/generations").json()["items"] == []

        records = [create_generation(client, f"queued {index}", seed=index) for index in range(5)]
        assert len({item["id"] for item in records}) == 5
        page = client.get("/api/generations?limit=60").json()["items"]
        assert len(page) == 5
        assert all(item["status"] == "queued" for item in page)


def test_generation_deletion_removes_application_files_only(settings_factory, fake_state) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client)
        composition = client.post(
            "/api/prompt-assistant/compose",
            headers={"X-CSRF-Token": csrf(client)},
            json={
                "mode": "refine",
                "prompt": "deletable image",
                "creative_direction": "soft studio light",
            },
        )
        assert composition.status_code == 200, composition.text
        payload = generation_payload(
            client,
            composition.json()["prompt"],
            seed=303,
        )
        payload["prompt_assistant_run_id"] = composition.json()["composition_id"]
        accepted = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=payload,
        )
        assert accepted.status_code == 201, accepted.text
        generation = accepted.json()
        wait_for_status(client, generation["id"], "succeeded")
        paths: list[Path] = []
        container = client.app.state.container
        with container.db.session_factory() as session:
            from app.models import Artifact, PromptAssistantRun
            from sqlalchemy import select

            artifacts = list(
                session.scalars(select(Artifact).where(Artifact.generation_id == generation["id"]))
            )
            for artifact in artifacts:
                paths.append(settings.data_dir / artifact.storage_path)
                if artifact.thumbnail_path:
                    paths.append(settings.data_dir / artifact.thumbnail_path)
            assert (
                session.scalar(
                    select(PromptAssistantRun).where(
                        PromptAssistantRun.generation_id == generation["id"]
                    )
                )
                is not None
            )
        assert paths and all(path.exists() for path in paths)
        submitted_before = len(fake_state.submitted)
        response = client.delete(
            f"/api/generations/{generation['id']}",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert response.status_code == 204
        assert client.get(f"/api/generations/{generation['id']}").status_code == 404
        assert all(not path.exists() for path in paths)
        assert len(fake_state.submitted) == submitted_before
        with container.db.session_factory() as session:
            from app.models import PromptAssistantRun
            from sqlalchemy import select

            assert (
                session.scalar(
                    select(PromptAssistantRun).where(
                        PromptAssistantRun.id == composition.json()["composition_id"]
                    )
                )
                is None
            )


def test_required_declared_durable_artifact_failure_is_not_reported_as_success(
    settings_factory, fake_state
) -> None:
    # Keep the final artifact pending long enough to switch retrieval into failure mode after
    # the provisional artifact checkpoint under full-suite scheduler contention.
    fake_state.slow_stage_delay = 2.0
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="archive.failure")
        generic = next(
            item
            for item in client.get("/api/workflows").json()
            if item["display_name"] == "Generic Landscape"
        )
        response = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json={
                "source_key": generic["source_key"],
                "revision": generic["revision"],
                "parameters": {"prompt": "slow archive unavailable", "iterations": 1},
            },
        )
        assert response.status_code == 201, response.text
        generation = response.json()
        checkpoint = wait_for_generation(
            client,
            generation["id"],
            lambda item: item["status"] == "running" and item["artifact_count"] >= 1,
        )
        assert checkpoint["canonical_artifact_id"] is None
        fake_state.fail_retrieval = True
        failed = wait_for_status(client, generation["id"], "failed_with_artifacts", timeout=10)
        assert failed["error_code"] == "artifact_persistence_failed"
        assert failed["canonical_artifact_id"] is None
        assert failed["best_available_artifact_id"] is not None
        failure_events = [
            item for item in failed["events"] if item["type"] == "artifact.persistence_failed"
        ]
        assert failure_events
        required_failures = [item for item in failure_events if item["payload"]["required"] is True]
        assert required_failures
        assert {item["payload"]["output_id"] for item in required_failures} == {"final_image"}


def test_unmapped_artifact_failure_preserves_reference_as_warning_and_allows_success(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="archive.optional")
        generation = create_generation(client, "slow optional archive unavailable", seed=911)
        wait_for_generation(
            client,
            generation["id"],
            lambda item: item["status"] == "running" and item["artifact_count"] >= 1,
        )
        fake_state.retrieval_failure_substrings.add("native-result")

        succeeded = wait_for_status(client, generation["id"], "succeeded", timeout=10)

        assert succeeded["error_code"] is None
        assert succeeded["artifact_count"] == 4
        assert set(succeeded["unmapped_outputs"]) == {"900", "901"}
        unresolved = succeeded["unmapped_outputs"]["901"]["images"][0]
        warning = next(
            item
            for item in succeeded["warnings"]
            if isinstance(item, dict) and item.get("code") == "optional_artifact_unavailable"
        )
        assert warning["output_id"] == "native:901"
        assert warning["reference"] == unresolved
        failure_events = [
            item for item in succeeded["events"] if item["type"] == "artifact.persistence_failed"
        ]
        assert failure_events
        assert all(item["payload"]["required"] is False for item in failure_events)


def test_unmapped_generic_file_is_archived_without_image_decoding(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="archive.generic")
        generation = create_generation(client, "generic locator archive", seed=912)

        succeeded = wait_for_status(client, generation["id"], "succeeded", timeout=10)

        generic_file = next(item for item in succeeded["artifacts"] if item["kind"] == "file")
        assert generic_file["output_id"] == "native:901"
        assert generic_file["thumbnail_url"] is None
        content = client.get(generic_file["content_url"])
        assert content.status_code == 200
        assert content.headers["content-type"] == "application/octet-stream"
        assert content.content == b'{"fixture":"generic-output"}'
        assert succeeded["best_available_artifact_id"] != generic_file["id"]


def test_transient_executed_event_retrieval_failure_recovers_from_terminal_history(
    settings_factory, fake_state
) -> None:
    fake_state.retrieval_failures_remaining = 1
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="archive.retry")
        generation = create_generation(client, "slow archive retry", seed=910)
        succeeded = wait_for_status(client, generation["id"], "succeeded", timeout=10)

        assert succeeded["error_code"] is None
        assert succeeded["artifact_count"] >= 2
        assert any(item["type"] == "artifact.persistence_failed" for item in succeeded["events"])
        assert not any(
            isinstance(item, dict) and item.get("code") == "artifact_persistence_failed"
            for item in succeeded["errors"]
        )


def test_deleting_queued_and_running_generations_reconciles_before_cleanup(
    settings_factory, fake_state
) -> None:
    import time

    queued_settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(queued_settings)) as client:
        provision_user(client, username="queued.delete")
        queued = create_generation(client, "delete before dispatch", seed=811)
        deleted = client.delete(
            f"/api/generations/{queued['id']}",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert deleted.status_code == 204
        assert client.get(f"/api/generations/{queued['id']}").status_code == 404
        assert fake_state.submitted == []

    running_settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(running_settings)) as client:
        provision_user(client, username="running.delete")
        running = create_generation(client, "slow delete while running", seed=812)
        wait_for_generation(
            client,
            running["id"],
            lambda item: item["status"] == "running" and item["artifact_count"] >= 1,
        )
        requested = client.delete(
            f"/api/generations/{running['id']}",
            headers={"X-CSRF-Token": csrf(client)},
        )
        # Terminal reconciliation may win the race between the running-state read and DELETE.
        # Active deletion is deferred (202); an already-terminal record is deleted immediately
        # (204). Both must converge on the same complete cleanup below.
        assert requested.status_code in {202, 204}
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if client.get(f"/api/generations/{running['id']}").status_code == 404:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                "running generation was not deleted after cancellation reconciliation"
            )
