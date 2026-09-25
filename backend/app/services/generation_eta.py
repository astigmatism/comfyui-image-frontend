"""Execution-only timing evidence. Queue and delivery time never train this model."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import statistics
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeGuard

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ..blocking import run_blocking
from ..models import (
    Generation,
    GenerationStatus,
    GenerationTimingAuditState,
    GenerationTimingProfile,
)

logger = logging.getLogger(__name__)
TIMING_FEATURE_VERSION = 3
_MAX_SAMPLE_SECONDS = 7 * 24 * 60 * 60


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        return _aware_utc(datetime.fromisoformat(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def is_checkpoint_declaration(declaration: Mapping[str, Any] | None) -> bool:
    return bool(
        isinstance(declaration, Mapping)
        and declaration.get("type") == "choice"
        and (
            declaration.get("semantic_role") in {"model", "checkpoint"}
            or declaration.get("id") == "checkpoint"
        )
    )


@dataclass(frozen=True)
class GenerationTimingFeatures:
    compute_key: str
    width: int | None
    height: int | None
    prompt_length: int

    @property
    def exact_key(self) -> str:
        return _digest(asdict(self))

    def comparable(self, other: GenerationTimingFeatures) -> bool:
        if self.compute_key != other.compute_key:
            return False
        if (
            max(self.prompt_length + 32, other.prompt_length + 32)
            / min(self.prompt_length + 32, other.prompt_length + 32)
            > 1.5
        ):
            return False
        if None in (self.width, self.height, other.width, other.height):
            return (self.width, self.height) == (other.width, other.height)
        assert self.width and self.height and other.width and other.height
        pixels = self.width * self.height / (other.width * other.height)
        aspect = (self.width / self.height) / (other.width / other.height)
        return 0.8 <= pixels <= 1.25 and 0.8 <= aspect <= 1.25


def build_generation_timing_features(generation: Generation) -> GenerationTimingFeatures:
    contract = generation.resolved_contract_json or {}
    effective = generation.effective_controls_json or {}
    source = generation.generation_source_json or {}
    controls: dict[str, Any] = {}
    width = height = None
    for definition in contract.get("inputs", contract.get("controls", [])):
        key = definition.get("id")
        value = effective.get(key)
        kind, role = definition.get("type", ""), definition.get("semantic_role", "")
        if role in {"width", "height"}:
            if role == "width":
                width = value
            else:
                height = value
        elif kind == "resolution" and isinstance(value, Mapping):
            width, height = value.get("width"), value.get("height")
        elif kind == "image":
            controls[key] = (
                {k: value.get(k) for k in ("width", "height")}
                if isinstance(value, Mapping)
                else None
            )
        elif (
            kind not in {"string", "multiline_string", "seed"}
            and "seed" not in str(role)
            and "prompt" not in str(role)
        ):
            controls[key] = value
    width = width if isinstance(width, int) and not isinstance(width, bool) and width > 0 else None
    height = (
        height if isinstance(height, int) and not isinstance(height, bool) and height > 0 else None
    )
    return GenerationTimingFeatures(
        compute_key=_digest(
            {
                "version": TIMING_FEATURE_VERSION,
                "instance": generation.comfyui_instance_id,
                "source": source.get("source_key", generation.workflow_id),
                "revision": [
                    generation.api_graph_sha256,
                    generation.contract_sha256,
                    source.get("manifest_sha256"),
                ],
                "controls": controls,
                "preset": generation.selected_preset,
                "outputs": sorted(generation.requested_outputs_json or []),
            }
        ),
        width=width,
        height=height,
        prompt_length=len(generation.final_prompt or ""),
    )


def execution_start(generation: Generation) -> datetime | None:
    timing = generation.execution_timing_json or {}
    if (
        timing.get("version") != TIMING_FEATURE_VERSION
        or timing.get("prompt_id") != generation.comfyui_prompt_id
    ):
        return None
    # Anchor the active countdown to this server's event receipt, not another host's clock.
    return _parse_timestamp(timing.get("observed_started_at") or timing.get("started_at"))


def verified_duration(generation: Generation) -> float | None:
    timing = generation.execution_timing_json or {}
    seconds = timing.get("duration_seconds")
    if (
        timing.get("version") == TIMING_FEATURE_VERSION
        and timing.get("prompt_id") == generation.comfyui_prompt_id
        and timing.get("provenance") in {"native", "monotonic"}
        and _number(seconds)
        and 0 < seconds <= _MAX_SAMPLE_SECONDS
    ):
        return float(seconds)
    return None


def native_execution_timing(
    history: Mapping[str, Any], prompt_id: str | None
) -> dict[str, Any] | None:
    """Pair native millisecond timestamps for this prompt, never application timestamps."""
    status = history.get("status", {})
    if not isinstance(status, Mapping) or status.get("status_str") != "success" or not prompt_id:
        return None
    starts: list[float] = []
    finishes: list[float] = []
    cached: set[str] = set()
    messages = status.get("messages", [])
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, (list, tuple)) or len(message) != 2:
            continue
        kind, data = message
        if not isinstance(data, Mapping) or data.get("prompt_id") != prompt_id:
            continue
        if kind == "execution_cached" and isinstance(data.get("nodes"), list):
            cached.update(
                str(node) for node in data.get("nodes", []) if isinstance(node, (str, int))
            )
        stamp = data.get("timestamp")
        if (
            not _number(stamp)
            or not 1_000_000_000_000 <= stamp <= datetime.now(UTC).timestamp() * 1000 + 300_000
        ):
            continue
        if kind == "execution_start":
            starts.append(float(stamp))
        elif kind == "execution_success":
            finishes.append(float(stamp))
    # Identical duplicate delivery is harmless; ambiguous attempts are not evidence.
    if len(set(starts)) != 1 or len(set(finishes)) != 1:
        return None
    prompt = history.get("prompt", [])
    graph = (
        prompt[2]
        if isinstance(prompt, list) and len(prompt) > 2 and isinstance(prompt[2], Mapping)
        else {}
    )
    if graph and set(graph).issubset(cached):
        return None
    seconds = (finishes[0] - starts[0]) / 1000
    if not 0 < seconds <= _MAX_SAMPLE_SECONDS:
        return None
    return {
        "version": TIMING_FEATURE_VERSION,
        "prompt_id": prompt_id,
        "provenance": "native",
        "started_at": datetime.fromtimestamp(starts[0] / 1000, UTC).isoformat(),
        "finished_at": datetime.fromtimestamp(finishes[0] / 1000, UTC).isoformat(),
        "duration_seconds": seconds,
    }


@dataclass(frozen=True)
class DurationEstimate:
    seconds: float
    lower: float
    upper: float
    basis: str
    sample_count: int
    confidence: str


def reliable_statistics(samples: Sequence[float], basis: str) -> DurationEstimate | None:
    clean = [float(v) for v in samples if _number(v) and 0 < v <= _MAX_SAMPLE_SECONDS]
    if not clean:
        return None
    median = statistics.median(clean)
    mad = statistics.median(abs(v - median) for v in clean)
    if len(clean) >= 3:
        tolerance = max(3 * mad, median * 0.25, 2)
        retained = [v for v in clean if abs(v - median) <= tolerance]
        if len(retained) <= len(clean) // 2:
            return None
        clean = retained
    if max(clean) / min(clean) > 2:
        return None
    median = statistics.median(clean)
    spread = statistics.median(abs(v - median) for v in clean) / median
    confidence = (
        "high"
        if len(clean) >= 5 and spread <= 0.1
        else "medium"
        if len(clean) >= 2 and spread <= 0.25
        else "low"
    )
    if basis == "historical_nearby" and confidence == "high":
        confidence = "medium"
    return DurationEstimate(
        median,
        min(min(clean), median * 0.75),
        max(max(clean), median * 1.25),
        basis,
        len(clean),
        confidence,
    )


def eta_payload(duration: DurationEstimate, origin: datetime, now: datetime) -> dict[str, Any]:
    completion = origin + timedelta(seconds=duration.seconds)
    remaining = max(0, (completion - now).total_seconds())
    return {
        "remaining_seconds": round(remaining, 3),
        "completion_at": completion.isoformat(),
        "lower_seconds": round(
            max(0, (origin + timedelta(seconds=duration.lower) - now).total_seconds()), 3
        ),
        "upper_seconds": round(
            max(remaining, (origin + timedelta(seconds=duration.upper) - now).total_seconds()), 3
        ),
        "confidence": duration.confidence if remaining else "low",
        "basis": duration.basis,
        "sample_count": duration.sample_count,
        "model_version": TIMING_FEATURE_VERSION,
        "updated_at": now.isoformat(),
    }


class GenerationEtaEstimator:
    """Bounded cache of durable, verified samples grouped by compute identity."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        max_profile_samples: int = 64,
        max_profiles: int = 4096,
        audit_interval_seconds: float = 300,
        audit_batch_size: int = 24,
        audit_time_budget_seconds: float = 2,
    ) -> None:
        self.session_factory = session_factory
        self.max_profile_samples = max(5, max_profile_samples)
        self.max_profiles = max(1, max_profiles)
        self.audit_interval_seconds = audit_interval_seconds
        self.audit_batch_size = audit_batch_size
        self._profiles: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._lock = threading.RLock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.revision = 0

    async def start(self) -> None:
        await run_blocking(self._load_profiles)
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(
            self._maintenance_loop(), name="generation-eta-maintenance"
        )
        self._wake.set()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._loop = None

    def notify(self) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)

    def _load_profiles(self) -> None:
        with self._lock, self.session_factory() as session:
            session.execute(
                delete(GenerationTimingProfile).where(
                    GenerationTimingProfile.feature_version != TIMING_FEATURE_VERSION
                )
            )
            rows = session.scalars(
                select(GenerationTimingProfile)
                .where(
                    GenerationTimingProfile.feature_version == TIMING_FEATURE_VERSION,
                    GenerationTimingProfile.scope == "verified",
                )
                .order_by(GenerationTimingProfile.updated_at.desc())
                .limit(self.max_profiles)
            ).all()
            with self._lock:
                self._profiles = OrderedDict((r.scope_key, r.samples_json) for r in reversed(rows))
                self.revision += 1
            session.commit()

    def record_success(self, session: Session, generation: Generation) -> None:
        seconds = verified_duration(generation)
        if generation.status != GenerationStatus.SUCCEEDED or seconds is None:
            return
        assert generation.execution_timing_json is not None
        features = build_generation_timing_features(generation)
        key = features.compute_key
        profile_id = _digest([TIMING_FEATURE_VERSION, key])
        row = session.get(GenerationTimingProfile, profile_id)
        samples = list(row.samples_json) if row else []
        if any(s.get("id") == generation.id for s in samples):
            return
        sample = {
            "id": generation.id,
            "batch": generation.timing_batch_id,
            "features": asdict(features),
            "seconds": seconds,
            "finished_at": generation.execution_timing_json["finished_at"],
        }
        samples = sorted([*samples, sample], key=lambda s: (s["finished_at"], s["id"]))[
            -self.max_profile_samples :
        ]
        stats = reliable_statistics([s["seconds"] for s in samples], "historical_exact")
        if row is None:
            row = GenerationTimingProfile(
                id=profile_id,
                feature_version=TIMING_FEATURE_VERSION,
                scope="verified",
                scope_key=key,
                sample_count=0,
                median_seconds=0,
                lower_seconds=0,
                upper_seconds=0,
            )
            session.add(row)
        row.samples_json = samples
        row.sample_count = len(samples)
        row.median_seconds = stats.seconds if stats else 0
        row.lower_seconds = stats.lower if stats else 0
        row.upper_seconds = stats.upper if stats else 0
        row.updated_at = max(
            _parse_timestamp(s["finished_at"]) or datetime.now(UTC) for s in samples
        )
        session.flush()
        old_ids = list(
            session.scalars(
                select(GenerationTimingProfile.id)
                .order_by(GenerationTimingProfile.updated_at.desc(), GenerationTimingProfile.id)
                .offset(self.max_profiles)
            )
        )
        if old_ids:
            session.execute(
                delete(GenerationTimingProfile).where(GenerationTimingProfile.id.in_(old_ids))
            )
        # Cache publication is performed after commit by the caller.
        logger.info(
            "generation_timing_sample",
            extra={
                "duration_seconds": seconds,
                "provenance": generation.execution_timing_json["provenance"],
                "model_version": TIMING_FEATURE_VERSION,
            },
        )

    def refresh(self, generation_id: str | None = None) -> None:
        if generation_id is None:
            self._load_profiles()
            return
        with self._lock, self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            key = build_generation_timing_features(generation).compute_key
            row = session.get(GenerationTimingProfile, _digest([TIMING_FEATURE_VERSION, key]))
            if row:
                self._profiles[key] = row.samples_json
                self._profiles.move_to_end(key)
                while len(self._profiles) > self.max_profiles:
                    self._profiles.popitem(last=False)
                self.revision += 1

    def duration(self, generation: Generation) -> DurationEstimate | None:
        features = build_generation_timing_features(generation)
        with self._lock:
            samples = list(self._profiles.get(features.compute_key, []))
        compatible = [
            s
            for s in samples
            if s["id"] != generation.id
            and features.comparable(GenerationTimingFeatures(**s["features"]))
        ]
        batch = [
            s["seconds"]
            for s in compatible
            if generation.timing_batch_id and s["batch"] == generation.timing_batch_id
        ][-5:]
        exact = [s["seconds"] for s in compatible if s["features"] == asdict(features)][-20:]
        nearby = [s["seconds"] for s in compatible][-20:]
        for basis, values in (
            ("batch", batch),
            ("historical_exact", exact),
            ("historical_nearby", nearby),
        ):
            if basis == "historical_nearby" and len(values) < 3:
                continue
            estimate = reliable_statistics(values, basis)
            if estimate and (basis != "historical_nearby" or estimate.sample_count >= 3):
                return estimate
        return None

    def estimate(
        self,
        generation: Generation,
        progress: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        start = execution_start(generation)
        if generation.status != GenerationStatus.RUNNING or start is None:
            return None
        duration = self.duration(generation)
        return (
            eta_payload(duration, start, _aware_utc(now or datetime.now(UTC))) if duration else None
        )

    async def _maintenance_loop(self) -> None:
        while True:
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.audit_interval_seconds)
            self._wake.clear()
            try:
                more = await run_blocking(self._audit_batch)
                if more:
                    await asyncio.sleep(0.05)
                    self._wake.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("generation_eta_maintenance_failed")

    def _audit_batch(self) -> bool:
        """Resume bounded historical verification. Never infer execution from wall duration."""
        with self.session_factory() as session:
            state = session.get(GenerationTimingAuditState, "generation_eta")
            if state is None:
                state = GenerationTimingAuditState(
                    key="generation_eta", feature_version=TIMING_FEATURE_VERSION
                )
                session.add(state)
            if state.feature_version != TIMING_FEATURE_VERSION:
                state.feature_version = TIMING_FEATURE_VERSION
                state.cursor_completed_at = None
                state.cursor_generation_id = None
            query = select(Generation).where(
                Generation.status == GenerationStatus.SUCCEEDED,
                Generation.completed_at.is_not(None),
            )
            if state.cursor_completed_at:
                query = query.where(
                    (Generation.completed_at > state.cursor_completed_at)
                    | (
                        (Generation.completed_at == state.cursor_completed_at)
                        & (Generation.id > state.cursor_generation_id)
                    )
                )
            rows = session.scalars(
                query.order_by(Generation.completed_at, Generation.id).limit(self.audit_batch_size)
            ).all()
            for generation in rows:
                if verified_duration(generation) is None:
                    timing = native_execution_timing(
                        generation.raw_history_json or {}, generation.comfyui_prompt_id
                    )
                    if timing:
                        generation.execution_timing_json = timing
                self.record_success(session, generation)
                state.cursor_completed_at = generation.completed_at
                state.cursor_generation_id = generation.id
            state.backfill_complete = len(rows) < self.audit_batch_size
            session.commit()
        self.refresh()
        return len(rows) == self.audit_batch_size
