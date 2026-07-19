from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import statistics
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session, load_only, sessionmaker

from ..models import (
    ACTIVE_STATUSES,
    Generation,
    GenerationEvent,
    GenerationStatus,
    GenerationTimingAuditState,
    GenerationTimingProfile,
)

logger = logging.getLogger(__name__)

TIMING_FEATURE_VERSION = 1
_DEFAULT_AUDIT_INTERVAL_SECONDS = 300.0
_DEFAULT_AUDIT_BATCH_SIZE = 24
_DEFAULT_MAX_PROFILE_SAMPLES = 64
_DEFAULT_MAX_PROFILES = 4_096
_DEFAULT_AUDIT_TIME_BUDGET_SECONDS = 2.0
_AUDIT_BUSY_TIMEOUT_MS = 250
_AUDIT_STATE_KEY = "generation_eta"
_MAX_ACTIVE_FEATURE_CACHE = 256
_MAX_AUDIT_PROGRESS_EVENTS_PER_GENERATION = 64
_MAX_SAMPLE_SECONDS = 7 * 24 * 60 * 60
_PERFORMANCE_TOKENS = frozenset(
    {
        "batch",
        "count",
        "frame",
        "iteration",
        "pass",
        "sample",
        "step",
        "tile",
    }
)
_CONTENT_TOKENS = frozenset({"caption", "content", "negative_prompt", "prompt", "text"})
_TOTAL_SCOPES = (
    "total_exact",
    "total_revision_resolution",
    "total_revision",
    "total_source",
    "total_instance",
)


@dataclass(frozen=True)
class GenerationTimingFeatures:
    """Versioned hashes of timing-relevant, non-content generation features."""

    exact_key: str
    revision_resolution_key: str
    revision_key: str
    source_key: str
    instance_key: str
    feature_version: int = TIMING_FEATURE_VERSION

    def total_profile_keys(self) -> tuple[tuple[str, str], ...]:
        return (
            (_TOTAL_SCOPES[0], self.exact_key),
            (_TOTAL_SCOPES[1], self.revision_resolution_key),
            (_TOTAL_SCOPES[2], self.revision_key),
            (_TOTAL_SCOPES[3], self.source_key),
            (_TOTAL_SCOPES[4], self.instance_key),
        )

    def persisted_hashes(self) -> dict[str, Any]:
        return {
            "feature_version": self.feature_version,
            **{scope: key for scope, key in self.total_profile_keys()},
        }


@dataclass(frozen=True)
class _ProfileSnapshot:
    median_seconds: float
    lower_seconds: float
    upper_seconds: float
    sample_count: int
    recent_sample_count: int


@dataclass(frozen=True)
class _AuditBatchResult:
    observed: int
    has_more: bool
    profile_updates: dict[tuple[str, str], _ProfileSnapshot]
    removed_profile_keys: frozenset[tuple[str, str]]


def _empty_audit_result() -> _AuditBatchResult:
    return _AuditBatchResult(
        observed=0,
        has_more=False,
        profile_updates={},
        removed_profile_keys=frozenset(),
    )


