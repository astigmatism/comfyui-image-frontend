"""Transaction-local preemption, shared by foreground admission and reel dispatch."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..demo_reel_models import DemoReelConfig, DemoReelEntry
from ..models import ACTIVE_STATUSES, Generation, GenerationStatus, utcnow

SYSTEM_OWNER_ID = "00000000-0000-4000-8000-000000000001"
IDLE_SECONDS = 300


def record_activity(session: Session, now: datetime | None = None) -> list[tuple[str, str]]:
    """Call under lock_user_state; revoke publication before requesting remote cancellation."""
    config = session.get(DemoReelConfig, 1)
    if config is None:
        return []
    now = now or utcnow()
    config.last_activity_at = now
    targets = []
    for entry in session.scalars(select(DemoReelEntry).where(DemoReelEntry.state == "running")):
        generation = session.get(Generation, entry.generation_id) if entry.generation_id else None
        if generation is None or generation.status in ACTIVE_STATUSES:
            entry.state = "discarded"
    for generation in session.scalars(
        select(Generation).where(
            Generation.owner_id == SYSTEM_OWNER_ID, Generation.status.in_(ACTIVE_STATUSES)
        )
    ):
        generation.pending_delete = True
        generation.cancel_requested_at = now
        if generation.status == GenerationStatus.QUEUED:
            generation.status = GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS
            generation.completed_at = now
        else:
            generation.status = GenerationStatus.CANCEL_REQUESTED
        if generation.comfyui_instance_id and generation.comfyui_prompt_id:
            targets.append((generation.comfyui_instance_id, generation.comfyui_prompt_id))
    return targets
