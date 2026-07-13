from __future__ import annotations

from ..errors import AppError
from ..models import GenerationStatus

ALLOWED_TRANSITIONS: dict[GenerationStatus, set[GenerationStatus]] = {
    GenerationStatus.QUEUED: {
        GenerationStatus.DISPATCHING,
        GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS,
    },
    GenerationStatus.DISPATCHING: {
        GenerationStatus.QUEUED,
        GenerationStatus.RUNNING,
        GenerationStatus.CANCEL_REQUESTED,
        GenerationStatus.FAILED_WITHOUT_ARTIFACTS,
        GenerationStatus.INTERRUPTED,
    },
    GenerationStatus.RUNNING: {
        GenerationStatus.CANCEL_REQUESTED,
        GenerationStatus.SUCCEEDED,
        GenerationStatus.FAILED_WITH_ARTIFACTS,
        GenerationStatus.FAILED_WITHOUT_ARTIFACTS,
        GenerationStatus.CANCELLED_WITH_ARTIFACTS,
        GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS,
        GenerationStatus.INTERRUPTED,
    },
    GenerationStatus.CANCEL_REQUESTED: {
        GenerationStatus.SUCCEEDED,
        GenerationStatus.CANCELLED_WITH_ARTIFACTS,
        GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS,
        GenerationStatus.FAILED_WITH_ARTIFACTS,
        GenerationStatus.FAILED_WITHOUT_ARTIFACTS,
        GenerationStatus.INTERRUPTED,
    },
    GenerationStatus.SUCCEEDED: set(),
    GenerationStatus.CANCELLED_WITH_ARTIFACTS: set(),
    GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS: set(),
    GenerationStatus.FAILED_WITH_ARTIFACTS: set(),
    GenerationStatus.FAILED_WITHOUT_ARTIFACTS: set(),
    GenerationStatus.INTERRUPTED: set(),
}


def assert_transition(current: GenerationStatus, target: GenerationStatus) -> None:
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise AppError(
            "invalid_status_transition",
            f"Generation cannot transition from {current.value} to {target.value}.",
            status_code=409,
        )
