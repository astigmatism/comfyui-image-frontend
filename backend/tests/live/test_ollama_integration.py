from __future__ import annotations

import base64
import io
import json
import os

import pytest
from app.config import Settings
from app.main import create_app
from app.models import PromptAssistantRun
from app.services.llm_router import RouterWatch, model_for, pick_service, thinking_value
from app.services.ollama import ComposeResult, OllamaAdapter
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from tests.conftest import csrf
from tests.helpers import generation_payload, provision_user

_BASE_URL = os.getenv("CIF_OLLAMA_BASE_URL")
_RUN_LIVE = os.getenv("CIF_RUN_LIVE_OLLAMA_TESTS") == "1"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (_RUN_LIVE and _BASE_URL),
        reason="set CIF_RUN_LIVE_OLLAMA_TESTS=1 and CIF_OLLAMA_BASE_URL to run",
    ),
]


def _adapter() -> OllamaAdapter:
    return OllamaAdapter(Settings(test_mode=True, ollama_base_url=_BASE_URL))


def _assert_structured_output_selected(result: ComposeResult) -> None:
    responses = result.raw_response.get("attempts", [result.raw_response])
    assert isinstance(responses, list)
    assert responses
    for response in responses:
        assert isinstance(response, dict)
        assert response.get("selected_field") in {"response", "thinking"}
        assert response.get("validation_stage") == "complete"


@pytest.mark.parametrize("think", [False, True])
async def test_live_create_returns_a_new_prompt_from_the_direction(think: bool) -> None:
    adapter = _adapter()
    try:
        result = await adapter.compose(
            mode="create",
            prompt="A plain studio portrait of a ceramic vase.",
            direction="an astronaut tending a greenhouse on Mars",
            think=think,
        )
    finally:
        await adapter.close()

    assert "astronaut" in result.prompt.casefold()
    assert "greenhouse" in result.prompt.casefold()
    assert "mars" in result.prompt.casefold() or "martian" in result.prompt.casefold()
    assert "ceramic vase" not in result.prompt.casefold()
    _assert_structured_output_selected(result)


@pytest.mark.parametrize("think", [False, True])
async def test_live_refine_applies_the_requested_change(think: bool) -> None:
    adapter = _adapter()
    try:
        result = await adapter.compose(
            mode="refine",
            prompt=(
                "A woman holding a red umbrella beside a quiet canal, eye-level photograph, "
                "soft overcast light."
            ),
            direction="Change only the red umbrella to a blue umbrella.",
            think=think,
        )
    finally:
        await adapter.close()

    normalized = result.prompt.casefold()
    assert "blue umbrella" in normalized
    assert "red umbrella" not in normalized
    assert "quiet canal" in normalized
    assert "soft overcast light" in normalized
    _assert_structured_output_selected(result)


async def test_live_repeated_create_returns_four_distinct_prompts() -> None:
    adapter = _adapter()
    direction = "a red fox beneath moonlit pines"
    current = "an unrelated starting prompt"
    prompts: list[str] = []
    try:
        for _ in range(4):
            result = await adapter.compose(
                mode="create",
                prompt=current,
                direction=direction,
                excluded_prompts=prompts,
            )
            prompts.append(result.prompt)
            current = result.prompt
            _assert_structured_output_selected(result)
    finally:
        await adapter.close()

    assert len(set(prompts)) == 4
    for prompt in prompts:
        normalized = prompt.casefold()
        assert "red fox" in normalized
        assert "moonlit" in normalized or "moonlight" in normalized
        assert "pine" in normalized
        assert len(prompt.split()) >= 5


@pytest.mark.parametrize("mode", ["create", "refine"])
@pytest.mark.parametrize("think", [False, True])
def test_live_composition_api_persists_and_compiles_the_router_output(
    settings_factory, fake_state, mode: str, think: bool
) -> None:
    """Use the real router with isolated accounts/storage and a fake ComfyUI target."""
    settings = settings_factory(ollama_base_url=_BASE_URL)
    app = create_app(settings)
    with TestClient(app) as client:
        provision_user(client, username="live.router.audit")
        body = {
            "mode": mode,
            "prompt": "A woman holding a red umbrella beside a quiet canal.",
            "creative_direction": (
                "an astronaut tending a greenhouse on Mars"
                if mode == "create"
                else "Change only the red umbrella to a blue umbrella."
            ),
            "think": think,
        }
        outgoing: list[dict] = []

        async def record_request(request) -> None:
            if request.url.path.endswith("/api/chat"):
                outgoing.append(json.loads(request.content))

        adapter = app.state.container.ollama
        adapter._client.event_hooks["request"].append(record_request)
        response = client.post(
            "/api/prompt-assistant/compose", headers={"X-CSRF-Token": csrf(client)}, json=body
        )
        assert response.status_code == 200, response.text
        composed = response.json()
        assert outgoing
        document = adapter.router.doc
        for request in outgoing:
            # The service chosen by capability, sent as a service ID with a supported effort.
            assert request["model"] == composed["service"]
            assert request["model"] in {"daytime", "nighttime"}
            assert request["think"] == thinking_value(
                model_for(document, request["model"]), "xhigh" if think else False
            )
            assert body["creative_direction"] in request["messages"][0]["content"]
            assert (body["prompt"] in request["messages"][0]["content"]) is (mode == "refine")
        final = composed["prompt"].casefold()
        if mode == "refine":
            assert "blue umbrella" in final and "red umbrella" not in final
            assert "quiet canal" in final
        else:
            assert "astronaut" in final and "greenhouse" in final
            assert "umbrella" not in final

        payload = generation_payload(client, "stale browser prompt", seed=123)
        payload["prompt_assistant_run_id"] = composed["composition_id"]
        accepted = client.post(
            "/api/generations", headers={"X-CSRF-Token": csrf(client)}, json=payload
        )
        assert accepted.status_code == 201, accepted.text
        generation_id = accepted.json()["id"]
        recalled = client.get(f"/api/generations/{generation_id}/recall").json()
        assert recalled["parameters"]["prompt"] == composed["prompt"]
        with app.state.container.db.session_factory() as session:
            run = session.get(PromptAssistantRun, composed["composition_id"])
            assert run is not None
            assert run.model_name == composed["model"]
            assert run.prompt_before == body["prompt"]
            assert run.creative_direction == body["creative_direction"]
            assert run.thinking_enabled is think
            assert run.ollama_output == composed["prompt"]
            assert run.generation_id == generation_id
        assert fake_state.ollama_calls == []


