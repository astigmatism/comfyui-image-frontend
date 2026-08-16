from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.publication import EDITABLE_WORKFLOW_DRIFT_WARNING
from app.main import create_app
from app.models import ServiceHealth
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.helpers import (
    first_profile,
    generation_payload,
    login_ready_admin,
    provision_user,
    restore_cookie,
)
from tests.publication_fixtures import (
    build_publication_bundle,
    generation_source_timeline_fixture,
)


def _cache_ollama_health(
    client: TestClient,
    *,
    available: bool,
    message: str | None = None,
    checked_at: datetime | None = None,
) -> None:
    container = client.app.state.container
    with container.db.session_factory() as session:
        health = session.get(ServiceHealth, "ollama")
        if health is None:
            health = ServiceHealth(service="ollama")
            session.add(health)
        health.available = available
        health.message = message
        health.checked_at = checked_at or datetime.now(UTC)
        session.commit()


def _contains_private_graph_key(value: Any) -> bool:
    if isinstance(value, dict):
        if {"selector", "bindings", "node_id", "instance_uuid"}.intersection(value):
            return True
        return any(_contains_private_graph_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_graph_key(item) for item in value)
    return False


def test_discovery_registers_only_valid_pair_and_public_contract_is_semantic(
    fake_state, app_client: TestClient
) -> None:
    provision_user(app_client)
    profile = first_profile(app_client)
    assert profile["display_name"] == "Krea 2 NSFW V4"
    assert profile["readiness"] == "ready"
    assert profile["available"] is True
    assert len(profile["source_key"]) == 64
    assert "workflows/" not in str(profile)
    assert profile["generation_source"]["schema_version"] == (
        "comfyui-image-frontend.generation-source/v1"
    )
    assert profile["generation_source"]["generation_type"] == "text_to_image"
    assert profile["generation_source"]["base_model"]["architecture"] == "krea2"
    assert "timeline" not in profile["generation_source"]["base_model"]
    assert profile["model_selectors"] == []
    counts = profile["technical_inventory"]["node_counts"]
    assert set(counts) == {
        "editable_root",
        "subgraph_definitions",
        "editable_subgraph_nodes",
        "compiled_api",
        "output_reachable",
        "compiled_orphans",
    }
    assert counts["output_reachable"] + counts["compiled_orphans"] == counts["compiled_api"]
    public_lora = next(
        item for item in profile["technical_inventory"]["loras"] if item["usage"] == "public_choice"
    )
    assert public_lora["parameter_id"] == "lora"
    assert "artifact" not in public_lora
    older_source = next(
        item
        for item in app_client.get("/api/workflows").json()
        if item["display_name"] == "Generic Landscape"
    )
    assert older_source["generation_source"] is None
    assert older_source["technical_inventory"] is None
    assert older_source["model_selectors"] == []

    detail = app_client.get(f"/api/workflows/{profile['source_key']}")
    assert detail.status_code == 200
    assert detail.json()["generation_source"] == profile["generation_source"]
    assert detail.json()["technical_inventory"] == profile["technical_inventory"]
    interface = detail.json()["interface"]
    assert _contains_private_graph_key(interface) is False
    assert [parameter["id"] for parameter in interface["inputs"]] == [
        "prompt",
        "width",
        "height",
        "seed",
        "enable_seedvr2_upscale",
        "lora",
        "lora_strength",
    ]
    assert interface["inputs"][3]["maximum"] == "1125899906842624"
    assert interface["inputs"][3]["default"] is None
    choice = interface["inputs"][5]
    assert choice == {
        "id": "lora",
        "type": "choice",
        "label": "LoRA",
        "description": "Selects the LoRA applied by the primary model-only LoRA loader.",
        "semantic_role": "lora",
        "required": False,
        "advanced": True,
        "group": "Advanced",
        "order": 55,
        "default": "knp_v4_1",
        "choices": [
            {"value": "knp_v4_1", "label": "KNP v4.1", "default_strength": 1.0},
            {"value": "knp_v3_1", "label": "KNP v3.1", "default_strength": 0.5},
            {"value": "knp_v2", "label": "KNP v2", "default_strength": 1.0},
            {
                "value": "mysticxxx_krea2_v1",
                "label": "MysticXXX Krea2 v1",
                "default_strength": 1.0,
            },
        ],
    }
    assert "safetensors" not in str(interface)
    assert "options_json" not in str(interface)
    assert [output["id"] for output in interface["outputs"]] == [
        "base",
        "second_pass",
        "final",
    ]
    assert [output["role"] for output in interface["outputs"]] == [
        "preview",
        "comparison",
        "final",
    ]
    assert all(output["kind"] == "image" for output in interface["outputs"])
    assert all(output["cardinality"] == "many" for output in interface["outputs"])

    # Ordinary users cannot inspect workflow registration diagnostics.
    assert app_client.get("/api/admin/workflows/diagnostics").status_code == 403

    # Read diagnostics directly only to verify startup discovery results; the API
    # authorization boundary above is the product behavior under test.
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        diagnostics = container.registry.diagnostics(session)
    assert {(item.basename, item.accepted, item.code) for item in diagnostics} == {
        ("Generic Landscape", True, "ready"),
        ("Krea 2 NSFW V4", True, "ready"),
    }
    assert fake_state.comfy_user_headers
    assert all(header == "fixture-user" for _, header in fake_state.comfy_user_headers)
    assert fake_state.userdata_raw_paths
    assert all(
        b"%2F" in path and b"/workflows/" not in path for path in fake_state.userdata_raw_paths
    )


