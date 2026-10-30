from __future__ import annotations

import math
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .schemas import APIModel


class DemoReelSlot(APIModel):
    id: UUID
    title: str = Field(default="", max_length=120)
    prompt: str = Field(min_length=1, max_length=100_000)
    resolutions: dict[str, dict[str, int]] = Field(default_factory=dict)

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a prompt for every slot.")
        return value


class DemoReelRecipe(APIModel):
    source_key: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)
    strength: float = Field(default=1.0, gt=0, allow_inf_nan=False)


class DemoReelUpdate(APIModel):
    revision: int = Field(ge=0)
    paused: bool = False
    rating_user_id: UUID | None = None
    slots: list[DemoReelSlot] = Field(default_factory=list, max_length=100)
    recipes: dict[str, DemoReelRecipe] = Field(default_factory=dict)
    strengths: dict[str, dict[str, float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self) -> DemoReelUpdate:
        if len({slot.id for slot in self.slots}) != len(self.slots):
            raise ValueError("Slot IDs must be unique.")
        if len(self.model_dump_json()) > 2_000_000:
            raise ValueError("Demo Reel settings exceed the size limit.")
        for strengths in self.strengths.values():
            if any(not math.isfinite(value) or value <= 0 for value in strengths.values()):
                raise ValueError("Strength overrides must be positive finite numbers.")
        return self


class DemoReelAction(APIModel):
    action: Literal[
        "favorite", "unfavorite", "copy", "move", "delete", "regenerate", "retry", "pause", "resume"
    ]
    generation_ids: list[str] = Field(default_factory=list, max_length=1000)
    collection_id: str | None = None
    key: UUID | None = None
    revision: int | None = Field(default=None, ge=0)


class DemoReelEnabled(APIModel):
    """Whether this LoRA takes part in the Demo Reel, set by an administrator per LoRA."""

    enabled: bool
