from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ..models import (
    ACTIVE_STATUSES,
    AppLock,
    Collection,
    Generation,
    GenerationRun,
    GenerationRunMember,
    GenerationStatus,
)
from ..schemas import GenerationActivity, GenerationRunProgress


def current_run(session: Session, owner_id: str) -> GenerationRun | None:
    return session.scalar(
        select(GenerationRun)
        .where(GenerationRun.owner_id == owner_id)
        .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        .limit(1)
    )


def begin_run(session: Session, owner_id: str, total: int) -> GenerationRun:
    # Acquire SQLite's write lock before choosing a run. Concurrent tabs must append
    # to the same run, even when neither has accepted its first generation yet.
    statement = insert(AppLock).values(key="generation_runs", integer_value=1)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[AppLock.key],
            set_={"integer_value": AppLock.integer_value + 1},
        )
    )
    run = current_run(session, owner_id)
    active = run is not None and session.scalar(
        select(Generation.id)
        .join(GenerationRunMember, GenerationRunMember.generation_id == Generation.id)
        .where(GenerationRunMember.run_id == run.id, Generation.status.in_(ACTIVE_STATUSES))
        .limit(1)
    )
    if not active:
        run = GenerationRun(owner_id=owner_id, total_count=0)
        session.add(run)
        session.flush()
    assert run is not None
    run.total_count += total
    run.updated_at = datetime.now(UTC)
    session.flush()
    return run


def retain_deleted_outcome(session: Session, generation: Generation) -> None:
    run = session.scalar(
        select(GenerationRun)
        .join(GenerationRunMember, GenerationRunMember.run_id == GenerationRun.id)
        .where(GenerationRunMember.generation_id == generation.id)
    )
    if run is None:
        return
    if generation.status == GenerationStatus.SUCCEEDED:
        run.deleted_succeeded_count += 1
    elif generation.status in {
        GenerationStatus.CANCELLED_WITH_ARTIFACTS,
        GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS,
    }:
        run.deleted_cancelled_count += 1
    else:
        run.deleted_failed_count += 1
    run.updated_at = datetime.now(UTC)


def _inflight_eta_fractions(session: Session, run_id: str, now: datetime) -> tuple[float, bool]:
    """Sum the ETA-derived completion fraction of each active run member.

    Returns ``(fractional_sum, any_member_had_eta)``. A member with a persisted
    ETA contributes ``elapsed / (elapsed + remaining)`` clamped to [0, 1]; queued,
    dispatching and cancel-requested items carry no ETA and contribute 0.
    """
    fractional_sum = 0.0
    has_eta = False
    for started_at, progress_json in session.execute(
        select(Generation.started_at, Generation.progress_json)
        .join(GenerationRunMember, GenerationRunMember.generation_id == Generation.id)
        .where(
            GenerationRunMember.run_id == run_id,
            Generation.status.in_(ACTIVE_STATUSES),
        )
    ):
        fraction = _inflight_eta_fraction(started_at, progress_json, now)
        if fraction is None:
            continue
        has_eta = True
        fractional_sum += fraction
    return fractional_sum, has_eta


def _inflight_eta_fraction(
    started_at: datetime | None,
    progress_json: Mapping[str, Any] | None,
    now: datetime,
) -> float | None:
    if started_at is None or not isinstance(progress_json, Mapping):
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    eta = progress_json.get("eta")
    if not isinstance(eta, Mapping):
        return None
    remaining = eta.get("remaining_seconds")
    if (
        isinstance(remaining, bool)
        or not isinstance(remaining, (int, float))
        or not (0 < remaining < float("inf"))
    ):
        return None
    # A stored ETA belongs to the current attempt only when it was written after
    # the generation started; the estimator rewrites it on every attempt.
    updated_raw = eta.get("updated_at")
    if isinstance(updated_raw, str):
        try:
            updated = datetime.fromisoformat(updated_raw)
        except ValueError:
            return None
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if updated < started_at:
            return None
    elapsed = (now - started_at).total_seconds()
    if elapsed < 0:
        elapsed = 0.0
    return min(1.0, elapsed / (elapsed + remaining))


def activity_snapshot(session: Session, owner_id: str) -> GenerationActivity:
    run = current_run(session, owner_id)
    progress = None
    if run is not None:
        rows = session.execute(
            select(Generation.status, func.count(), func.max(Generation.completed_at))
            .join(GenerationRunMember, GenerationRunMember.generation_id == Generation.id)
            .where(GenerationRunMember.run_id == run.id, Generation.owner_id == owner_id)
            .group_by(Generation.status)
        ).all()
        counts = {status: count for status, count, _ in rows}
        remaining = sum(counts.get(status, 0) for status in ACTIVE_STATUSES)
        succeeded = counts.get(GenerationStatus.SUCCEEDED, 0) + run.deleted_succeeded_count
        cancelled = (
            sum(
                counts.get(status, 0)
                for status in (
                    GenerationStatus.CANCELLED_WITH_ARTIFACTS,
                    GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS,
                )
            )
            + run.deleted_cancelled_count
        )
        resolved = run.total_count - remaining
        completed = max(
            [run.updated_at.replace(tzinfo=UTC)]
            + [timestamp.replace(tzinfo=UTC) for _, _, timestamp in rows if timestamp]
        )
        completed_fraction = None
        if remaining > 0 and run.total_count > 0:
            fractional_sum, has_eta = _inflight_eta_fractions(session, run.id, datetime.now(UTC))
            if has_eta:
                completed_fraction = round(
                    min(1.0, (resolved + fractional_sum) / run.total_count), 4
                )
        progress = GenerationRunProgress(
            id=run.id,
            total_count=run.total_count,
            resolved_count=resolved,
            remaining_count=remaining,
            succeeded_count=succeeded,
            cancelled_count=cancelled,
            failed_count=resolved - succeeded - cancelled,
            completed_at=completed if remaining == 0 else None,
            completed_fraction=completed_fraction,
        )

    parents: dict[str, str | None] = {
        collection_id: parent_id
        for collection_id, parent_id in session.execute(
            select(Collection.id, Collection.parent_id).where(Collection.owner_id == owner_id)
        )
    }
    collection_counts = dict.fromkeys(parents, 0)
    remaining_count = 0
    for collection_id, count in session.execute(
        select(Generation.collection_id, func.count())
        .where(Generation.owner_id == owner_id, Generation.status.in_(ACTIVE_STATUSES))
        .group_by(Generation.collection_id)
    ):
        remaining_count += count
        visited: set[str] = set()
        while collection_id in parents and collection_id not in visited:
            visited.add(collection_id)
            collection_counts[collection_id] += count
            collection_id = parents[collection_id]

    return GenerationActivity(
        run=progress,
        remaining_count=remaining_count,
        collection_remaining_counts=collection_counts,
        collection_generation_counts={
            str(collection_id): count
            for collection_id, count in session.execute(
                select(Generation.collection_id, func.count())
                .where(
                    Generation.owner_id == owner_id,
                    Generation.collection_id.is_not(None),
                    Generation.pending_delete.is_(False),
                )
                .group_by(Generation.collection_id)
            )
        },
    )