def test_source_summary_and_detail_project_authoritative_checkpoint_selector(
    fake_state, settings_factory
) -> None:
    bundle = build_publication_bundle("moody")
    fake_state.workflow_files = dict(bundle.files)

    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="checkpoint.selector")
        summaries = client.get("/api/workflows")
        assert summaries.status_code == 200
        summary = summaries.json()[0]
        assert summary["display_name"] == "Moody Krea 2 Mix V4"
        assert len(summary["model_selectors"]) == 1
        selector = summary["model_selectors"][0]

        assert selector == {
            "parameter_id": "checkpoint",
            "label": "Checkpoint",
            "description": (
                "Selects the Moody Krea 2 diffusion checkpoint. V4 INT8 remains the default; "
                "V5 BF16 is the highest-precision V5 option and is substantially heavier on "
                "VRAM/RAM."
            ),
            "default": "v4_int8",
            "choices": [
                {
                    "value": "v4_int8",
                    "label": "Moody Krea 2 V4 INT8 ConvRot",
                    "released_month": "2026-07",
                },
                {
                    "value": "tyjr_mxfp8",
                    "label": "Moody Krea 2 TYJR MXFP8",
                    "released_month": "2026-07",
                },
                {
                    "value": "v4_bf16",
                    "label": "Moody Krea 2 V4 BF16",
                    "released_month": "2026-07",
                },
                {
                    "value": "cutie_x_int8",
                    "label": "Moody Krea 2 Cutie X INT8",
                    "released_month": "2026-07",
                },
                {
                    "value": "v5_bf16",
                    "label": "Moody Krea 2 V5 BF16",
                    "released_month": None,
                },
            ],
        }
        assert "safetensors" not in str(selector)
        assert "options_json" not in str(selector)

        detail_response = client.get(f"/api/workflows/{summary['source_key']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["model_selectors"] == summary["model_selectors"]
        interface_checkpoint = next(
            item for item in detail["interface"]["inputs"] if item["id"] == "checkpoint"
        )
        assert interface_checkpoint == {
            "id": "checkpoint",
            "type": "choice",
            "label": "Checkpoint",
            "description": (
                "Selects the Moody Krea 2 diffusion checkpoint. V4 INT8 remains the default; "
                "V5 BF16 is the highest-precision V5 option and is substantially heavier on "
                "VRAM/RAM."
            ),
            "semantic_role": "model",
            "required": False,
            "advanced": True,
            "group": "Advanced",
            "order": 60,
            "default": "v4_int8",
            "choices": [
                {"value": "v4_int8", "label": "Moody Krea 2 V4 INT8 ConvRot"},
                {"value": "tyjr_mxfp8", "label": "Moody Krea 2 TYJR MXFP8"},
                {"value": "v4_bf16", "label": "Moody Krea 2 V4 BF16"},
                {"value": "cutie_x_int8", "label": "Moody Krea 2 Cutie X INT8"},
                {"value": "v5_bf16", "label": "Moody Krea 2 V5 BF16"},
            ],
        }
        interface_guidance = next(
            item for item in detail["interface"]["inputs"] if item["id"] == "guidance_strength"
        )
        assert interface_guidance == {
            "id": "guidance_strength",
            "type": "number",
            "label": "Guidance strength",
            "description": "Controls the guidance strength used by the Moody Krea 2 workflow.",
            "semantic_role": "guidance",
            "required": False,
            "advanced": True,
            "group": "Advanced",
            "order": 70,
            "default": 1.0,
            "minimum": 0.0,
            "maximum": 2.0,
            "step": 0.05,
        }


def test_dependency_unavailable_catalog_keeps_safe_checkpoint_selector(
    fake_state, settings_factory
) -> None:
    bundle = build_publication_bundle("moody")
    fake_state.workflow_files = dict(bundle.files)
    fake_state.object_info.pop("CIFChoiceParameter")

    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="checkpoint.unavailable")
        response = client.get("/api/workflows")
        assert response.status_code == 200
        source = response.json()[0]

        assert source["available"] is False
        assert source["readiness"] == "dependency_missing"
        assert source["model_selectors"][0]["parameter_id"] == "checkpoint"
        assert source["model_selectors"][0]["choices"][1] == {
            "value": "tyjr_mxfp8",
            "label": "Moody Krea 2 TYJR MXFP8",
            "released_month": "2026-07",
        }
        assert "safetensors" not in str(source["model_selectors"])
        assert client.get(f"/api/workflows/{source['source_key']}").status_code == 409


