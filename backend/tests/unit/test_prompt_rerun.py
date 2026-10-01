from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.errors import AppError
from app.schemas import PromptRerunCreate
from app.services.prompt_rerun import (
    PromptPlan,
    SourcePrompt,
    build_requests,
    original_dimensions,
    original_seed,
    plan_prompts,
    target_inputs,
)
from pydantic import ValidationError

REVISION = {
    "publication_id": "pub",
    "workflow_sha256": "a" * 64,
    "api_sha256": "b" * 64,
    "manifest_sha256": "c" * 64,
}
CONTRACT = {
    "inputs": [
        {"id": "prompt", "type": "string", "semantic_role": "positive_prompt"},
        {"id": "width", "type": "integer", "semantic_role": "width", "minimum": 16, "step": 8},
        {"id": "height", "type": "integer", "semantic_role": "height", "maximum": 2048},
        {"id": "seed", "type": "seed", "semantic_role": "seed"},
        {"id": "checkpoint", "type": "choice", "semantic_role": "checkpoint"},
        {"id": "steps", "type": "integer"},
    ]
}
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def payload(**overrides):
    data = {
        "generation_ids": ["g1"],
        "folder_name": "  Re-run  ",
        "source_key": "source",
        "revision": REVISION,
        "parameters": {
            "prompt": "panel prompt",
            "width": 512,
            "height": 768,
            "seed": "42",
            "steps": 20,
        },
    }
    data.update(overrides)
    return PromptRerunCreate.model_validate(data)


def prompt(index, text, **extra):
    return SourcePrompt(
        generation_id=f"g{index}", prompt=text, accepted_at=T0 + timedelta(seconds=index), **extra
    )


def plan(*prompts):
    return PromptPlan(prompts=list(prompts))


def test_plan_orders_oldest_first_skips_empty_and_deduplicates():
    rows = [prompt(3, "b"), prompt(1, "a"), prompt(2, "  "), prompt(4, "a")]
    result = plan_prompts(rows, skip_duplicates=True)
    assert [item.generation_id for item in result.prompts] == ["g1", "g3"]
    assert (result.generation_count, result.skipped_count, result.duplicate_count) == (4, 1, 1)
    assert result.unique_prompt_count == 2
    kept = plan_prompts(rows, skip_duplicates=False)
    assert [item.generation_id for item in kept.prompts] == ["g1", "g3", "g4"]


def test_prompt_is_exact_and_panel_prompt_and_seed_are_replaced():
    text = "  exact\nprompt  with spacing "
    built = build_requests(CONTRACT, payload(), plan(prompt(1, text, seed="7")), "folder")
    [request] = built.requests
    assert request.parameters["prompt"] == text
    assert request.parameters["seed"] == "random"
    assert request.parameters["steps"] == 20
    assert request.collection_id == "folder"
    assert request.prompt_assistant is None and request.prompt_assistant_run_id is None


def test_expands_prompts_by_variants_by_quantity_in_order():
    variants = [{"checkpoint": "a"}, {"checkpoint": "b"}]
    built = build_requests(
        CONTRACT,
        payload(model_variants=variants, quantity=2),
        plan(prompt(1, "one"), prompt(2, "two")),
        None,
    )
    assert [(r.parameters["prompt"], r.parameters["checkpoint"]) for r in built.requests] == [
        ("one", "a"),
        ("one", "a"),
        ("one", "b"),
        ("one", "b"),
        ("two", "a"),
        ("two", "a"),
        ("two", "b"),
        ("two", "b"),
    ]


def test_variant_cannot_override_prompt():
    built = build_requests(
        CONTRACT, payload(model_variants=[{"prompt": "hijack"}]), plan(prompt(1, "real")), None
    )
    assert built.requests[0].parameters["prompt"] == "real"


def test_keep_original_resolution_uses_valid_dimensions_and_counts_fallbacks():
    built = build_requests(
        CONTRACT,
        payload(keep_original_resolution=True),
        plan(
            prompt(1, "fits", width=1024, height=1536),
            prompt(2, "unknown"),
            prompt(3, "off-step", width=1001, height=1536),
            prompt(4, "too tall", width=1024, height=4096),
        ),
        None,
    )
    sizes = [(r.parameters["width"], r.parameters["height"]) for r in built.requests]
    assert sizes == [(1024, 1536), (512, 768), (512, 768), (512, 768)]
    assert built.resolution_fallback_count == 3


def test_chosen_resolution_applies_when_not_keeping_original():
    built = build_requests(CONTRACT, payload(), plan(prompt(1, "x", width=1024, height=1024)), None)
    assert (built.requests[0].parameters["width"], built.requests[0].parameters["height"]) == (
        512,
        768,
    )


def test_original_seed_mode_reuses_seed_or_falls_back_to_random():
    built = build_requests(
        CONTRACT,
        payload(seed_mode="original"),
        plan(prompt(1, "seeded", seed="123456789012345678901"), prompt(2, "unseeded")),
        None,
    )
    assert [r.parameters["seed"] for r in built.requests] == ["123456789012345678901", "random"]


def test_original_seed_requires_single_quantity_and_folder_name_is_normalized():
    assert payload().folder_name == "Re-run"
    with pytest.raises(ValidationError):
        payload(seed_mode="original", quantity=2)
    with pytest.raises(ValidationError):
        payload(folder_name="   ")
    with pytest.raises(ValidationError):
        payload(quantity=17)
    with pytest.raises(ValidationError):
        payload(model_variants=[{"checkpoint": "a"}, {"checkpoint": "a"}])


def test_limit_is_enforced_before_building():
    many = plan(*[prompt(index, f"p{index}") for index in range(17)])
    with pytest.raises(AppError) as error:
        build_requests(CONTRACT, payload(quantity=16), many, None)
    assert error.value.code == "prompt_rerun_too_large"
    assert error.value.details == {"planned": 272, "limit": 256}
    assert (
        len(build_requests(CONTRACT, payload(quantity=16), plan(*many.prompts[:16]), None).requests)
        == 256
    )


def test_source_without_prompt_input_is_rejected():
    with pytest.raises(AppError) as error:
        target_inputs({"inputs": [{"id": "steps", "type": "integer"}]})
    assert error.value.code == "source_kind_invalid"


def test_historical_dimensions_and_seed_extraction():
    assert original_dimensions(CONTRACT, {"width": 640, "height": 480}) == (640, 480)
    assert original_dimensions(CONTRACT, {"width": True, "height": 0}) == (None, None)
    legacy = {"controls": [{"id": "size", "type": "resolution"}]}
    assert original_dimensions(legacy, {"size": {"width": 800, "height": 600}}) == (800, 600)
    assert original_seed(CONTRACT, {"seed": 99}) == "99"
    assert original_seed(CONTRACT, {}) is None
