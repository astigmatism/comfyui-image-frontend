from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.schemas import GenerationCreate, GenerationSummary, PromptAssistantSnapshot
from pydantic import ValidationError


@pytest.mark.parametrize(
    "accepted_at",
    [
        datetime(2026, 9, 25, 7, 11, 40, 123456),
        datetime(2026, 9, 25, 7, 11, 40, 123456, tzinfo=UTC),
        datetime(2026, 9, 25, 0, 11, 40, 123456, tzinfo=timezone(timedelta(hours=-7))),
        "2026-09-25T07:11:40.123456",
        "2026-09-25T16:11:40.123456+09:00",
    ],
)
def test_generation_acceptance_is_always_serialized_as_utc(accepted_at) -> None:
    summary = GenerationSummary(
        id="generation",
        status="queued",
        workflow_display_name="Workflow",
        comfyui_instance_id="default",
        comfyui_instance_label="Primary",
        accepted_at=accepted_at,
        artifact_count=0,
        image_count=0,
        final_artifact_count=0,
    )
    assert summary.accepted_at.tzinfo is UTC
    assert summary.model_dump(mode="json")["accepted_at"] == "2026-09-25T07:11:40.123456Z"


def test_prompt_assistant_snapshot_normalizes_blank_instructions() -> None:
    snapshot = PromptAssistantSnapshot(
        mode="refine", creative_direction="a fox", instructions="   "
    )
    assert snapshot.instructions is None
    assert snapshot.thinking_enabled is True
    assert PromptAssistantSnapshot(mode="create", instructions=None).mode == "create"


def test_prompt_assistant_snapshot_rejects_unknown_mode_and_oversized_instructions() -> None:
    with pytest.raises(ValidationError):
        PromptAssistantSnapshot(mode="polish")
    with pytest.raises(ValidationError):
        PromptAssistantSnapshot(instructions="x" * 8001)


def test_generation_create_accepts_optional_assistant_snapshot() -> None:
    request = GenerationCreate(
        source_key="krea",
        parameters={"prompt": "a fox"},
        prompt_assistant={
            "mode": "create",
            "creative_direction": "winter light",
            "instructions": "keep it simple",
            "thinking_enabled": False,
        },
    )
    assert request.prompt_assistant is not None
    assert request.prompt_assistant.thinking_enabled is False
    # Omitting the snapshot entirely remains valid for older clients.
    omitted = GenerationCreate(source_key="krea", parameters={"prompt": "a fox"})
    assert omitted.prompt_assistant is None