def test_discovery_uses_recursive_fallback_with_headers_and_encoded_artifacts(
    fake_state, settings_factory
) -> None:
    fake_state.listing_mode = "fallback"
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="fallback.discovery")
        sources = client.get("/api/workflows").json()
        assert {item["display_name"] for item in sources} == {
            "Generic Landscape",
            "Krea 2 NSFW V4",
        }
        assert any(path == "/userdata" for path, _ in fake_state.comfy_user_headers)
        assert fake_state.userdata_raw_paths
        assert all(b"%2F" in path for path in fake_state.userdata_raw_paths)


def test_source_api_preserves_unknown_additive_metadata(fake_state, settings_factory) -> None:
    def add_future_metadata(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["generation_source"]["generation_type"] = "spatial_remix"
        manifest["generation_source"]["future_profile"] = {"rank": 7}
        manifest["technical_inventory"]["warnings"].append("future_inventory_warning")
        manifest["technical_inventory"]["unclassified_loaders"].append(
            {"class_type": "FutureModelProvider", "future_hint": True}
        )

    bundle = build_publication_bundle("krea", mutate_manifest=add_future_metadata)
    fake_state.workflow_files = dict(bundle.files)

    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="future.metadata")
        source = first_profile(client)
        assert source["generation_source"]["generation_type"] == "spatial_remix"
        assert source["generation_source"]["future_profile"] == {"rank": 7}
        assert source["technical_inventory"]["warnings"] == ["future_inventory_warning"]
        assert source["technical_inventory"]["unclassified_loaders"][-1] == {
            "class_type": "FutureModelProvider",
            "future_hint": True,
        }


def test_source_list_and_detail_preserve_model_timeline_without_private_bindings(
    fake_state, settings_factory
) -> None:
    timeline = generation_source_timeline_fixture()
    settings = settings_factory()
    provenance_path = "/provenance-must-remain-inert"
    timeline["architecture"]["source"]["url"] = f"{settings.comfyui_base_url}{provenance_path}"

    def add_timeline(manifest) -> None:  # type: ignore[no-untyped-def]
        manifest["generation_source"]["base_model"]["timeline"] = timeline

    bundle = build_publication_bundle("krea", mutate_manifest=add_timeline)
    fake_state.workflow_files = dict(bundle.files)

    with TestClient(create_app(settings)) as client:
        provision_user(client, username="timeline.metadata")
        summary = first_profile(client)
        detail_response = client.get(f"/api/workflows/{summary['source_key']}")

        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert summary["readiness"] == "ready"
        assert summary["generation_source"]["base_model"]["timeline"] == timeline
        assert detail["generation_source"]["base_model"]["timeline"] == timeline
        assert detail["generation_source"] == summary["generation_source"]
        assert timeline["architecture"]["introduced_month"] == "2026-01"
        assert timeline["default_model"]["released_month"] == "2026-06"
        assert timeline["future_timeline_field"] == {"rank": 7}
        assert _contains_private_graph_key(detail["interface"]) is False
        assert provenance_path not in fake_state.http_request_paths


