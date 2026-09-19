from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..domain.prompt_instructions import DEFAULT_PROMPT_INSTRUCTIONS
from ..errors import AppError
from ..models import (
    ACTIVE_STATUSES,
    AutoGeneration,
    AutoGenerationCycle,
    Generation,
    GenerationEvent,
    GenerationRunMember,
    GenerationStatus,
    User,
    UserState,
    WorkflowProfile,
)
from ..schemas import AutoGenerationResponse, AutoGenerationSnapshot, PromptAssistantSnapshot
from .events import publish_event
from .generation_activity import begin_run, retain_deleted_outcome
from .prompt_assistant import compose_prompt
from .user_state import lock_user_state, notify_user

if TYPE_CHECKING:
    from ..container import AppContainer

logger = logging.getLogger(__name__)
_RETRYABLE = {
    "comfyui_instance_unavailable",
    "ollama_output_budget_exhausted",
    "ollama_generate_unavailable",
    "ollama_generate_transport_error",
    "ollama_generate_timeout",
    "ollama_generate_invalid_json",
    "ollama_unavailable",
}


def response(row: AutoGeneration | None, session: Session | None = None) -> AutoGenerationResponse:
    if row is None:
        return AutoGenerationResponse()
    snapshot = (
        AutoGenerationSnapshot.model_validate(row.snapshot_json) if row.snapshot_json else None
    )
    limit = snapshot.max_generations if snapshot else None
    ready = (
        session.scalar(
            select(AutoGenerationCycle.id)
            .where(
                AutoGenerationCycle.user_id == row.user_id,
                AutoGenerationCycle.revision == row.revision,
                AutoGenerationCycle.state == "ready",
                AutoGenerationCycle.prompt_run_id.is_not(None),
            )
            .limit(1)
        )
        if session
        else None
    )
    profile = session.get(WorkflowProfile, row.profile_id) if session and row.profile_id else None
    return AutoGenerationResponse(
        prompt_ready=bool(ready),
        workflow_name=profile.display_name if profile else None,
        enabled=row.enabled,
        revision=row.revision,
        status=row.status,
        snapshot=snapshot,
        latest_prompt=row.latest_prompt,
        accepted_count=row.accepted_count,
        remaining=max(0, limit - row.accepted_count) if limit is not None else None,
        error_code=row.error_code,
        message=row.message,
        next_retry_at=row.next_retry_at,
        updated_at=row.updated_at,
    )