def build_generation_timing_features(generation: Generation) -> GenerationTimingFeatures:
    """Return only hashes; prompt, seed, owner, asset IDs, and other content are excluded."""

    source_data = _mapping(getattr(generation, "generation_source_json", None))
    instance_id = _bounded_identity(source_data.get("instance_id"), fallback="unknown-instance")
    source_identity = _bounded_identity(
        source_data.get("source_key"),
        fallback=_bounded_identity(
            getattr(generation, "workflow_id", None), fallback="unknown-source"
        ),
    )
    api_revision = _bounded_identity(
        source_data.get("api_sha256") or getattr(generation, "api_graph_sha256", None),
        fallback="unknown-api-revision",
    )

    instance_key = _digest("instance", {"instance": instance_id})
    source_key = _digest(
        "source",
        {"instance": instance_id, "source": source_identity},
    )
    revision_key = _digest(
        "revision",
        {
            "instance": instance_id,
            "source": source_identity,
            "api_revision": api_revision,
        },
    )

    contract = _mapping(getattr(generation, "resolved_contract_json", None))
    effective = _mapping(getattr(generation, "effective_controls_json", None))
    definitions = contract.get("inputs") or contract.get("controls") or []
    if not isinstance(definitions, list):
        definitions = []

    width: int | None = None
    height: int | None = None
    performance_controls: dict[str, Any] = {}
    for raw_definition in definitions:
        if not isinstance(raw_definition, Mapping):
            continue
        control_id = raw_definition.get("id")
        if not isinstance(control_id, str) or not control_id:
            continue
        input_type = str(raw_definition.get("type", ""))
        semantic_role = str(raw_definition.get("semantic_role", ""))
        value = effective.get(control_id)

        if semantic_role == "width":
            width = _positive_integer(value) or width
            continue
        if semantic_role == "height":
            height = _positive_integer(value) or height
            continue
        if input_type == "resolution" and isinstance(value, Mapping):
            width = _positive_integer(value.get("width")) or width
            height = _positive_integer(value.get("height")) or height
            continue
        if input_type == "image" and isinstance(value, Mapping):
            media_width = _positive_integer(value.get("width"))
            media_height = _positive_integer(value.get("height"))
            if media_width and media_height:
                performance_controls[control_id] = {
                    "media_pixel_bucket": _pixel_bucket(media_width, media_height),
                    "media_aspect_bucket": _aspect_bucket(media_width, media_height),
                }
            continue
        if _is_content_or_seed(input_type, semantic_role, control_id):
            continue
        if input_type == "boolean" and isinstance(value, bool):
            performance_controls[control_id] = value
        elif input_type in {"asset_selector", "choice", "enum"} and isinstance(
            value, (str, int, float, bool)
        ):
            performance_controls[control_id] = _bounded_choice(value)
        elif input_type in {"integer", "number"} and _is_performance_numeric(
            semantic_role, control_id
        ):
            numeric = _finite_number(value)
            if numeric is not None:
                performance_controls[control_id] = numeric

    resolution_shape = {
        "pixel_bucket": _pixel_bucket(width, height),
        "aspect_bucket": _aspect_bucket(width, height),
    }
    revision_resolution_key = _digest(
        "revision-resolution",
        {"revision": revision_key, "resolution": resolution_shape},
    )
    exact_key = _digest(
        "exact",
        {
            "revision_resolution": revision_resolution_key,
            "width": width,
            "height": height,
            "controls": performance_controls,
            "preset": _bounded_choice(getattr(generation, "selected_preset", None)),
            "requested_outputs": sorted(
                value[:200]
                for value in (getattr(generation, "requested_outputs_json", None) or [])
                if isinstance(value, str) and value
            ),
        },
    )
    return GenerationTimingFeatures(
        exact_key=exact_key,
        revision_resolution_key=revision_resolution_key,
        revision_key=revision_key,
        source_key=source_key,
        instance_key=instance_key,
    )


def build_progress_landmark_key(
    features: GenerationTimingFeatures,
    progress: Mapping[str, Any] | None,
) -> str | None:
    """Hash an exact-feature/current-node decile; it is not a workflow percentage."""

    if not isinstance(progress, Mapping) or progress.get("kind") != "node":
        return None
    node_id = next(
        (
            str(value).strip()
            for value in (
                progress.get("display_node_id"),
                progress.get("real_node_id"),
                progress.get("node_id"),
            )
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip()
        ),
        None,
    )
    fraction = _progress_fraction(progress)
    if node_id is None or fraction is None:
        return None
    decile = min(10, max(0, math.floor(fraction * 10)))
    return _digest(
        "progress-landmark",
        {
            "exact": features.exact_key,
            "node": node_id[:100],
            "decile": decile,
        },
    )