def test_editable_workflow_drift_keeps_both_sources_visible_through_refresh(
    fake_state, settings_factory
) -> None:
    editable_paths = [
        path
        for path in fake_state.workflow_files
        if path.endswith(".json") and not path.endswith((".api.json", ".interface.json"))
    ]
    assert len(editable_paths) == 2
    for path in editable_paths:
        # Valid JSON with different raw bytes models an ordinary post-publication save.
        fake_state.workflow_files[path] += b"\n"

    with TestClient(create_app(settings_factory())) as client:
        _, user_cookie = provision_user(client, username="editable.drift")
        startup_sources = client.get("/api/workflows")
        assert startup_sources.status_code == 200, startup_sources.text
        sources = startup_sources.json()
        assert {item["display_name"] for item in sources} == {
            "Generic Landscape",
            "Krea 2 NSFW V4",
        }
        assert all(item["available"] is True for item in sources)
        assert all(item["readiness"] == "ready_with_warnings" for item in sources)
        assert all(item["warnings"] == [EDITABLE_WORKFLOW_DRIFT_WARNING] for item in sources)
        source_keys = {item["source_key"] for item in sources}

        login_ready_admin(client)
        refreshed = client.post(
            "/api/admin/workflows/refresh",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert refreshed.status_code == 200, refreshed.text
        assert {
            (item["basename"], item["accepted"], item["code"]) for item in refreshed.json()
        } == {
            ("Generic Landscape", True, "ready_with_warnings"),
            ("Krea 2 NSFW V4", True, "ready_with_warnings"),
        }
        assert all(
            item["details"]["editable_workflow_drifted"] is True
            and item["details"]["observed_workflow_sha256"] != item["details"]["workflow_sha256"]
            for item in refreshed.json()
        )

        restore_cookie(client, user_cookie)
        after_refresh = client.get("/api/workflows")
        assert after_refresh.status_code == 200, after_refresh.text
        refreshed_sources = after_refresh.json()
        assert {item["source_key"] for item in refreshed_sources} == source_keys
        assert all(item["available"] is True for item in refreshed_sources)
        assert all(item["readiness"] == "ready_with_warnings" for item in refreshed_sources)
        assert all(
            item["warnings"] == [EDITABLE_WORKFLOW_DRIFT_WARNING] for item in refreshed_sources
        )


def test_choice_defaults_strength_precedence_and_invalid_public_values_fail_before_prompt(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="choice.validation")

    def validate(parameters: dict[str, Any]) -> Any:
        payload = generation_payload(app_client, "choice validation", seed=73)
        payload["parameters"].update(parameters)
        return app_client.post(
            "/api/generations/validate",
            headers={"X-CSRF-Token": csrf(app_client)},
            json=payload,
        )

    cases = [
        ({}, "knp_v4_1", 1.0),
        ({"lora": None}, "knp_v4_1", 1.0),
        ({"lora": "knp_v3_1"}, "knp_v3_1", 0.5),
        ({"lora": "knp_v3_1", "lora_strength": 0.7}, "knp_v3_1", 0.7),
        ({"lora_strength": 0.8}, "knp_v4_1", 0.8),
    ]
    for parameters, expected_choice, expected_strength in cases:
        response = validate(parameters)
        assert response.status_code == 200, response.text
        effective = response.json()["effective_parameters"]
        assert effective["lora"] == expected_choice
        assert effective["lora_strength"] == expected_strength

    for invalid in (
        "",
        "KNP v3.1",
        "Krea2/KNPV4.1_pre.safetensors",
        "not_published",
    ):
        response = validate({"lora": invalid})
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"]["code"] == "parameter_validation_failed"
        assert set(body["error"]["fields"]) == {"lora"}
        assert "safetensors" not in body["error"]["fields"]["lora"]

    assert fake_state.submitted == []


def test_successful_empty_listing_retires_current_catalog(fake_state, settings_factory) -> None:
    with TestClient(create_app(settings_factory())) as client:
        _, user_cookie = provision_user(client, username="empty.catalog")
        assert len(client.get("/api/workflows").json()) == 2
        fake_state.workflow_files = {}
        login_ready_admin(client)
        refreshed = client.post(
            "/api/admin/workflows/refresh",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert refreshed.status_code == 200
        assert refreshed.json() == []
        restore_cookie(client, user_cookie)
        assert client.get("/api/workflows").json() == []


def test_prompt_assistant_uses_router_selected_model_and_records_effective_model(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client)
    _cache_ollama_health(app_client, available=True)
    assert app_client.get("/api/prompt-assistant/status").json()["available"] is True
    fake_state.ollama_effective_model = "router-active:latest"
    before = app_client.get("/api/generations").json()["items"]
    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "refine",
            "prompt": "portrait",
            "creative_direction": "soft window light",
        },
    )
    assert response.status_code == 200, response.text
    composed = response.json()
    assert composed["prompt"] == "portrait, soft window light"
    assert composed["model"] == "router-active:latest"
    request_payload = fake_state.ollama_calls[-1]
    assert "model" not in request_payload
    assert request_payload["stream"] is False
    assert request_payload["think"] is True
    assert request_payload["format"] == {
        "type": "object",
        "properties": {"prompt": {"type": "string"}},
        "required": ["prompt"],
        "additionalProperties": False,
    }
    assert request_payload["options"] == {"temperature": 0.1, "seed": 0, "num_predict": 512}
    assert request_payload["prompt"] == (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. "
        "Refine the current prompt according to the creative direction.\n\n"
        "Current prompt:\nportrait\n\nCreative direction:\nsoft window light"
    )
    assert composed["template_version"] == "v5"
    assert app_client.get("/api/generations").json()["items"] == before

    payload = generation_payload(app_client, composed["prompt"], seed=123)
    payload["prompt_assistant_run_id"] = composed["composition_id"]
    accepted = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=payload,
    )
    assert accepted.status_code == 201, accepted.text
    generation_id = accepted.json()["id"]
    # Generate submits the visible prompt; it does not invoke Ollama again.
    assert len(fake_state.ollama_calls) == 1
    container = app_client.app.state.container
    from app.models import PromptAssistantRun
    from sqlalchemy import select

    with container.db.session_factory() as session:
        run = session.scalar(
            select(PromptAssistantRun).where(PromptAssistantRun.id == composed["composition_id"])
        )
        assert run is not None
        assert run.generation_id == generation_id
        assert run.prompt_before == "portrait"
        assert run.creative_direction == "soft window light"
        assert run.model_name == "router-active:latest"
        assert run.ollama_output == composed["prompt"]
    recalled = app_client.get(f"/api/generations/{generation_id}/recall").json()
    assert recalled["available"] is True
    assert recalled["parameters"]["prompt"] == composed["prompt"]
    assert len(fake_state.ollama_calls) == 1


