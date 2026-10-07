from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from app.config import Settings
from app.domain.expectations import (
    MAX_EXPECTATIONS,
    best_attempt,
    build_revision_direction,
    evaluation_from_json,
    evaluation_schema,
    first_attempt_direction,
    normalize_expectations,
    parse_expectations,
    score_results,
    validate_evaluation,
)
from app.errors import AppError
from app.schemas import ExpectationCheckCreate, SharedSettings
from app.services.expectation_checks import encode_for_vision
from app.services.ollama import OllamaAdapter
from PIL import Image
from pydantic import ValidationError
from tests.router_fixtures import (
    CAPABILITIES_PATH,
    capabilities_response,
    paired_document,
    router_document,
    router_model,
)

EXPECTATIONS = ["The keeper wears a red raincoat", "A lit lighthouse beam is visible"]


def test_expectations_parse_one_per_line_and_drop_bullets() -> None:
    text = "- red raincoat\n\n  2. lighthouse beam  \n* storm\n\u2022 waves\n"
    assert parse_expectations(text) == ["red raincoat", "lighthouse beam", "storm", "waves"]
    assert normalize_expectations(["  - a ", "", "b"]) == ["a", "b"]
    with pytest.raises(ValueError, match="at least one"):
        normalize_expectations(["", "  "])
    with pytest.raises(ValueError, match="at most"):
        normalize_expectations([f"item {index}" for index in range(MAX_EXPECTATIONS + 1)])
    with pytest.raises(ValueError, match="under"):
        normalize_expectations(["x" * 301])


def test_every_expectation_must_reach_the_pass_score() -> None:
    evaluation = score_results(EXPECTATIONS, [(95, "red"), (79, "faint")], 80, "close")
    assert evaluation.score == 79
    assert evaluation.passed is False
    assert [item.met for item in evaluation.results] == [True, False]
    passing = score_results(EXPECTATIONS, [(80, "red"), (91, "bright")], 80, "")
    assert passing.passed is True and passing.score == 80
    stored = evaluation_from_json(passing.as_json())
    assert stored == passing
    assert evaluation_from_json({}) is None


def test_validate_evaluation_rejects_structural_faults() -> None:
    valid = {
        "results": [
            {"expectation": 2, "score": 60, "observation": "dim"},
            {"expectation": 1, "score": 90.0, "observation": "red"},
        ],
        "summary": "Beam is dim.",
    }
    evaluation = validate_evaluation(valid, EXPECTATIONS, 80)
    assert [item.score for item in evaluation.results] == [90, 60]
    assert evaluation.results[0].expectation == EXPECTATIONS[0]
    faults = [
        {"results": []},
        {"results": [{"expectation": 1, "score": 90, "observation": ""}], "summary": ""},
        {
            "results": [
                {"expectation": 1, "score": 101, "observation": ""},
                {"expectation": 2, "score": 1, "observation": ""},
            ]
        },
        {
            "results": [
                {"expectation": 1, "score": 50, "observation": ""},
                {"expectation": 1, "score": 50, "observation": ""},
            ]
        },
        {
            "results": [
                {"expectation": 3, "score": 50, "observation": ""},
                {"expectation": 1, "score": 50, "observation": ""},
            ]
        },
        {
            "results": [
                {"expectation": 1, "score": True, "observation": ""},
                {"expectation": 2, "score": 1, "observation": ""},
            ]
        },
        "not an object",
    ]
    for fault in faults:
        with pytest.raises(ValueError):
            validate_evaluation(fault, EXPECTATIONS, 80)


def test_directions_carry_expectations_and_feedback() -> None:
    first = first_attempt_direction("warm film still", EXPECTATIONS)
    assert first.startswith("warm film still\n\nThe image must also satisfy")
    assert "- The keeper wears a red raincoat" in first
    assert first_attempt_direction("", EXPECTATIONS).startswith("The image must also satisfy")
    evaluation = score_results(EXPECTATIONS, [(40, "The coat is yellow."), (90, "Lit.")], 80, "x")
    revision = build_revision_direction("warm film still", evaluation)
    assert revision.startswith("warm film still")
    assert "Unmet expectations:\n- The keeper wears a red raincoat (score 40/100): The coat" in (
        revision
    )
    assert "Expectations already met (keep them):\n- A lit lighthouse beam is visible" in revision
    assert "Reviewer summary: x" in revision


def test_best_attempt_prefers_higher_score_then_later_attempt() -> None:
    assert best_attempt([(1, 40), (2, 70), (3, 70)]) == 3
    assert best_attempt([(1, 90), (2, 70)]) == 1
    assert best_attempt([]) is None


