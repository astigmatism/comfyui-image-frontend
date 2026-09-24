"""Project automatic pipeline progress without changing its accepted-prompt baseline."""

from __future__ import annotations

from ..models import AutoGeneration, AutoGenerationCycle, GenerationPreparation, PromptGenerationRun
from ..schemas import AutoGenerationProgress


def project_progress(
    auto: AutoGeneration,
    cycle: AutoGenerationCycle | None,
    preparation: GenerationPreparation | None,
    text: PromptGenerationRun | None,
    *,
    images_active: bool,
) -> AutoGenerationProgress:
    progress = AutoGenerationProgress(revision=auto.revision)
    if cycle and cycle.revision == auto.revision and cycle.state != "discarded":
        progress.cycle_id = cycle.id
        progress.cycle_created_at = cycle.created_at
        if preparation:
            if text and text.status == "succeeded":
                progress.raw_prompt = text.prompt
            if preparation.assistant_run_id:
                progress.refined_prompt = preparation.prompt
            if preparation.status in {"preparing", "refining", "ready"}:
                if text and text.status in {"queued", "dispatching", "submitting", "running"}:
                    progress.active_stages.append("prompt_generation")
                elif text and text.status == "succeeded":
                    progress.active_stages.append(
                        "creative_direction"
                        if preparation.request_json.get("assistant")
                        and not preparation.assistant_run_id
                        else "image"
                    )
        else:
            progress.refined_prompt = cycle.prompt
            if cycle.state == "preparing" and auto.snapshot_json.get("assistant"):
                progress.active_stages.append("creative_direction")
            elif cycle.state in {"preparing", "ready"}:
                progress.active_stages.append("image")
    if images_active and "image" not in progress.active_stages:
        progress.active_stages.append("image")
    if not auto.enabled or auto.status in {"blocked", "paused", "retrying", "completed", "off"}:
        progress.active_stages = []
    return progress