def test_create_prompt_assistant_requests_a_complete_creative_krea_2_prompt(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="creative.prompt")
    _cache_ollama_health(app_client, available=True)
    app_client.app.state.container.ollama.seed_resolver = lambda minimum, maximum: 700

    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "create",
            "prompt": "old prompt that create mode must ignore",
            "creative_direction": "a red fox",
        },
    )

    assert response.status_code == 200, response.text
    request_payload = fake_state.ollama_calls[-1]
    instruction = request_payload["prompt"]
    assert instruction == (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. "
        "Create one complete, polished, directly usable image prompt from this creative "
        "direction:\n\na red fox"
    )
    assert request_payload["think"] is True
    assert request_payload["options"] == {"temperature": 0.5, "seed": 700, "num_predict": 512}
    assert response.json()["template_version"] == "v5"


def test_create_prompt_assistant_retries_an_unchanged_current_prompt(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="unchanged.prompt")
    _cache_ollama_health(app_client, available=True)
    app_client.app.state.container.ollama.seed_resolver = lambda minimum, maximum: 800
    fake_state.ollama_response_prompts = [
        "  OLD   PROMPT that create mode must replace  ",
        "a red fox stalking through snowy pines, low viewpoint, pale winter sunrise",
    ]

    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "create",
            "prompt": "old prompt that create mode must replace",
            "creative_direction": "a red fox",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["prompt"] == (
        "a red fox stalking through snowy pines, low viewpoint, pale winter sunrise"
    )
    assert response.json()["template_version"] == "v5"
    assert len(fake_state.ollama_calls) == 2
    first_request, retry_request = fake_state.ollama_calls
    assert first_request["options"] == {"temperature": 0.5, "seed": 800, "num_predict": 512}
    assert retry_request["options"] == {
        "temperature": 0.7,
        "seed": 801,
        "num_predict": 512,
    }
    assert retry_request["prompt"] == first_request["prompt"]
    assert retry_request["prompt"].endswith("from this creative direction:\n\na red fox")


def test_create_prompt_assistant_accepts_a_useful_paraphrase_without_retrying(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="preserved.direction")
    _cache_ollama_health(app_client, available=True)
    app_client.app.state.container.ollama.seed_resolver = lambda minimum, maximum: 850
    fake_state.ollama_response_prompts = [
        "A vibrant red fox stands beneath pines washed in moonlight.",
    ]

    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "create",
            "prompt": "",
            "creative_direction": "a red fox beneath moonlit pines",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["prompt"] == (
        "A vibrant red fox stands beneath pines washed in moonlight."
    )
    assert len(fake_state.ollama_calls) == 1
    assert fake_state.ollama_calls[0]["think"] is True