def test_settings_and_check_request_bounds() -> None:
    settings = SharedSettings.model_validate(
        {"creative_direction_expectations": {"enabled": True, "text": "a\nb", "threshold": 90}}
    )
    assert settings.creative_direction_expectations.max_attempts == 5
    with pytest.raises(ValidationError):
        SharedSettings.model_validate({"creative_direction_expectations": {"threshold": 0}})
    with pytest.raises(ValidationError):
        SharedSettings.model_validate({"creative_direction_expectations": {"max_attempts": 11}})
    base = {
        "purpose": "apply",
        "assistant": {"mode": "refine", "prompt": "x", "creative_direction": ""},
        "expectations": ["- a", "b"],
        "items": [{"source_key": "k"}],
    }
    parsed = ExpectationCheckCreate.model_validate(base)
    assert parsed.expectations == ["a", "b"]
    for change in (
        {"items": [{"source_key": "k"}, {"source_key": "k"}]},
        {"purpose": "generate", "items": [{"source_key": "k"}, {"source_key": "other"}]},
        {"items": [{"source_key": "k", "prompt_assistant_run_id": "run"}]},
        {"expectations": [""]},
        {"threshold": 101},
    ):
        with pytest.raises(ValidationError):
            ExpectationCheckCreate.model_validate({**base, **change})


def test_vision_encoding_flattens_downscales_and_uses_jpeg(tmp_path) -> None:
    class Store:
        def read(self, relative: str) -> bytes:
            assert relative == "assets/x/image.png"
            buffer = io.BytesIO()
            Image.new("RGBA", (2048, 1024), (255, 0, 0, 0)).save(buffer, "PNG")
            return buffer.getvalue()

    data_url = encode_for_vision(Store(), "assets/x/image.png")  # type: ignore[arg-type]
    assert data_url.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (1024, 512)
        # Fully transparent pixels flatten onto white rather than black.
        assert decoded.getpixel((10, 10)) == pytest.approx((255, 255, 255), abs=3)


def _adapter(handler, **settings: object) -> OllamaAdapter:
    async def skip_retry(_: float) -> None:
        pass

    return OllamaAdapter(
        Settings(test_mode=True, ollama_base_url="http://router.test", **settings),
        transport=httpx.MockTransport(handler),
        retry_sleeper=skip_retry,
        seed_resolver=lambda minimum, maximum: 41,
    )


def _scores(*scores: int, summary: str = "ok") -> str:
    return json.dumps(
        {
            "results": [
                {"expectation": index + 1, "score": score, "observation": f"seen {index + 1}"}
                for index, score in enumerate(scores)
            ],
            "summary": summary,
        }
    )


@pytest.mark.parametrize("think", [False, True])
async def test_evaluate_image_sends_inline_image_schema_and_never_the_prompt(think: bool) -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            return capabilities_response(request)
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "model": "resolved-vision",
                "message": {"content": _scores(92, 70), "thinking": "looked carefully"},
                "done_reason": "stop",
            },
        )

    adapter = _adapter(handler)
    try:
        result = await adapter.evaluate_image(
            image_data_url="data:image/jpeg;base64,AAAA",
            expectations=EXPECTATIONS,
            threshold=80,
            think=think,
        )
    finally:
        await adapter.close()
    assert result.model == "resolved-vision"
    assert result.evaluation.score == 70 and result.evaluation.passed is False
    payload = calls[0]
    message = payload["messages"][0]
    assert message["role"] == "user"
    assert message["images"] == ["data:image/jpeg;base64,AAAA"]
    assert "1. The keeper wears a red raincoat\n2. A lit lighthouse beam" in message["content"]
    assert payload["format"] == evaluation_schema(2)
    assert payload["model"] == "nighttime"
    assert payload["think"] == ("xhigh" if think else False)
    assert payload["options"]["temperature"] == 0.1
    assert payload["options"]["seed"] == payload["seed"] == 41
    assert "looked carefully" not in json.dumps(result.diagnostics)


async def test_evaluate_image_escalates_budget_redraws_invalid_sheets_and_reads_thinking() -> None:
    responses = [
        {"model": "m", "message": {"content": "", "thinking": "long"}, "done_reason": "length"},
        {"model": "m", "message": {"content": _scores(50)}, "done_reason": "stop"},
        {
            "model": "m",
            "message": {"content": "", "thinking": _scores(85, 90)},
            "done_reason": "stop",
        },
    ]
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            return capabilities_response(request)
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=responses[len(calls) - 1])

    adapter = _adapter(handler)
    try:
        result = await adapter.evaluate_image(
            image_data_url="data:image/jpeg;base64,AAAA", expectations=EXPECTATIONS, threshold=80
        )
    finally:
        await adapter.close()
    assert result.evaluation.passed is True
    assert [call["options"]["num_predict"] for call in calls] == [2048, 4096, 2048]
    # The structurally invalid sheet (one score for two expectations) redraws with a new seed.
    assert [call["seed"] for call in calls] == [41, 41, 42]
    attempts = result.diagnostics["attempts"]
    assert attempts[1]["rejection_reason"] == "not every expectation was scored"
    assert attempts[-1]["selected_field"] == "thinking"


