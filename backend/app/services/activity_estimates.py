"""Account-scoped finish projections over the actual, shared execution queues."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from ..models import (
    ACTIVE_STATUSES,
    ComfyUIInstanceHealth,
    Generation,
    GenerationPreparation,
    GenerationStatus,
    PromptGenerationRun,
    SchedulerState,
)
from .comfyui_instances import ComfyUIInstances
from .generation_eta import (
    DurationEstimate,
    GenerationEtaEstimator,
    _parse_timestamp,
    eta_payload,
    execution_start,
)


class ActivityEstimator:
    def __init__(self, estimator: GenerationEtaEstimator, instances: ComfyUIInstances) -> None:
        self.estimator = estimator
        self.instances = instances
        self._cache: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
        self._lock = threading.RLock()

    def project(self, session: Session, owner: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        jobs = list(
            session.scalars(
                select(Generation)
                .options(
                    load_only(
                        Generation.id,
                        Generation.owner_id,
                        Generation.status,
                        Generation.queue_seq,
                        Generation.comfyui_prompt_id,
                        Generation.comfyui_instance_id,
                        Generation.execution_timing_json,
                        Generation.timing_batch_id,
                        Generation.auto_cycle_id,
                        Generation.resolved_contract_json,
                        Generation.effective_controls_json,
                        Generation.generation_source_json,
                        Generation.workflow_id,
                        Generation.api_graph_sha256,
                        Generation.contract_sha256,
                        Generation.selected_preset,
                        Generation.requested_outputs_json,
                        Generation.final_prompt,
                    )
                )
                .where(Generation.status.in_(ACTIVE_STATUSES))
            )
        )
        own = [g for g in jobs if g.owner_id == owner]
        preparations = list(
            session.scalars(
                select(GenerationPreparation)
                .options(
                    load_only(
                        GenerationPreparation.id,
                        GenerationPreparation.owner_id,
                        GenerationPreparation.status,
                    )
                )
                .where(GenerationPreparation.status.in_(["preparing", "refining", "ready"]))
            )
        )
        own_preparations = [p for p in preparations if p.owner_id == owner]
        text_jobs = list(
            session.scalars(
                select(PromptGenerationRun)
                .options(
                    load_only(
                        PromptGenerationRun.id,
                        PromptGenerationRun.instance_id,
                        PromptGenerationRun.status,
                    )
                )
                .where(PromptGenerationRun.status.in_(["queued", "dispatching", "running"]))
            )
        )
        health = {
            h.instance_id: h.available for h in session.scalars(select(ComfyUIInstanceHealth))
        }
        last_owners = {s.key: s.last_user_id for s in session.scalars(select(SchedulerState))}
        queues = {}
        for config in self.instances.configs:
            observed = getattr(self.instances.get(config.id), "queue_observation", None)
            queues[config.id] = (
                observed[1] if observed and time.monotonic() - observed[0] <= 5 else None
            )
        signature = json.dumps(
            [
                self.estimator.revision,
                [
                    (g.id, g.status, g.queue_seq, g.comfyui_prompt_id, g.execution_timing_json)
                    for g in jobs
                ],
                [(p.id, p.status) for p in preparations],
                [(p.id, p.status) for p in text_jobs],
                health,
                queues,
                last_owners,
            ],
            sort_keys=True,
            default=str,
        )
        signature = hashlib.sha256(signature.encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(owner)
            if cached and cached[0] == signature:
                return self._age(cached[1], now)
        running = [
            g
            for g in own
            if execution_start(g) and not (g.execution_timing_json or {}).get("finished_at")
        ]
        current_estimates = [
            (g, self.estimator.estimate(g, now=now) if health.get(g.comfyui_instance_id) else None)
            for g in running
        ]
        current_estimates.sort(
            key=lambda pair: (
                pair[1] is None,
                pair[1]["completion_at"] if pair[1] else "",
                pair[0].queue_seq,
            )
        )
        if len(current_estimates) > 1 and any(eta is None for _, eta in current_estimates):
            current_estimates.sort(key=lambda pair: pair[1] is not None)
        current_job, current_eta = current_estimates[0] if current_estimates else (None, None)
        result: dict[str, Any] = {
            "current_eta": current_eta,
            "queue_eta": None,
            "current_generation_id": current_job.id if current_job else None,
            "running_count": len(running),
            "queued_count": len(own) + len(own_preparations) - len(running),
            "snapshot_at": now,
            "current_state": "running" if current_eta else "estimating" if running else "waiting",
        }
        # An unmeasured upstream stage cannot be silently omitted or multiplied by batch size.
        known = bool(own) and not own_preparations
        finishes: list[tuple[float, DurationEstimate]] = []
        blocker_ends: list[datetime] = []
        evidence: list[DurationEstimate] = []
        for runtime in {g.comfyui_instance_id for g in own}:
            group = [g for g in jobs if g.comfyui_instance_id == runtime]
            queue = queues.get(runtime)
            if (
                not health.get(runtime)
                or queue is None
                or any(t.instance_id == runtime for t in text_jobs)
            ):
                known = False
                continue
            native_order = [
                str(entry[1])
                for key in ("queue_running", "queue_pending")
                for entry in queue.get(key, [])
                if isinstance(entry, list) and len(entry) > 1
            ]
            mapped = {g.comfyui_prompt_id: g for g in group if g.comfyui_prompt_id}
            # External work behind every already-submitted owned job cannot delay this account.
            owned_positions = [
                i
                for i, prompt in enumerate(native_order)
                if prompt in mapped and mapped[prompt].owner_id == owner
            ]
            cutoff = max(owned_positions, default=len(native_order))
            if any(g.owner_id == owner and g.comfyui_prompt_id not in native_order for g in group):
                cutoff = len(native_order)
            if any(prompt not in mapped for prompt in native_order[: cutoff + 1]):
                known = False
                continue
            accepted = sorted(
                [g for g in group if g.status != GenerationStatus.QUEUED],
                key=lambda g: (
                    0 if execution_start(g) else 1,
                    native_order.index(g.comfyui_prompt_id)
                    if g.comfyui_prompt_id in native_order
                    else len(native_order),
                    g.queue_seq,
                ),
            )
            pending = [g for g in group if g.status == GenerationStatus.QUEUED]
            state_key = "instance:" + hashlib.sha256(runtime.encode()).hexdigest()[:40]
            last = last_owners.get(state_key)
            while pending:
                first = {
                    o: min(g.queue_seq for g in pending if g.owner_id == o)
                    for o in {g.owner_id for g in pending}
                }
                owners = sorted(first, key=lambda o: (first[o], o))
                next_owner = (
                    owners[(owners.index(last) + 1) % len(owners)] if last in owners else owners[0]
                )
                job = min(
                    (g for g in pending if g.owner_id == next_owner),
                    key=lambda g: (g.auto_cycle_id is not None, g.queue_seq),
                )
                accepted.append(job)
                pending.remove(job)
                last = next_owner
            elapsed = 0.0
            anchored = False
            for job in accepted:
                duration = self.estimator.duration(job)
                start = execution_start(job)
                if (
                    duration is None
                    or job.status == GenerationStatus.CANCEL_REQUESTED
                    or (job.execution_timing_json or {}).get("finished_at")
                ):
                    known = False
                    break
                evidence.append(duration)
                if start:
                    remaining = (start + timedelta(seconds=duration.seconds) - now).total_seconds()
                    if anchored or remaining <= 0:
                        known = False
                        break
                    elapsed = remaining
                    blocker_ends.append(start + timedelta(seconds=duration.seconds))
                    anchored = True
                else:
                    elapsed += duration.seconds
                if job.owner_id == owner:
                    finishes.append((elapsed, duration))
                # Work after the account's final accepted item cannot delay its completion.
                if job == next((g for g in reversed(accepted) if g.owner_id == owner), None):
                    break
            if not anchored:
                known = False
        if known and finishes:
            seconds = max(f[0] for f in finishes)
            confidence = min(
                (sample.confidence for sample in evidence),
                key=lambda c: {"low": 0, "medium": 1, "high": 2}[c],
            )
            total = DurationEstimate(
                seconds,
                seconds * 0.75,
                seconds * 1.25,
                "queue",
                min(sample.sample_count for sample in evidence),
                confidence,
            )
            result["queue_eta"] = eta_payload(total, now, now)
        result["_valid_until"] = min(blocker_ends) if blocker_ends else None
        with self._lock:
            self._cache[owner] = (signature, result)
            self._cache.move_to_end(owner)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        return self._age(result, now)

    @staticmethod
    def _age(snapshot: dict[str, Any], now: datetime) -> dict[str, Any]:
        result = {**snapshot, "snapshot_at": now}
        valid_until = result.pop("_valid_until", None)
        for key in ("current_eta", "queue_eta"):
            eta = snapshot[key]
            if eta:
                completion = _parse_timestamp(eta["completion_at"])
                updated = _parse_timestamp(eta["updated_at"])
                assert completion and updated
                age = max(0, (now - updated).total_seconds())
                result[key] = {
                    **eta,
                    "remaining_seconds": max(0, (completion - now).total_seconds()),
                    "lower_seconds": max(0, eta["lower_seconds"] - age),
                    "upper_seconds": max(0, eta["upper_seconds"] - age),
                    "updated_at": now.isoformat(),
                }
        if result["current_eta"] and result["current_eta"]["remaining_seconds"] <= 0:
            result["current_state"] = "overdue"
            result["queue_eta"] = None
        if valid_until and now >= valid_until:
            result["queue_eta"] = None
        return result