def test_create_prompt_assistant_never_accepts_a_recent_two_prompt_cycle(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="distinct.sequence")
    _cache_ollama_health(app_client, available=True)
    base_seeds = iter([100, 200, 300, 400])
    app_client.app.state.container.ollama.seed_resolver = lambda minimum, maximum: next(base_seeds)
    prompts = {
        "a": "a red fox beneath moonlit pines, standing among moss-covered roots",
        "b": "a red fox beneath moonlit pines, leaping across a snowy ravine",
        "c": "a red fox beneath moonlit pines, drinking beside a silver woodland pool",
        "d": "a red fox beneath moonlit pines, resting under wind-bent mountain branches",
    }
    fake_state.ollama_response_prompts = [
        prompts["a"],
        prompts["b"],
        prompts["a"],
        prompts["c"],
        prompts["b"],
        prompts["d"],
    ]

    current = "an unrelated starting prompt"
    composed_prompts = []
    for _ in range(4):
        response = app_client.post(
            "/api/prompt-assistant/compose",
            headers={"X-CSRF-Token": csrf(app_client)},
            json={
                "mode": "create",
                "prompt": current,
                "creative_direction": "a red fox beneath moonlit pines",
            },
        )
        assert response.status_code == 200, response.text
        current = response.json()["prompt"]
        composed_prompts.append(current)

    assert composed_prompts == [prompts["a"], prompts["b"], prompts["c"], prompts["d"]]
    assert [call["options"]["seed"] for call in fake_state.ollama_calls] == [
        100,
        200,
        300,
        301,
        400,
        401,
    ]
    expected_instruction = (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. "
        "Create one complete, polished, directly usable image prompt from this creative "
        "direction:\n\na red fox beneath moonlit pines"
    )
    assert {call["prompt"] for call in fake_state.ollama_calls} == {expected_instruction}


def test_prompt_assistant_accepts_structured_final_prompt_from_thinking_field(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="thinking.prompt")
    _cache_ollama_health(app_client, available=True)
    fake_state.ollama_response_in_thinking = True

    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "create",
            "prompt": "",
            "creative_direction": "a red fox",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["prompt"] == "a red fox"
    assert fake_state.ollama_calls[-1]["think"] is True


def test_prompt_assistant_rejects_a_response_without_thinking_output(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="missing.thinking")
    _cache_ollama_health(app_client, available=True)
    fake_state.ollama_include_thinking = False

    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "refine",
            "prompt": "a portrait in cool light",
            "creative_direction": "make the light warmer",
        },
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "ollama_invalid_response"
    assert error["message"] == "Prompt Assistant did not return thinking output."
    assert error["fields"] == {}
    assert error["details"] == {}
    assert fake_state.ollama_calls[-1]["think"] is True


def test_prompt_assistant_can_disable_thinking(app_client: TestClient, fake_state) -> None:
    provision_user(app_client, username="thinking.disabled")
    _cache_ollama_health(app_client, available=True)

    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "refine",
            "prompt": "a portrait in cool light",
            "creative_direction": "make the light warmer",
            "think": False,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["prompt"] == "a portrait in cool light, make the light warmer"
    assert fake_state.ollama_calls[-1]["think"] is False
    container = app_client.app.state.container
    from app.models import PromptAssistantRun

    with container.db.session_factory() as session:
        run = session.get(PromptAssistantRun, response.json()["composition_id"])
        assert run is not None
        assert run.thinking_enabled is False


def test_prompt_assistant_retries_a_transient_generate_failure(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="transient.ollama")
    fake_state.ollama_generate_failure_status = 503
    fake_state.ollama_generate_failures_remaining = 2
    retry_delays: list[float] = []

    async def record_retry_delay(delay: float) -> None:
        retry_delays.append(delay)

    app_client.app.state.container.ollama.retry_sleeper = record_retry_delay
    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "refine",
            "prompt": "a portrait",
            "creative_direction": "warm window light",
            "think": False,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["prompt"] == "a portrait, warm window light"
    assert len(fake_state.ollama_calls) == 3
    assert retry_delays == [0.25, 0.5]


def test_prompt_assistant_records_a_rejected_generate_request_precisely(
    app_client: TestClient, fake_state, caplog
) -> None:
    provision_user(app_client, username="rejected.ollama")
    fake_state.ollama_generate_failure_status = 400
    fake_state.ollama_generate_failures_remaining = 1

    with caplog.at_level(logging.WARNING, logger="app.services.ollama"):
        response = app_client.post(
            "/api/prompt-assistant/compose",
            headers={"X-CSRF-Token": csrf(app_client)},
            json={
                "mode": "refine",
                "prompt": "a portrait",
                "creative_direction": "warm window light",
                "think": False,
            },
        )

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "ollama_generate_rejected"
    assert error["details"] == {
        "operation": "generate",
        "failure_kind": "http_status",
        "attempts": 1,
        "thinking_enabled": False,
        "upstream_status": 400,
    }
    assert len(fake_state.ollama_calls) == 1

    container = app_client.app.state.container
    from app.models import PromptAssistantRun
    from sqlalchemy import select

    with container.db.session_factory() as session:
        run = session.scalar(
            select(PromptAssistantRun).where(
                PromptAssistantRun.error_code == "ollama_generate_rejected"
            )
        )
        assert run is not None
        assert run.thinking_enabled is False
        assert run.error_code == "ollama_generate_rejected"
        assert run.raw_response_json == {"error_details": error["details"]}

    from app.main import JsonFormatter

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "ollama_generate_attempt_failed"
    )
    failure = json.loads(JsonFormatter().format(record))
    assert failure == {
        **{key: failure[key] for key in ("timestamp", "level", "logger")},
        "message": "ollama_generate_attempt_failed",
        "service": "ollama",
        "operation": "generate",
        "assistant_mode": "refine",
        "thinking_enabled": False,
        "attempt": 1,
        "max_attempts": 3,
        "failure_kind": "http_status",
        "retryable": False,
        "upstream_status": 400,
        "exception_class": "HTTPStatusError",
    }
    assert "a portrait" not in json.dumps(failure)