def _vision_image() -> str:
    """A synthetic scene: a red disc on a white background, as a JPEG data URL."""

    image = Image.new("RGB", (512, 512), (255, 255, 255))
    ImageDraw.Draw(image).ellipse((96, 96, 416, 416), fill=(220, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


async def test_live_router_advertises_vision_for_the_configured_model() -> None:
    adapter = _adapter()
    try:
        capabilities = await adapter.capabilities()
    finally:
        await adapter.close()
    assert capabilities["vision"] is True, capabilities


async def test_live_selection_matches_the_reference_rule_on_the_current_document() -> None:
    """Read-only: the chosen services equal pick_service(doc, nsfw=True, fallback_any=True)."""

    adapter = _adapter()
    try:
        capabilities = await adapter.capabilities()
        document = adapter.router.doc if adapter.router else None
    finally:
        await adapter.close()
    assert document is not None, "the router's capabilities document could not be read"
    status = capabilities["router"]
    assert status["service"] == pick_service(document, nsfw=True, fallback_any=True)
    assert status["vision_service"] == pick_service(
        document, nsfw=True, require=["vision"], fallback_any=True
    )
    chosen = model_for(document, status["service"]) or {}
    assert status["fallback"] is (chosen.get("nsfw") is not True)
    assert status["configuration_id"] == (document.get("configuration") or {}).get("id")


async def test_live_event_stream_delivers_the_current_document() -> None:
    """Read-only: one subscription delivers the complete document first."""

    import asyncio

    import httpx

    stop = asyncio.Event()
    async with httpx.AsyncClient(
        base_url=_BASE_URL.removesuffix("/v1") if _BASE_URL else "",
        headers={"X-Client-Name": "comfyui-image-frontend-live-test"},
    ) as client:
        watch = RouterWatch(client)
        watch.add_listener(lambda _doc: stop.set())
        task = asyncio.create_task(watch._follow())
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert watch.doc is not None and watch.doc.get("schema_version") == 1


@pytest.mark.parametrize("think", [False, True])
async def test_live_vision_scores_visible_and_absent_expectations(think: bool) -> None:
    """The router must accept inline data-URL images and the model must judge the image."""

    adapter = _adapter()
    try:
        result = await adapter.evaluate_image(
            image_data_url=_vision_image(),
            expectations=[
                "A large red circle is the main subject",
                "A cat is visible in the image",
            ],
            threshold=80,
            think=think,
        )
    finally:
        await adapter.close()
    red_circle, cat = result.evaluation.results
    assert red_circle.score >= 70, result.evaluation
    assert cat.score <= 30, result.evaluation
    assert result.evaluation.passed is False


def test_live_expectation_check_revises_the_prompt_from_vision_feedback(
    settings_factory, fake_state
) -> None:
    """Real router for composition and review; fake ComfyUI; a synthetic probe image."""

    from tests.integration.test_expectation_checks import (
        check_body,
        finish_probe,
        seed_vision,
        started,
        step,
    )

    settings = settings_factory(ollama_base_url=_BASE_URL)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="live.expectations")
        seed_vision(client)
        body = check_body(
            client,
            prompt="A simple flat illustration of a red circle on a white background.",
            direction="Keep it a flat, minimal illustration.",
            expectations=["A blue square is the main subject"],
            max_attempts=2,
        )
        check = started(client, body)
        first = step(client, check)["attempts"][0]
        assert first["status"] == "ready", first
        assert step(client, check)["attempts"][0]["status"] == "generating"
        image = io.BytesIO()
        _red = Image.new("RGB", (512, 512), (255, 255, 255))
        ImageDraw.Draw(_red).ellipse((96, 96, 416, 416), fill=(220, 20, 20))
        _red.save(image, "PNG")
        finish_probe(client, check, content=image.getvalue())
        reviewed = step(client, check)
        attempt = reviewed["attempts"][0]
        assert attempt["status"] == "not_met", attempt
        assert attempt["score"] <= 40, attempt
        assert attempt["results"][0]["observation"]
        revised = step(client, check)["attempts"][1]
        assert revised["status"] == "ready", revised
        assert revised["prompt"] != first["prompt"]
        normalized = revised["prompt"].casefold()
        assert "blue" in normalized and "square" in normalized, revised["prompt"]
        assert fake_state.ollama_calls == []
