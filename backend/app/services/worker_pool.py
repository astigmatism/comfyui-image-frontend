"""Opt-in pool of interchangeable ComfyUI image workers behind one primary.

The primary instance remains the authoritative publication catalog and is always
the first worker. Additional workers execute image jobs only, and only for a
source revision they were themselves validated to carry, so a job compiled from
the primary's frozen graph can never be dispatched to a runtime that does not
have that exact publication.

Image execution is bound late: a generation is accepted without a runtime and
the dispatcher records the winning worker in the same transaction that claims
it. Prompt (text) generation keeps its single fixed instance.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..config import ComfyUIInstanceConfig
from ..models import (
    ComfyUIInstanceHealth,
    Generation,
    GenerationStatus,
    ServiceHealth,
    WorkflowProfile,
)
from .comfyui_instances import ComfyUIInstances

#: Statuses that occupy a worker slot. A queued generation holds no worker.
ASSIGNED_STATUSES = (
    GenerationStatus.DISPATCHING,
    GenerationStatus.RUNNING,
    GenerationStatus.CANCEL_REQUESTED,
)

#: Fairness cursor shared by every image worker, so oldest-per-user FIFO and
#: per-user round robin apply to the pool rather than to one runtime.
IMAGE_POOL_SCHEDULER_SCOPE = "image-pool"


def scheduler_state_key(scope: str) -> str:
    return "instance:" + hashlib.sha256(scope.encode()).hexdigest()[:40]


@dataclass(frozen=True)
class WorkerStatus:
    id: str
    label: str
    available: bool
    active_count: int
    capacity: int

    @property
    def free_slots(self) -> int:
        return max(0, self.capacity - self.active_count) if self.available else 0

    @property
    def busy(self) -> bool:
        return self.active_count > 0

    @property
    def idle(self) -> bool:
        return self.available and self.free_slots > 0


@dataclass(frozen=True)
class WorkerPoolSnapshot:
    workers: tuple[WorkerStatus, ...] = ()
    unassigned_queued_count: int = 0

    @property
    def worker_count(self) -> int:
        return len(self.workers)

    @property
    def available_count(self) -> int:
        return sum(1 for worker in self.workers if worker.available)

    @property
    def busy_count(self) -> int:
        return sum(1 for worker in self.workers if worker.available and worker.busy)

    @property
    def idle_count(self) -> int:
        return sum(1 for worker in self.workers if worker.idle)

    @property
    def free_slot_count(self) -> int:
        return sum(worker.free_slots for worker in self.workers)

    def payload(self) -> dict[str, Any]:
        return {
            "worker_count": self.worker_count,
            "available_count": self.available_count,
            "idle_count": self.idle_count,
            "busy_count": self.busy_count,
            "free_slot_count": self.free_slot_count,
            "unassigned_queued_count": self.unassigned_queued_count,
        }


@dataclass
class ImageWorkerPool:
    """Membership and live occupancy of the image-execution pool."""

    instances: ComfyUIInstances
    member_ids: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        self.member_ids = tuple(self.instances.image_pool_ids)

    @property
    def primary_id(self) -> str:
        return self.instances.default_id

    def members(self) -> tuple[ComfyUIInstanceConfig, ...]:
        configs = tuple(
            config for member in self.member_ids if (config := self.instances.config(member))
        )
        return configs

    def is_member(self, instance_id: str | None) -> bool:
        return instance_id is not None and instance_id in self.member_ids

    def capacity(self, instance_id: str) -> int:
        config = self.instances.config(instance_id)
        return int(config.concurrency or 1) if config else 1

    def role(self, instance_id: str) -> str:
        if self.is_member(instance_id):
            return "image"
        if instance_id == self.instances.settings.comfyui_text_instance_id:
            return "text"
        return "unused"

    def health(self, session: Session) -> dict[str, bool]:
        """Read cached per-instance health with the legacy default fallback."""

        health = {
            str(row.instance_id): bool(row.available)
            for row in session.scalars(select(ComfyUIInstanceHealth))
        }
        if self.primary_id not in health:
            legacy = session.get(ServiceHealth, "comfyui")
            health[self.primary_id] = bool(legacy and legacy.available)
        return health

    def active_counts(self, session: Session) -> dict[str, int]:
        return {
            str(instance_id): int(count)
            for instance_id, count in session.execute(
                select(Generation.comfyui_instance_id, func.count())
                .where(
                    Generation.comfyui_instance_id.in_(self.member_ids),
                    Generation.status.in_(ASSIGNED_STATUSES),
                )
                .group_by(Generation.comfyui_instance_id)
            )
        }

    def snapshot(self, session: Session) -> WorkerPoolSnapshot:
        health = self.health(session)
        active = self.active_counts(session)
        queued = int(
            session.scalar(
                select(func.count())
                .select_from(Generation)
                .where(
                    Generation.status == GenerationStatus.QUEUED,
                    Generation.comfyui_instance_id.is_(None),
                )
            )
            or 0
        )
        return WorkerPoolSnapshot(
            workers=tuple(
                WorkerStatus(
                    id=config.id,
                    label=config.label,
                    available=bool(health.get(config.id)),
                    active_count=active.get(config.id, 0),
                    capacity=int(config.concurrency or 1),
                )
                for config in self.members()
            ),
            unassigned_queued_count=queued,
        )

    def available_member_ids(self, session: Session) -> tuple[str, ...]:
        health = self.health(session)
        return tuple(member for member in self.member_ids if health.get(member))

    def eligible_clause(self, worker_id: str) -> ColumnElement[bool]:
        """Restrict pooled claims to jobs whose exact revision this worker carries.

        Revision identity, not catalog currency, is the test: a republication
        after acceptance must not strand an already accepted job, and a worker
        that never carried the accepted revision must never receive it.
        """

        accepted = aliased(WorkflowProfile)
        replica = aliased(WorkflowProfile)
        carries_revision = exists(
            select(replica.id)
            .join(accepted, accepted.id == Generation.workflow_profile_id)
            .where(
                replica.instance_id == worker_id,
                replica.source_id.is_not(None),
                replica.source_id == accepted.source_id,
                replica.publication_id == Generation.workflow_version,
                replica.ui_graph_sha256 == Generation.ui_graph_sha256,
                replica.api_graph_sha256 == Generation.api_graph_sha256,
                replica.manifest_sha256 == Generation.contract_sha256,
            )
        )
        if worker_id != self.primary_id:
            return carries_revision
        # A pre-publication profile has no comparable revision identity. Only the
        # catalog instance that accepted it can execute it.
        legacy_profile = exists(
            select(accepted.id).where(
                accepted.id == Generation.workflow_profile_id,
                or_(accepted.source_id.is_(None), accepted.publication_id.is_(None)),
            )
        )
        return or_(carries_revision, legacy_profile)

    def claimable_clause(self, worker_id: str) -> ColumnElement[bool]:
        """Pooled jobs this worker may take, plus jobs already pinned to it."""

        return or_(
            and_(Generation.comfyui_instance_id.is_(None), self.eligible_clause(worker_id)),
            Generation.comfyui_instance_id == worker_id,
        )