def test_prompt_assistant_reports_exhausted_transient_generate_failures(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="unavailable.ollama")
    fake_state.ollama_generate_failure_status = 503
    fake_state.ollama_generate_failures_remaining = 3

    async def skip_retry_delay(_: float) -> None:
        return None

    app_client.app.state.container.ollama.retry_sleeper = skip_retry_delay
    response = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "mode": "refine",
            "prompt": "a portrait",
            "creative_direction": "warm window light",
        },
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "ollama_generate_unavailable"
    assert error["details"] == {
        "operation": "generate",
        "failure_kind": "http_status",
        "attempts": 3,
        "thinking_enabled": True,
        "upstream_status": 503,
    }
    assert len(fake_state.ollama_calls) == 3


def test_ollama_outage_only_disables_assistant(app_client: TestClient, fake_state) -> None:
    provision_user(app_client)
    fake_state.ollama_available = False
    _cache_ollama_health(
        app_client,
        available=False,
        message="Prompt Assistant could not reach the Ollama router.",
    )
    status = app_client.get("/api/prompt-assistant/status")
    assert status.status_code == 200
    assert status.json()["available"] is False
    compose = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"mode": "create", "prompt": "", "creative_direction": "a moonlit lake"},
    )
    assert compose.status_code == 503

    validation = app_client.post(
        "/api/generations/validate",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=generation_payload(app_client, "manual prompt", seed=44),
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True


def test_empty_router_model_listing_only_disables_assistant(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="empty.router")
    fake_state.models = []
    _cache_ollama_health(
        app_client,
        available=False,
        message="Prompt Assistant is unavailable because the Ollama router has no reachable model.",
    )

    status = app_client.get("/api/prompt-assistant/status")
    assert status.status_code == 200
    assert status.json()["available"] is False
    assert "router has no reachable model" in status.json()["message"]
    compose = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"mode": "create", "prompt": "", "creative_direction": "a moonlit lake"},
    )
    assert compose.status_code == 503
    assert fake_state.ollama_calls == []

    validation = app_client.post(
        "/api/generations/validate",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=generation_payload(app_client, "manual prompt", seed=45),
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True


def test_prompt_assistant_status_uses_bounded_cached_health_without_contacting_ollama(
    app_client: TestClient, monkeypatch
) -> None:
    provision_user(app_client, username="cached.assistant.status")
    _cache_ollama_health(app_client, available=True)

    async def unexpected_live_probe() -> list[str]:
        raise AssertionError("status endpoint contacted Ollama")

    monkeypatch.setattr(
        app_client.app.state.container.ollama, "available_models", unexpected_live_probe
    )
    response = app_client.get("/api/prompt-assistant/status")
    assert response.status_code == 200
    assert response.json() == {"available": True, "message": None}

    _cache_ollama_health(
        app_client,
        available=True,
        checked_at=datetime.now(UTC) - timedelta(seconds=31),
    )
    stale = app_client.get("/api/prompt-assistant/status")
    assert stale.status_code == 200
    assert stale.json()["available"] is False
    assert "stale" in stale.json()["message"]


def test_cached_prompt_assistant_success_does_not_mask_runtime_compose_failure(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="assistant.runtime.failure")
    _cache_ollama_health(app_client, available=True)
    assert app_client.get("/api/prompt-assistant/status").json()["available"] is True

    fake_state.ollama_available = False
    compose = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"mode": "create", "prompt": "", "creative_direction": "storm over a city"},
    )
    assert compose.status_code == 503
    assert compose.json()["error"]["code"] == "ollama_unavailable"


def test_private_bindings_unpublished_parameters_and_stale_revisions_are_rejected(
    app_client: TestClient,
) -> None:
    provision_user(app_client, username="operator.guard")
    profile = first_profile(app_client)
    response = app_client.get(f"/api/workflows/{profile['source_key']}")
    assert response.status_code == 200, response.text
    assert _contains_private_graph_key(response.json()["interface"]) is False

    payload = generation_payload(app_client, "cannot elevate through semantic controls", seed=5)
    payload["parameters"]["operator_internal_switch"] = True
    rejected = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=payload,
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["fields"] == {
        "operator_internal_switch": "Unknown published parameter."
    }

    stale = generation_payload(app_client, "revision must remain exact", seed=6)
    stale["revision"]["api_sha256"] = "0" * 64
    rejected_revision = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=stale,
    )
    assert rejected_revision.status_code == 409
    assert rejected_revision.json()["error"]["code"] == "source_republished"