class AutoGenerationService:
    def __init__(self, container: AppContainer) -> None:
        self.container = container
        self._task: asyncio.Task[None] | None = None
        self._users: dict[str, asyncio.Task[None]] = {}
        self._ready = False

    def health_snapshot(self) -> dict[str, Any]:
        disabled = not self.container.settings.enable_background_worker
        return {
            "ready": disabled or bool(self._ready and self._task and not self._task.done()),
            "active_users": len(self._users),
        }

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="auto-generation-coordinator")

    async def stop(self) -> None:
        tasks = [*self._users.values(), *([self._task] if self._task else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._users.clear()
        self._task = None
        self._ready = False

    def _get_locked(self, session: Session, user_id: str, revision: int) -> AutoGeneration:
        lock_user_state(session)
        row = session.get(AutoGeneration, user_id)
        if row is None:
            row = AutoGeneration(user_id=user_id)
            session.add(row)
            session.flush()
        if row.revision != revision:
            raise AppError(
                "auto_generation_conflict",
                "Auto generation changed on another device. Review its current state.",
                status_code=409,
            )
        user = session.get(User, user_id)
        if user is None or user.state != UserState.ACTIVE:
            raise AppError("not_found", "Account is unavailable.", status_code=404)
        return row

    def _capture(
        self, session: Session, user_id: str, snapshot: AutoGenerationSnapshot
    ) -> tuple[dict[str, Any], str]:
        service = self.container.generations
        user = session.get(User, user_id)
        assert user is not None
        request = snapshot.generation
        profile = service._profile_for_request(session, request)
        runtime = service._instance_for_request(session, request, require_available=False)
        service._collection_for_owner(session, user_id, request.collection_id)
        # Validate every variant, without freezing resolved random seeds.
        for variant in snapshot.variants:
            item = request.model_copy(
                update={"parameters": {**request.public_parameters, **variant}, "controls": None}
            )
            service._compile(session, user=user, profile=profile, request=item)
        assistant = snapshot.assistant
        if assistant:
            if assistant.mode == "refine" and not assistant.prompt.strip():
                raise AppError("prompt_required", "Refine mode requires a starting prompt.")
            if not assistant.creative_direction.strip():
                raise AppError("direction_required", "Enter Creative Direction before enabling it.")
        captured = snapshot.model_copy(deep=True)
        captured.generation.comfyui_instance_id = runtime.id
        if captured.assistant and not captured.assistant.instructions:
            captured.assistant.instructions = DEFAULT_PROMPT_INSTRUCTIONS[captured.assistant.mode]
        if captured.assistant:
            captured.generation.prompt_assistant = PromptAssistantSnapshot(
                mode=captured.assistant.mode,
                creative_direction=captured.assistant.creative_direction,
                instructions=captured.assistant.instructions,
                thinking_enabled=captured.assistant.think,
            )
        return captured.model_dump(mode="json"), profile.id

    @staticmethod
    def _invalidate(session: Session, row: AutoGeneration) -> None:
        session.execute(
            update(AutoGenerationCycle)
            .where(
                AutoGenerationCycle.user_id == row.user_id,
                AutoGenerationCycle.state.in_(["preparing", "ready"]),
            )
            .values(state="discarded", claim=None)
        )
        row.revision += 1
        row.failures = 0
        row.error_code = None
        row.message = None
        row.next_retry_at = None
        row.updated_at = datetime.now(UTC)

    async def change(
        self,
        user_id: str,
        revision: int,
        *,
        enabled: bool | None = None,
        snapshot: AutoGenerationSnapshot | None = None,
        retry: bool = False,
        limit: int | None = None,
        reset_limit: bool = False,
    ) -> AutoGenerationResponse:
        deleted: list[str] = []
        with self.container.db.session_factory() as session:
            row = self._get_locked(session, user_id, revision)
            if enabled is not None and row.enabled == enabled:
                return response(row, session)
            if enabled is True or snapshot is not None:
                if snapshot is None:
                    raise AppError(
                        "snapshot_required", "Capture settings before enabling auto generation."
                    )
                row.snapshot_json, row.profile_id = self._capture(session, user_id, snapshot)
                row.latest_prompt = snapshot.assistant.prompt if snapshot.assistant else None
            if (retry or reset_limit) and not row.snapshot_json:
                raise AppError("snapshot_required", "Enable auto generation first.")
            if enabled is True:
                row.accepted_count = 0
            if reset_limit:
                row.snapshot_json = {**row.snapshot_json, "max_generations": limit}
                row.accepted_count = 0
            self._invalidate(session, row)
            if enabled is not None:
                row.enabled = enabled
            row.status = "waiting" if row.enabled else "off"
            if enabled is False:
                # Serialize with dispatch. No external requests or partial commits here.
                queued = session.scalars(
                    select(Generation).where(
                        Generation.owner_id == user_id,
                        Generation.auto_cycle_id.is_not(None),
                        Generation.status == GenerationStatus.QUEUED,
                    )
                ).all()
                for job in queued:
                    job.status = GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS
                    retain_deleted_outcome(session, job)
                    deleted.append(job.id)
                    session.delete(job)
            session.commit()
            result = response(row, session)
        for job_id in deleted:
            await self.container.broker.publish(
                user_id,
                {"id": None, "type": "generation.deleted", "generation_id": job_id, "payload": {}},
            )
        logger.info(
            "auto_generation_configuration_changed",
            extra={
                "actor_user_id": user_id,
                "action": "limit_reset" if reset_limit else "configuration",
            },
        )
        await notify_user(self.container.broker, user_id, "auto_generation.updated")
        return result

    async def _run(self) -> None:
        recovered = False
        while True:
            try:
                if not self.container.worker.health_snapshot()["ready"]:
                    await asyncio.sleep(0.1)
                    continue
                if not recovered:
                    with self.container.db.session_factory() as session:
                        lock_user_state(session)
                        session.execute(
                            update(AutoGenerationCycle)
                            .where(
                                AutoGenerationCycle.state == "preparing",
                            )
                            .values(claim=None)
                        )
                        session.commit()
                    recovered = True
                self._ready = True
                for user_id, task in list(self._users.items()):
                    if task.done():
                        if not task.cancelled():
                            task.result()
                        del self._users[user_id]
                with self.container.db.session_factory() as session:
                    owners = list(
                        session.scalars(
                            select(AutoGeneration.user_id)
                            .join(
                                User,
                                User.id == AutoGeneration.user_id,
                            )
                            .where(
                                AutoGeneration.enabled.is_(True),
                                AutoGeneration.status != "blocked",
                                User.state == UserState.ACTIVE,
                            )
                        )
                    )
                for user_id in owners:
                    if user_id not in self._users:
                        self._users[user_id] = asyncio.create_task(self.step(user_id))
            except asyncio.CancelledError:
                raise
            except Exception:
                self._ready = False
                logger.exception("auto_generation_coordinator_error")
                # Consume failures; the durable state is retried on the next iteration.
                self._users = {key: task for key, task in self._users.items() if not task.done()}
            await asyncio.sleep(max(0.1, self.container.settings.dispatch_poll_seconds))

    async def step(self, user_id: str) -> None:
        revision: int | None = None
        try:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                row = session.get(AutoGeneration, user_id)
                user = session.get(User, user_id)
                if (
                    not row
                    or not row.enabled
                    or row.status == "blocked"
                    or not user
                    or user.state != UserState.ACTIVE
                ):
                    return
                revision = row.revision
                if row.next_retry_at and row.next_retry_at.replace(tzinfo=UTC) > datetime.now(UTC):
                    return
                snapshot = AutoGenerationSnapshot.model_validate(row.snapshot_json)
                # A failed accepted cycle is observed once; never flood the gallery with failures.
                accepted = session.scalars(
                    select(AutoGenerationCycle).where(
                        AutoGenerationCycle.user_id == user_id,
                        AutoGenerationCycle.state == "accepted",
                    )
                ).all()
                for previous in accepted:
                    jobs = session.scalars(
                        select(Generation).where(Generation.auto_cycle_id == previous.id)
                    ).all()
                    if any(job.status in ACTIVE_STATUSES for job in jobs):
                        continue
                    previous.state = "completed"
                    failed = next(
                        (
                            job
                            for job in jobs
                            if job.status
                            in {
                                GenerationStatus.FAILED_WITH_ARTIFACTS,
                                GenerationStatus.FAILED_WITHOUT_ARTIFACTS,
                                GenerationStatus.INTERRUPTED,
                            }
                        ),
                        None,
                    )
                    if failed and previous.revision == revision:
                        row.status = "blocked"
                        row.error_code = failed.error_code or "automatic_generation_failed"
                        row.message = (
                            failed.error_message
                            or "An automatic generation failed. Review its result, then retry."
                        )
                        session.commit()
                        await notify_user(self.container.broker, user_id, "auto_generation.updated")
                        return
                cycle = session.scalar(
                    select(AutoGenerationCycle).where(
                        AutoGenerationCycle.user_id == user_id,
                        AutoGenerationCycle.revision == revision,
                        AutoGenerationCycle.state.in_(["preparing", "ready"]),
                    )
                )
                if cycle is None:
                    cycle = AutoGenerationCycle(user_id=user_id, revision=revision)
                    session.add(cycle)
                    session.flush()
                if cycle.state == "preparing":
                    if cycle.claim:
                        return
                    claim = str(uuid.uuid4())
                    cycle.claim = claim
                    cycle_id = cycle.id
                    row.status = "preparing"
                    latest = row.latest_prompt
                    session.commit()
                    assistant = snapshot.assistant
                    # Release the connection while calling the model.
                    session.close()
                    await notify_user(self.container.broker, user_id, "auto_generation.updated")
                    composed = None
                    if assistant:
                        assistant = assistant.model_copy(
                            update={"prompt": latest or assistant.prompt}
                        )
                        composed = await compose_prompt(self.container, session, user_id, assistant)
                    lock_user_state(session)
                    session.expire_all()
                    row = session.get(AutoGeneration, user_id)
                    cycle = session.get(AutoGenerationCycle, cycle_id)
                    if (
                        not row
                        or not row.enabled
                        or row.revision != revision
                        or not cycle
                        or cycle.claim != claim
                    ):
                        return
                    cycle.prompt = composed.prompt if composed else None
                    cycle.prompt_run_id = composed.composition_id if composed else None
                    cycle.state = "ready"
                    cycle.claim = None
                    session.commit()
                # Reacquire a transaction: controls may have changed during composition.
                lock_user_state(session)
                session.expire_all()
                row = session.get(AutoGeneration, user_id)
                cycle = session.get(AutoGenerationCycle, cycle.id)
                if (
                    not row
                    or not row.enabled
                    or row.revision != revision
                    or not cycle
                    or cycle.state != "ready"
                ):
                    return
                pending = session.scalar(
                    select(Generation.id)
                    .where(
                        Generation.owner_id == user_id,
                        Generation.status.in_(ACTIVE_STATUSES),
                    )
                    .limit(1)
                )
                if pending:
                    row.status = "generating"
                    session.commit()
                    return
                limit = snapshot.max_generations
                budget = (
                    min(len(snapshot.variants) * snapshot.quantity, limit - row.accepted_count)
                    if limit is not None
                    else len(snapshot.variants) * snapshot.quantity
                )
                if budget <= 0:
                    row.enabled = False
                    row.status = "completed"
                    row.message = "Generation limit reached."
                    session.commit()
                    await notify_user(self.container.broker, user_id, "auto_generation.updated")
                    return
                profile = session.get(WorkflowProfile, row.profile_id)
                if profile is None:
                    raise AppError("source_unavailable", "The captured workflow is unavailable.")
                parameters = copy.deepcopy(snapshot.generation.public_parameters)
                if cycle.prompt:
                    prompt_id = next(
                        (
                            item["id"]
                            for item in profile.resolved_contract_json.get("inputs", [])
                            if item.get("semantic_role") == "positive_prompt"
                        ),
                        None,
                    )
                    if not prompt_id:
                        raise AppError(
                            "source_unavailable", "The captured workflow has no prompt input."
                        )
                    parameters[prompt_id] = cycle.prompt
                # Quantity one shares a seed across checkpoints; repeats resolve independently.
                if snapshot.quantity == 1:
                    compiled = self.container.generations._compile(
                        session,
                        user=user,
                        profile=profile,
                        request=snapshot.generation.model_copy(
                            update={
                                "parameters": {**parameters, **snapshot.variants[0]},
                                "controls": None,
                            }
                        ),
                    )
                    parameters.update(
                        {key: str(value) for key, value in compiled.resolved_seeds.items()}
                    )
                run = begin_run(session, user_id, budget)
                events: list[GenerationEvent] = []
                for variant in snapshot.variants:
                    for _ in range(snapshot.quantity):
                        if len(events) >= budget:
                            break
                        request = snapshot.generation.model_copy(
                            update={
                                "parameters": {**parameters, **variant},
                                "controls": None,
                                "prompt_assistant_run_id": cycle.prompt_run_id
                                if not events
                                else None,
                            }
                        )
                        generation, event = self.container.generations._prepare_accept(
                            session,
                            user=user,
                            request=request,
                            frozen_profile=profile,
                        )
                        generation.auto_cycle_id = cycle.id
                        session.add(GenerationRunMember(generation_id=generation.id, run_id=run.id))
                        events.append(event)
                cycle.state = "accepted"
                row.accepted_count += len(events)
                row.latest_prompt = cycle.prompt or row.latest_prompt
                row.failures = 0
                row.next_retry_at = None
                row.error_code = None
                row.message = None
                row.status = "generating"
                if limit is not None and row.accepted_count >= limit:
                    row.enabled = False
                    row.status = "completed"
                    row.message = "Generation limit reached. The final jobs will finish."
                session.commit()
                logger.info(
                    "auto_generation_cycle_accepted",
                    extra={"actor_user_id": user_id, "target_id": cycle.id},
                )
                for event in events:
                    await publish_event(self.container.broker, event)
            await notify_user(self.container.broker, user_id, "auto_generation.updated")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "auto_generation_cycle_failed",
                extra={"actor_user_id": user_id, "exception_class": type(error).__name__},
            )
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                row = session.get(AutoGeneration, user_id)
                if not row or not row.enabled or row.revision != revision:
                    return
                session.execute(
                    update(AutoGenerationCycle)
                    .where(
                        AutoGenerationCycle.user_id == user_id,
                        AutoGenerationCycle.state == "preparing",
                    )
                    .values(claim=None)
                )
                retryable = not isinstance(error, AppError) or error.code in _RETRYABLE
                row.failures += 1
                row.status = "retrying" if retryable else "blocked"
                row.error_code = (
                    error.code if isinstance(error, AppError) else "automation_unavailable"
                )
                row.message = (
                    error.message
                    if isinstance(error, AppError)
                    else "Auto generation is temporarily unavailable. Retrying."
                )
                row.next_retry_at = (
                    datetime.now(UTC) + timedelta(seconds=min(60, 2 ** min(row.failures - 1, 6)))
                    if retryable
                    else None
                )
                session.commit()
            await notify_user(self.container.broker, user_id, "auto_generation.updated")