async def test_evaluate_image_reports_invalid_responses_and_router_rejections() -> None:
    def malformed(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            return capabilities_response(request)
        return httpx.Response(
            200, json={"model": "m", "message": {"content": "no json"}, "done_reason": "stop"}
        )

    adapter = _adapter(malformed)
    with pytest.raises(AppError) as invalid:
        await adapter.evaluate_image(
            image_data_url="data:image/jpeg;base64,AAAA", expectations=EXPECTATIONS, threshold=80
        )
    await adapter.close()
    assert invalid.value.code == "vision_check_invalid_response"
    assert len(invalid.value.details["attempt_diagnostics"]) == 3

    def rejected(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            return capabilities_response(request)
        return httpx.Response(400, json={"error": {"code": "UNSUPPORTED_PROFILE_CAPABILITY"}})

    adapter = _adapter(rejected)
    with pytest.raises(AppError) as unavailable:
        await adapter.evaluate_image(
            image_data_url="data:image/jpeg;base64,AAAA", expectations=EXPECTATIONS, threshold=80
        )
    await adapter.close()
    assert unavailable.value.code == "vision_unavailable"


async def test_capabilities_come_from_the_router_document_never_from_names() -> None:
    def paired(request: httpx.Request) -> httpx.Response:
        assert request.url.path == CAPABILITIES_PATH, "no /api/tags or /api/show probe"
        return capabilities_response(request)

    adapter = _adapter(paired)
    capabilities = await adapter.capabilities()
    await adapter.close()
    assert capabilities["vision"] is True
    assert capabilities["capabilities"] == ["completion", "thinking", "tools", "vision"]
    assert capabilities["router"]["service"] == "nighttime"
    assert capabilities["router"]["vision_service"] == "nighttime"
    assert capabilities["router"]["vision_fallback"] is False

    # Only the non-NSFW model accepts images: vision checks use it, text stays on Nighttime.
    split = router_document(
        [
            router_model("daytime", nsfw=False, score=68.3, vision=True),
            router_model("nighttime", nsfw=True, score=64.9, vision=False),
        ]
    )

    def split_handler(request: httpx.Request) -> httpx.Response:
        return capabilities_response(request, split)

    adapter = _adapter(split_handler)
    capabilities = await adapter.capabilities()
    await adapter.close()
    assert capabilities["vision"] is True
    assert capabilities["router"]["service"] == "nighttime"
    assert capabilities["router"]["vision_service"] == "daytime"
    assert capabilities["router"]["vision_fallback"] is True
    assert "inspect images" in capabilities["router"]["notice"]

    blind = paired_document(vision=False)

    def blind_handler(request: httpx.Request) -> httpx.Response:
        return capabilities_response(request, blind)

    adapter = _adapter(blind_handler)
    capabilities = await adapter.capabilities()
    await adapter.close()
    assert capabilities["vision"] is False
    assert capabilities["router"]["vision_service"] is None

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    adapter = _adapter(offline)
    capabilities = await adapter.capabilities()
    await adapter.close()
    assert capabilities["vision"] is False
    assert capabilities["router"]["state"] == "unavailable"
    assert capabilities["router"]["reason"] == "router_unreachable"


async def test_vision_check_uses_the_only_model_that_accepts_images() -> None:
    document = router_document(
        [
            router_model("daytime", nsfw=False, score=60.0, vision=True),
            router_model("nighttime", nsfw=True, score=70.0, vision=False),
        ]
    )
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            return capabilities_response(request, document)
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"model": "x", "message": {"content": _scores(90, 95)}, "done_reason": "stop"},
        )

    adapter = _adapter(handler)
    try:
        result = await adapter.evaluate_image(
            image_data_url="data:image/jpeg;base64,AAAA", expectations=EXPECTATIONS, threshold=80
        )
    finally:
        await adapter.close()
    assert [call["model"] for call in calls] == ["daytime"]
    assert result.service == "daytime"
    assert result.fallback is True
    assert result.diagnostics["router"]["reason"] == "no_nsfw_model_with_vision"


async def test_vision_check_without_any_image_model_is_unavailable_without_sending() -> None:
    document = paired_document(vision=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == CAPABILITIES_PATH, "no model can take the image"
        return capabilities_response(request, document)

    adapter = _adapter(handler)
    with pytest.raises(AppError) as unavailable:
        await adapter.evaluate_image(
            image_data_url="data:image/jpeg;base64,AAAA", expectations=EXPECTATIONS, threshold=80
        )
    await adapter.close()
    assert unavailable.value.code == "vision_unavailable"
    assert unavailable.value.status_code == 503


async def test_a_declining_non_nsfw_reviewer_is_reported_without_redraws() -> None:
    from tests.router_fixtures import solo_document

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            return capabilities_response(request, solo_document())
        calls.append(json.loads(request.content))
        return httpx.Response(
            502,
            json={"error": {"code": "MALFORMED_STRUCTURED_OUTPUT", "message": "not the schema"}},
        )

    adapter = _adapter(handler)
    with pytest.raises(AppError) as declined:
        await adapter.evaluate_image(
            image_data_url="data:image/jpeg;base64,AAAA", expectations=EXPECTATIONS, threshold=80
        )
    await adapter.close()
    assert declined.value.code == "ollama_model_declined"
    assert declined.value.message.startswith("No NSFW model is available; Daytime declined")
    assert len(calls) == 1