def test_dependency_loss_disables_an_accepted_source_until_refresh_recovers(
    app_client: TestClient, fake_state
) -> None:
    _, user_cookie = provision_user(app_client, username="dependency.guard")
    generic = next(
        item
        for item in app_client.get("/api/workflows").json()
        if item["display_name"] == "Generic Landscape"
    )

    fake_state.object_info.pop("CIFPublishImage")
    login_ready_admin(app_client)
    refreshed = app_client.post(
        "/api/admin/workflows/refresh",
        headers={"X-CSRF-Token": csrf(app_client)},
    )
    assert refreshed.status_code == 200, refreshed.text
    dependency_diagnostic = next(
        item for item in refreshed.json() if item["code"] == "dependency_missing"
    )
    assert dependency_diagnostic["details"]["missing_class_types"] == ["CIFPublishImage"]

    restore_cookie(app_client, user_cookie)
    unavailable = next(
        item
        for item in app_client.get("/api/workflows").json()
        if item["source_key"] == generic["source_key"]
    )
    assert unavailable["available"] is False
    assert unavailable["readiness"] == "dependency_missing"
    assert unavailable["message"] == (
        "Required ComfyUI node classes are unavailable for this source."
    )
    assert "CIFPublishImage" not in str(unavailable)
    rejected = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "source_key": generic["source_key"],
            "revision": generic["revision"],
            "parameters": {"prompt": "must not queue", "iterations": 1},
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "source_dependency_missing"

    fake_state.object_info["CIFPublishImage"] = {"input": {"required": {"images": ["IMAGE"]}}}
    login_ready_admin(app_client)
    recovered = app_client.post(
        "/api/admin/workflows/refresh",
        headers={"X-CSRF-Token": csrf(app_client)},
    )
    assert recovered.status_code == 200, recovered.text
    restore_cookie(app_client, user_cookie)
    ready = next(
        item
        for item in app_client.get("/api/workflows").json()
        if item["source_key"] == generic["source_key"]
    )
    assert ready["available"] is True
    assert ready["readiness"] == "ready"


def test_republication_changes_only_new_generation_snapshots(
    app_client: TestClient, fake_state
) -> None:
    _, user_cookie = provision_user(app_client, username="revision.guard")
    first_payload = generation_payload(app_client, "old immutable revision", seed=31)
    first = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=first_payload,
    )
    assert first.status_code == 201, first.text

    def mark_new_revision(manifest, workflow, api) -> None:  # type: ignore[no-untyped-def]
        del manifest
        workflow["revision_marker"] = "new-publication"
        api["20"]["inputs"]["revision_marker"] = "new-publication"

    republished = build_publication_bundle(
        "krea",
        publication_id="55555555-5555-4555-8555-555555555555",
        mutate_artifacts=mark_new_revision,
    )
    fake_state.workflow_files = dict(republished.files)
    login_ready_admin(app_client)
    refreshed = app_client.post(
        "/api/admin/workflows/refresh",
        headers={"X-CSRF-Token": csrf(app_client)},
    )
    assert refreshed.status_code == 200, refreshed.text

    restore_cookie(app_client, user_cookie)
    second_payload = generation_payload(app_client, "new immutable revision", seed=32)
    second = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=second_payload,
    )
    assert second.status_code == 201, second.text

    from app.models import Generation, WorkflowProfile

    container = app_client.app.state.container
    with container.db.session_factory() as session:
        old_generation = session.get(Generation, first.json()["id"])
        new_generation = session.get(Generation, second.json()["id"])
        assert old_generation is not None and new_generation is not None
        assert old_generation.workflow_profile_id != new_generation.workflow_profile_id
        assert (
            old_generation.generation_source_json["publication_id"]
            != (new_generation.generation_source_json["publication_id"])
        )
        assert "revision_marker" not in old_generation.compiled_graph_json["20"]["inputs"]
        assert new_generation.compiled_graph_json["20"]["inputs"]["revision_marker"] == (
            "new-publication"
        )
        old_profile = session.get(WorkflowProfile, old_generation.workflow_profile_id)
        new_profile = session.get(WorkflowProfile, new_generation.workflow_profile_id)
        assert old_profile is not None and new_profile is not None
        assert "revision_marker" not in old_profile.source_ui_json
        assert new_profile.source_ui_json["revision_marker"] == "new-publication"