class GenerationEtaEstimator:
    """In-memory ETA lookup backed by bounded, idle-maintained SQLite profiles."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        audit_interval_seconds: float = _DEFAULT_AUDIT_INTERVAL_SECONDS,
        audit_batch_size: int = _DEFAULT_AUDIT_BATCH_SIZE,
        max_profile_samples: int = _DEFAULT_MAX_PROFILE_SAMPLES,
        max_profiles: int = _DEFAULT_MAX_PROFILES,
        audit_time_budget_seconds: float = _DEFAULT_AUDIT_TIME_BUDGET_SECONDS,
    ) -> None:
        self.session_factory = session_factory
        self.audit_interval_seconds = max(0.05, float(audit_interval_seconds))
        self.audit_batch_size = max(1, int(audit_batch_size))
        self.max_profile_samples = max(8, int(max_profile_samples))
        self.max_profiles = max(8, int(max_profiles))
        self.max_total_profiles = max(5, self.max_profiles * 3 // 4)
        self.max_landmark_profiles = self.max_profiles - self.max_total_profiles
        self.audit_time_budget_seconds = max(0.05, float(audit_time_budget_seconds))
        self._profiles: dict[tuple[str, str], _ProfileSnapshot] = {}
        self._feature_cache: OrderedDict[str, GenerationTimingFeatures] = OrderedDict()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._audit_stop_event = threading.Event()
        self._audit_connection_lock = threading.Lock()
        self._audit_connection: Any | None = None
        self._maintenance_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._maintenance_task is not None:
            if not self._maintenance_task.done():
                return
            self._maintenance_task.result()
        self._audit_stop_event.clear()
        self._profiles = await asyncio.to_thread(self._load_profiles)
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._wake_event.set()
        self._maintenance_task = asyncio.create_task(
            self._maintenance_loop(), name="generation-eta-maintenance"
        )

    async def stop(self) -> None:
        task = self._maintenance_task
        self._maintenance_task = None
        self._stop_event.set()
        self._audit_stop_event.set()
        self._interrupt_audit_connection()
        self.notify()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self._feature_cache.clear()
        self._loop = None

    def notify(self) -> None:
        """Wake maintenance after a terminal commit without doing database work inline."""

        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._wake_event.set)

    def _interrupt_audit_connection(self) -> None:
        with self._audit_connection_lock:
            connection = self._audit_connection
        interrupt = getattr(connection, "interrupt", None)
        if callable(interrupt):
            with suppress(Exception):
                interrupt()

    def estimate(
        self,
        generation: Generation,
        progress: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        status = getattr(generation, "status", None)
        normalized_status = status.value if isinstance(status, GenerationStatus) else str(status)
        if normalized_status != GenerationStatus.RUNNING.value:
            return None
        started_at = getattr(generation, "started_at", None)
        if not isinstance(started_at, datetime):
            return None
        current = _aware_utc(now or datetime.now(UTC))
        elapsed = max(0.0, (current - _aware_utc(started_at)).total_seconds())
        features = self._active_features(generation)
        active_progress = progress
        if active_progress is None:
            saved_progress = getattr(generation, "progress_json", None)
            active_progress = saved_progress if isinstance(saved_progress, Mapping) else None

        landmark_key = build_progress_landmark_key(features, active_progress)
        profile: _ProfileSnapshot | None = None
        basis = ""
        subtract_elapsed = True
        if landmark_key is not None:
            profile = self._profiles.get(("progress_landmark", landmark_key))
            if profile is not None:
                basis = "progress_landmark"
                subtract_elapsed = False
        if profile is None:
            for scope, scope_key in features.total_profile_keys():
                profile = self._profiles.get((scope, scope_key))
                if profile is not None:
                    basis = {
                        "total_exact": "historical_exact",
                        "total_revision_resolution": "historical_revision_resolution",
                        "total_revision": "historical_revision",
                        "total_source": "historical_source",
                        "total_instance": "historical_instance",
                    }[scope]
                    break
        if profile is None:
            return None

        offset = elapsed if subtract_elapsed else 0.0
        remaining = max(0.0, profile.median_seconds - offset)
        lower = max(0.0, min(remaining, profile.lower_seconds - offset))
        upper = max(remaining, profile.upper_seconds - offset, 0.0)
        confidence = _cap_confidence(_profile_confidence(profile), basis)
        if subtract_elapsed and remaining <= 0:
            # A still-running job has survived beyond the historical median. Move to the
            # compatible upper tail instead of presenting a misleading zero-second ETA.
            upper_tail = max(0.0, profile.upper_seconds - elapsed)
            fallback_tail = min(30.0, max(1.0, profile.median_seconds * 0.1))
            remaining = max(upper_tail, fallback_tail)
            lower = 0.0
            upper = max(remaining, upper_tail, remaining * 1.5)
            if elapsed >= profile.upper_seconds:
                confidence = "low"
            elif confidence == "high":
                confidence = "medium"
        confidence = _cap_confidence(confidence, basis)
        remaining = round(remaining, 1)
        lower = round(lower, 1)
        upper = round(upper, 1)
        completion_at = current + timedelta(seconds=remaining)
        return {
            "remaining_seconds": remaining,
            "completion_at": completion_at.isoformat(),
            "lower_seconds": lower,
            "upper_seconds": upper,
            "confidence": confidence,
            "basis": basis,
            "updated_at": current.isoformat(),
        }

    def _active_features(self, generation: Generation) -> GenerationTimingFeatures:
        generation_id = str(getattr(generation, "id", ""))
        cached = self._feature_cache.get(generation_id)
        if cached is not None:
            self._feature_cache.move_to_end(generation_id)
            return cached
        features = build_generation_timing_features(generation)
        self._feature_cache[generation_id] = features
        if len(self._feature_cache) > _MAX_ACTIVE_FEATURE_CACHE:
            self._feature_cache.popitem(last=False)
        return features

    async def _maintenance_loop(self) -> None:
        while not self._stop_event.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake_event.wait(), timeout=self.audit_interval_seconds)
            self._wake_event.clear()
            if self._stop_event.is_set():
                return
            try:
                result = await self._run_audit_batch()
                self._apply_audit_result(result)
                if result.has_more:
                    await asyncio.sleep(0.05)
                    self._wake_event.set()
            except asyncio.CancelledError:
                self._audit_stop_event.set()
                self._interrupt_audit_connection()
                raise
            except Exception:
                logger.exception("generation_eta_maintenance_failed")

    async def _run_audit_batch(self) -> _AuditBatchResult:
        audit_task = asyncio.create_task(asyncio.to_thread(self._audit_batch))
        try:
            return await asyncio.shield(audit_task)
        except asyncio.CancelledError:
            self._audit_stop_event.set()
            self._interrupt_audit_connection()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(audit_task, return_exceptions=True)
            raise

    def _apply_audit_result(self, result: _AuditBatchResult) -> None:
        if not result.profile_updates and not result.removed_profile_keys:
            return
        profiles = dict(self._profiles)
        for key in result.removed_profile_keys:
            profiles.pop(key, None)
        profiles.update(result.profile_updates)
        if len(profiles) > self.max_profiles:
            logger.error(
                "generation_eta_cache_bound_exceeded",
                extra={"profiles": len(profiles), "limit": self.max_profiles},
            )
            profiles = self._load_profiles()
        self._profiles = profiles

    def _load_profiles(self) -> dict[tuple[str, str], _ProfileSnapshot]:
        with self.session_factory() as session:
            self._prune_profiles(session)
            rows = list(
                session.scalars(
                    select(GenerationTimingProfile)
                    .where(GenerationTimingProfile.feature_version == TIMING_FEATURE_VERSION)
                    .order_by(
                        GenerationTimingProfile.updated_at.desc(),
                        GenerationTimingProfile.sample_count.desc(),
                        GenerationTimingProfile.id,
                    )
                    .limit(self.max_profiles)
                )
            )
            session.commit()
            return {
                (profile.scope, profile.scope_key): _profile_snapshot(profile) for profile in rows
            }

    def _audit_batch(self) -> _AuditBatchResult:
        if self._audit_stop_event.is_set():
            return _empty_audit_result()
        with self.session_factory() as session:
            connection, prior_busy_timeout = self._configure_audit_connection(session)
            try:
                return self._audit_batch_in_session(session)
            except Exception:
                session.rollback()
                if self._audit_stop_event.is_set():
                    return _empty_audit_result()
                raise
            finally:
                self._restore_audit_connection(connection, prior_busy_timeout)

    def _audit_batch_in_session(self, session: Session) -> _AuditBatchResult:
        if self._has_active_generation(session):
            return _empty_audit_result()
        state = self._audit_state(session)
        statement = (
            select(Generation)
            .options(
                load_only(
                    Generation.id,
                    Generation.status,
                    Generation.started_at,
                    Generation.completed_at,
                    Generation.generation_source_json,
                    Generation.workflow_id,
                    Generation.api_graph_sha256,
                    Generation.resolved_contract_json,
                    Generation.effective_controls_json,
                    Generation.selected_preset,
                    Generation.requested_outputs_json,
                )
            )
            .where(
                Generation.status == GenerationStatus.SUCCEEDED,
                Generation.completed_at.is_not(None),
            )
        )
        if state.cursor_completed_at is not None and state.cursor_generation_id is not None:
            statement = statement.where(
                or_(
                    Generation.completed_at > state.cursor_completed_at,
                    and_(
                        Generation.completed_at == state.cursor_completed_at,
                        Generation.id > state.cursor_generation_id,
                    ),
                )
            )
        candidates = list(
            session.scalars(
                statement.order_by(Generation.completed_at, Generation.id).limit(
                    self.audit_batch_size
                )
            )
        )
        if not candidates:
            if not state.backfill_complete:
                state.backfill_complete = True
                state.updated_at = datetime.now(UTC)
                if self._audit_stop_event.is_set() or self._has_active_generation(session):
                    session.rollback()
                else:
                    session.commit()
            return _empty_audit_result()

        events_by_generation = self._load_progress_events(
            session, [generation.id for generation in candidates]
        )
        deadline = time.monotonic() + self.audit_time_budget_seconds
        touched_profiles: dict[str, GenerationTimingProfile] = {}
        processed = 0
        for generation in candidates:
            if self._audit_stop_event.is_set():
                session.rollback()
                return _empty_audit_result()
            if processed and time.monotonic() >= deadline:
                break
            started_at = generation.started_at
            completed_at = generation.completed_at
            if not isinstance(completed_at, datetime):
                continue
            observed_at = _aware_utc(completed_at)
            if isinstance(started_at, datetime):
                duration = (observed_at - _aware_utc(started_at)).total_seconds()
            else:
                duration = 0.0
            if math.isfinite(duration) and 0 < duration <= _MAX_SAMPLE_SECONDS:
                features = build_generation_timing_features(generation)
                for scope, scope_key in features.total_profile_keys():
                    profile = self._add_profile_sample(
                        session,
                        scope,
                        scope_key,
                        duration,
                        observed_at=observed_at,
                    )
                    touched_profiles[profile.id] = profile
                    if self._audit_stop_event.is_set():
                        session.rollback()
                        return _empty_audit_result()

                landmark_values: dict[str, float] = {}
                for payload, created_at in events_by_generation.get(generation.id, []):
                    raw_progress = payload.get("progress")
                    progress = raw_progress if isinstance(raw_progress, Mapping) else None
                    landmark_key = build_progress_landmark_key(features, progress)
                    if landmark_key is None:
                        continue
                    residual = (observed_at - _aware_utc(created_at)).total_seconds()
                    if 0 <= residual <= _MAX_SAMPLE_SECONDS and math.isfinite(residual):
                        prior = landmark_values.get(landmark_key)
                        if prior is None or residual > prior:
                            landmark_values[landmark_key] = residual
                for landmark_key, residual in sorted(landmark_values.items()):
                    profile = self._add_profile_sample(
                        session,
                        "progress_landmark",
                        landmark_key,
                        residual,
                        observed_at=observed_at,
                    )
                    touched_profiles[profile.id] = profile
                    if self._audit_stop_event.is_set():
                        session.rollback()
                        return _empty_audit_result()

            state.cursor_completed_at = observed_at
            state.cursor_generation_id = generation.id
            state.backfill_complete = False
            state.updated_at = datetime.now(UTC)
            processed += 1

        has_more = processed < len(candidates) or len(candidates) >= self.audit_batch_size
        state.backfill_complete = not has_more
        if self._audit_stop_event.is_set() or self._has_active_generation(session):
            session.rollback()
            return _empty_audit_result()
        removed_keys, removed_ids = self._prune_profiles(session)
        session.flush()
        profile_updates = {
            (profile.scope, profile.scope_key): _profile_snapshot(profile)
            for profile_id, profile in touched_profiles.items()
            if profile_id not in removed_ids
        }
        session.commit()
        return _AuditBatchResult(
            observed=processed,
            has_more=has_more,
            profile_updates=profile_updates,
            removed_profile_keys=frozenset(removed_keys),
        )

    @staticmethod
    def _has_active_generation(session: Session) -> bool:
        return (
            session.scalar(
                select(Generation.id).where(Generation.status.in_(ACTIVE_STATUSES)).limit(1)
            )
            is not None
        )

    @staticmethod
    def _load_progress_events(
        session: Session,
        generation_ids: list[str],
    ) -> dict[str, list[tuple[Mapping[str, Any], datetime]]]:
        if not generation_ids:
            return {}
        ranked_events = (
            select(
                GenerationEvent.id.label("event_id"),
                GenerationEvent.generation_id,
                GenerationEvent.payload_json,
                GenerationEvent.created_at,
                func.row_number()
                .over(
                    partition_by=GenerationEvent.generation_id,
                    order_by=GenerationEvent.id,
                )
                .label("generation_head_rank"),
                func.row_number()
                .over(
                    partition_by=GenerationEvent.generation_id,
                    order_by=GenerationEvent.id.desc(),
                )
                .label("generation_tail_rank"),
            )
            .where(
                GenerationEvent.generation_id.in_(generation_ids),
                GenerationEvent.event_type == "generation.progress",
            )
            .subquery()
        )
        event_rows = session.execute(
            select(
                ranked_events.c.generation_id,
                ranked_events.c.payload_json,
                ranked_events.c.created_at,
            )
            .where(
                or_(
                    ranked_events.c.generation_head_rank
                    <= _MAX_AUDIT_PROGRESS_EVENTS_PER_GENERATION // 2,
                    ranked_events.c.generation_tail_rank
                    <= _MAX_AUDIT_PROGRESS_EVENTS_PER_GENERATION // 2,
                )
            )
            .order_by(ranked_events.c.generation_id, ranked_events.c.event_id)
        ).all()
        events_by_generation: dict[str, list[tuple[Mapping[str, Any], datetime]]] = {}
        for generation_id, payload, created_at in event_rows:
            if isinstance(payload, Mapping) and isinstance(created_at, datetime):
                events_by_generation.setdefault(str(generation_id), []).append(
                    (payload, created_at)
                )
        return events_by_generation

    def _audit_state(self, session: Session) -> GenerationTimingAuditState:
        state = session.get(GenerationTimingAuditState, _AUDIT_STATE_KEY)
        now = datetime.now(UTC)
        if state is None:
            state = GenerationTimingAuditState(
                key=_AUDIT_STATE_KEY,
                feature_version=TIMING_FEATURE_VERSION,
                cursor_completed_at=None,
                cursor_generation_id=None,
                backfill_complete=False,
                updated_at=now,
            )
            session.add(state)
        elif state.feature_version != TIMING_FEATURE_VERSION:
            state.feature_version = TIMING_FEATURE_VERSION
            state.cursor_completed_at = None
            state.cursor_generation_id = None
            state.backfill_complete = False
            state.updated_at = now
        return state

    def _configure_audit_connection(self, session: Session) -> tuple[Any, int]:
        connection = session.connection().connection.driver_connection
        if connection is None:
            raise RuntimeError("ETA audit requires an active DBAPI connection")
        cursor = connection.cursor()
        try:
            row = cursor.execute("PRAGMA busy_timeout").fetchone()
            prior_busy_timeout = int(row[0]) if row else 15_000
            cursor.execute(f"PRAGMA busy_timeout={_AUDIT_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()
        set_progress_handler = getattr(connection, "set_progress_handler", None)
        if callable(set_progress_handler):
            set_progress_handler(lambda: int(self._audit_stop_event.is_set()), 1_000)
        with self._audit_connection_lock:
            self._audit_connection = connection
        return connection, prior_busy_timeout

    def _restore_audit_connection(self, connection: Any, prior_busy_timeout: int) -> None:
        with self._audit_connection_lock:
            if self._audit_connection is connection:
                self._audit_connection = None
        set_progress_handler = getattr(connection, "set_progress_handler", None)
        if callable(set_progress_handler):
            with suppress(Exception):
                set_progress_handler(None, 0)
        with suppress(Exception):
            cursor = connection.cursor()
            try:
                cursor.execute(f"PRAGMA busy_timeout={prior_busy_timeout}")
            finally:
                cursor.close()

    def _prune_profiles(
        self,
        session: Session,
    ) -> tuple[set[tuple[str, str]], set[str]]:
        removed_keys: set[tuple[str, str]] = set()
        removed_ids: set[str] = set()
        stale = session.execute(
            select(
                GenerationTimingProfile.id,
                GenerationTimingProfile.scope,
                GenerationTimingProfile.scope_key,
            ).where(GenerationTimingProfile.feature_version != TIMING_FEATURE_VERSION)
        ).all()
        self._delete_profile_rows(session, stale, removed_keys, removed_ids)

        total_victims = session.execute(
            select(
                GenerationTimingProfile.id,
                GenerationTimingProfile.scope,
                GenerationTimingProfile.scope_key,
            )
            .where(
                GenerationTimingProfile.feature_version == TIMING_FEATURE_VERSION,
                GenerationTimingProfile.scope != "progress_landmark",
            )
            .order_by(
                GenerationTimingProfile.updated_at.desc(),
                GenerationTimingProfile.sample_count.desc(),
                GenerationTimingProfile.id,
            )
            .offset(self.max_total_profiles)
        ).all()
        self._delete_profile_rows(session, total_victims, removed_keys, removed_ids)

        landmark_victims = session.execute(
            select(
                GenerationTimingProfile.id,
                GenerationTimingProfile.scope,
                GenerationTimingProfile.scope_key,
            )
            .where(
                GenerationTimingProfile.feature_version == TIMING_FEATURE_VERSION,
                GenerationTimingProfile.scope == "progress_landmark",
            )
            .order_by(
                GenerationTimingProfile.updated_at.desc(),
                GenerationTimingProfile.sample_count.desc(),
                GenerationTimingProfile.id,
            )
            .offset(self.max_landmark_profiles)
        ).all()
        self._delete_profile_rows(session, landmark_victims, removed_keys, removed_ids)
        return removed_keys, removed_ids

    @staticmethod
    def _delete_profile_rows(
        session: Session,
        rows: Sequence[Any],
        removed_keys: set[tuple[str, str]],
        removed_ids: set[str],
    ) -> None:
        if not rows:
            return
        row_ids = [str(row.id) for row in rows]
        removed_ids.update(row_ids)
        removed_keys.update((str(row.scope), str(row.scope_key)) for row in rows)
        session.execute(
            delete(GenerationTimingProfile)
            .where(GenerationTimingProfile.id.in_(row_ids))
            .execution_options(synchronize_session=False)
        )

    def _add_profile_sample(
        self,
        session: Session,
        scope: str,
        scope_key: str,
        sample_seconds: float,
        *,
        observed_at: datetime | None = None,
    ) -> GenerationTimingProfile:
        profile_id = _digest("profile", {"scope": scope, "key": scope_key})
        profile = session.get(GenerationTimingProfile, profile_id)
        if profile is None:
            profile = GenerationTimingProfile(
                id=profile_id,
                feature_version=TIMING_FEATURE_VERSION,
                scope=scope,
                scope_key=scope_key,
                sample_count=0,
                samples_json=[],
                median_seconds=sample_seconds,
                lower_seconds=sample_seconds,
                upper_seconds=sample_seconds,
            )
            session.add(profile)
            session.flush()
        samples = [*_valid_samples(profile.samples_json), float(sample_seconds)]
        samples = samples[-self.max_profile_samples :]
        median, lower, upper = _robust_stats(samples)
        profile.samples_json = samples
        profile.sample_count = int(profile.sample_count) + 1
        profile.median_seconds = median
        profile.lower_seconds = lower
        profile.upper_seconds = upper
        profile.updated_at = _aware_utc(observed_at or datetime.now(UTC))
        return profile


def _digest(namespace: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(
        f"generation-eta/v{TIMING_FEATURE_VERSION}/{namespace}:".encode() + encoded
    ).hexdigest()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bounded_identity(value: Any, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    return normalized[:512] if normalized else fallback


def _bounded_choice(value: Any) -> str | int | float | bool | None:
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _positive_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _pixel_bucket(width: int | None, height: int | None) -> int | None:
    if width is None or height is None:
        return None
    return round(math.log2(width * height) * 2)


def _aspect_bucket(width: int | None, height: int | None) -> int | None:
    if width is None or height is None:
        return None
    return round(math.log2(width / height) * 2)


def _tokenized(value: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", value.casefold()))
    plural_aliases = {
        "batches": "batch",
        "counts": "count",
        "frames": "frame",
        "iterations": "iteration",
        "passes": "pass",
        "samples": "sample",
        "steps": "step",
        "tiles": "tile",
    }
    return tokens | {plural_aliases[token] for token in tokens if token in plural_aliases}


def _is_content_or_seed(input_type: str, semantic_role: str, control_id: str) -> bool:
    tokens = _tokenized(semantic_role) | _tokenized(control_id)
    return input_type in {"string", "seed"} or "seed" in tokens or bool(tokens & _CONTENT_TOKENS)


def _is_performance_numeric(semantic_role: str, control_id: str) -> bool:
    tokens = _tokenized(semantic_role) | _tokenized(control_id)
    return bool(tokens & _PERFORMANCE_TOKENS)


def _progress_fraction(progress: Mapping[str, Any]) -> float | None:
    fraction = _finite_number(progress.get("fraction"))
    if fraction is None:
        value = _finite_number(progress.get("value"))
        maximum = _finite_number(progress.get("maximum"))
        if value is None or maximum is None or maximum <= 0:
            return None
        fraction = value / maximum
    if not 0 <= fraction <= 1:
        return None
    return float(fraction)


def _valid_samples(raw_samples: Any) -> list[float]:
    if not isinstance(raw_samples, list):
        return []
    return [
        float(value)
        for value in raw_samples
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= _MAX_SAMPLE_SECONDS
    ]


def _robust_stats(samples: list[float]) -> tuple[float, float, float]:
    clean = _valid_samples(samples)
    if not clean:
        raise ValueError("at least one finite timing sample is required")
    median = float(statistics.median(clean))
    deviations = [abs(value - median) for value in clean]
    mad = float(statistics.median(deviations))
    if len(clean) >= 4:
        tolerance = max(median * 0.05, mad * 4.5, 0.25)
        filtered = [value for value in clean if abs(value - median) <= tolerance]
        if len(filtered) >= max(3, len(clean) // 2):
            clean = filtered
            median = float(statistics.median(clean))
    if len(clean) == 1:
        lower = max(0.0, median * 0.75)
        upper = median * 1.25
    else:
        ordered = sorted(clean)
        lower = _quantile(ordered, 0.2)
        upper = _quantile(ordered, 0.8)
        minimum_spread = max(1.0, median * 0.1)
        lower = min(lower, max(0.0, median - minimum_spread))
        upper = max(upper, median + minimum_spread)
    return median, max(0.0, lower), max(median, upper)


def _quantile(ordered: list[float], fraction: float) -> float:
    position = (len(ordered) - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    weight = position - lower_index
    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


def _profile_confidence(profile: _ProfileSnapshot) -> str:
    effective_count = min(profile.sample_count, profile.recent_sample_count)
    if effective_count >= 20:
        return "high"
    if effective_count >= 5:
        return "medium"
    return "low"


def _profile_snapshot(profile: GenerationTimingProfile) -> _ProfileSnapshot:
    return _ProfileSnapshot(
        median_seconds=float(profile.median_seconds),
        lower_seconds=float(profile.lower_seconds),
        upper_seconds=float(profile.upper_seconds),
        sample_count=int(profile.sample_count),
        recent_sample_count=len(_valid_samples(profile.samples_json)),
    )


def _cap_confidence(confidence: str, basis: str) -> str:
    levels = ("low", "medium", "high")
    maximum = {
        "progress_landmark": "high",
        "historical_exact": "high",
        "historical_revision_resolution": "medium",
        "historical_revision": "low",
        "historical_source": "low",
        "historical_instance": "low",
    }.get(basis, "low")
    try:
        return levels[min(levels.index(confidence), levels.index(maximum))]
    except ValueError:
        return "low"


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
