from datetime import UTC, datetime

import pytest
from app.models import (
    AutoGeneration,
    AutoGenerationCycle,
    GenerationPreparation,
    PromptGenerationRun,
)
from app.services.auto_generation_progress import project_progress


def progress(
    *,
    text_status="running",
    assistant=True,
    preparation_status="preparing",
    refined=False,
    images=False,
    enabled=True,
    status="preparing",
    cycle_revision=4,
    cycle_state="accepted",
    text_enabled=True,
):
    auto = AutoGeneration(
        user_id="owner",
        revision=4,
        enabled=enabled,
        status=status,
        snapshot_json={"assistant": {"mode": "refine"} if assistant else None},
    )
    cycle = AutoGenerationCycle(
        id="cycle",
        user_id="owner",
        revision=cycle_revision,
        state=cycle_state,
        created_at=datetime(2026, 9, 24, tzinfo=UTC),
        prompt="refined" if refined else None,
    )
    preparation = (
        GenerationPreparation(
            status=preparation_status,
            request_json={"assistant": {"mode": "refine"} if assistant else None},
            assistant_run_id="composition" if refined else None,
            prompt="refined" if refined else None,
        )
        if text_enabled
        else None
    )
    text = PromptGenerationRun(status=text_status, prompt="raw") if text_enabled else None
    return project_progress(auto, cycle, preparation, text, images_active=images)


@pytest.mark.parametrize("text_status", ["queued", "dispatching", "submitting", "running"])
def test_text_stage_does_not_publish_unfinished_text(text_status):
    result = progress(text_status=text_status)
    assert result.active_stages == ["prompt_generation"]
    assert result.raw_prompt is None
    assert result.refined_prompt is None


def test_raw_prompt_is_visible_throughout_refinement():
    result = progress(text_status="succeeded", preparation_status="refining")
    assert result.active_stages == ["creative_direction"]
    assert (result.cycle_id, result.revision, result.raw_prompt) == ("cycle", 4, "raw")


@pytest.mark.parametrize("assistant", [False, True])
def test_saved_text_is_visible_while_waiting_for_images(assistant):
    result = progress(
        text_status="succeeded", assistant=assistant, refined=assistant, preparation_status="ready"
    )
    assert result.active_stages == ["image"]
    assert result.raw_prompt == "raw"
    assert result.refined_prompt == ("refined" if assistant else None)


@pytest.mark.parametrize(
    "assistant, expected", [(False, ["image"]), (True, ["creative_direction", "image"])]
)
def test_image_only_and_assistant_lookahead(assistant, expected):
    result = progress(text_enabled=False, assistant=assistant, cycle_state="preparing", images=True)
    assert result.active_stages == expected


@pytest.mark.parametrize("status", ["blocked", "paused", "retrying", "completed", "off"])
def test_nonworking_statuses_clear_activity_without_losing_preview(status):
    result = progress(text_status="succeeded", refined=True, images=True, status=status)
    assert result.active_stages == []
    assert result.refined_prompt == "refined"


def test_stop_and_failed_text_do_not_claim_active_work():
    assert progress(enabled=False, images=True).active_stages == []
    result = progress(text_status="failed", preparation_status="failed")
    assert result.active_stages == []
    assert result.raw_prompt is None


@pytest.mark.parametrize("overrides", [{"cycle_revision": 3}, {"cycle_state": "discarded"}])
def test_obsolete_cycles_do_not_publish_prompts(overrides):
    result = progress(text_status="succeeded", refined=True, **overrides)
    assert result.cycle_id is None
    assert result.raw_prompt is None
    assert result.refined_prompt is None
    assert result.active_stages == []


def test_accepted_batch_only_reports_actual_image_activity():
    assert progress(
        text_status="succeeded", refined=True, preparation_status="accepted", images=True
    ).active_stages == ["image"]
    assert (
        progress(text_status="succeeded", refined=True, preparation_status="accepted").active_stages
        == []
    )
