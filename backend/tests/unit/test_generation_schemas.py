from __future__ import annotations

import pytest
from app.schemas import GenerationCreate, PromptAssistantSnapshot
from pydantic import ValidationError


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
