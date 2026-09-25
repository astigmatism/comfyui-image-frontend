from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..blocking import run_blocking as _run_blocking
from ..domain.prompt_instructions import DEFAULT_PROMPT_INSTRUCTIONS
from ..errors import AppError
from ..models import (
    ACTIVE_STATUSES,
    AutoGeneration,
    AutoGenerationCycle,
    Generation,
    GenerationEvent,
    GenerationPreparation,
    GenerationRunMember,
    GenerationStatus,
    PromptGenerationRun,
    User,
    UserState,
    WorkflowProfile,
)
from ..schemas import (
    AutoGenerationResponse,
    AutoGenerationSnapshot,
    GenerationPreparationCreate,
    GenerationPreparationItem,
    PromptAssistantSnapshot,
)
from .auto_generation_progress import project_progress
from .events import event_payload
from .generation_activity import begin_run
from .prompt_assistant import compose_prompt
from .user_state import lock_user_state, notify_user

if TYPE_CHECKING:
    from ..container import AppContainer

logger = logging.getLogger(__name__)
_RETRYABLE = {
    "comfyui_instance_unavailable",
    "source_catalog_loading",
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
    progress = None
    if session:
        cycle = session.scalar(
            select(AutoGenerationCycle)
            .where(
                AutoGenerationCycle.user_id == row.user_id,
                AutoGenerationCycle.revision == row.revision,
                AutoGenerationCycle.state != "discarded",
            )
            .order_by(AutoGenerationCycle.created_at.desc(), AutoGenerationCycle.id.desc())
            .limit(1)
        )
        preparation = (
            session.scalar(
                select(GenerationPreparation)
                .where(GenerationPreparation.auto_cycle_id == cycle.id)
                .order_by(GenerationPreparation.position)
                .limit(1)
            )
            if cycle
            else None
        )
        text = session.get(PromptGenerationRun, preparation.prompt_run_id) if preparation else None
        images_active = session.scalar(
            select(Generation.id)
            .where(
                Generation.owner_id == row.user_id,
                Generation.auto_cycle_id.is_not(None),
                Generation.status.in_(ACTIVE_STATUSES),
            )
            .limit(1)
        )
        progress = project_progress(
            row, cycle, preparation, text, images_active=images_active is not None
        )
    return AutoGenerationResponse(
        progress=progress,
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
        text_profile = None
        if snapshot.prompt_generation:
            text_profile, _ = self.container.prompt_generation.validate_source(
                session, snapshot.prompt_generation
            )
        # Validate every variant, without freezing resolved random seeds.
        for variant in snapshot.variants:
            item = request.model_copy(
                update={"parameters": {**request.public_parameters, **variant}, "controls": None}
            )
            if snapshot.prompt_generation:
                self.container.prompt_generation.capture_image(
                    session,
                    user_id,
                    GenerationPreparationItem(
                        generation=item,
                        prompt_generation=snapshot.prompt_generation,
                        assistant=snapshot.assistant,
                    ),
                    profile=profile,
                )
            else:
                service._compile(session, user=user, profile=profile, request=item)
        assistant = snapshot.assistant
        if assistant:
            if (
                assistant.mode == "refine"
                and not assistant.prompt.strip()
                and not snapshot.prompt_generation
            ):
                raise AppError("prompt_required", "Refine mode requires a starting prompt.")
            if not assistant.creative_direction.strip():
                raise AppError("direction_required", "Enter Creative Direction before enabling it.")
        captured = snapshot.model_copy(deep=True)
        captured.generation.comfyui_instance_id = runtime.id
        captured.generation.source_key = profile.source_key
        captured.generation.profile_id = None
        if captured.prompt_generation and text_profile:
            captured.prompt_generation.comfyui_instance_id = text_profile.instance_id
            captured.prompt_generation.source_key = str(text_profile.source_key)
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
        from .prompt_generation import PREPARATION_ACTIVE, PromptGenerationService

        for prepared in session.scalars(
            select(GenerationPreparation).where(
                GenerationPreparation.owner_id == row.user_id,
                GenerationPreparation.auto_cycle_id.is_not(None),
                GenerationPreparation.status.in_(PREPARATION_ACTIVE),
            )
        ):
            PromptGenerationService.fail_preparation(
                session,
                prepared,
                "discarded",
                "Automatic settings changed before image acceptance.",
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
        def change_transaction() -> tuple[bool, AutoGenerationResponse]:
            with self.container.db.session_factory() as session:
                row = self._get_locked(session, user_id, revision)
                if row.enabled and row.error_code == "collection_deleted" and enabled is not False:
                    raise AppError(
                        "collection_deleted",
                        "The destination folder was deleted. Turn off auto generation, "
                        "open another folder, and turn it on again.",
                        status_code=409,
                    )
                if enabled is not None and row.enabled == enabled:
                    return False, response(row, session)
                if enabled is True or snapshot is not None:
                    if snapshot is None:
                        raise AppError(
                            "snapshot_required", "Capture settings before enabling auto generation."
                        )
                    if enabled is not True and not row.enabled:
                        raise AppError(
                            "auto_generation_disabled", "Auto generation is off.", status_code=409
                        )
                    if row.enabled and snapshot.generation.collection_id != row.snapshot_json.get(
                        "generation", {}
                    ).get("collection_id"):
                        raise AppError(
                            "auto_generation_destination_locked",
                            "Turn off auto generation before choosing another destination folder.",
                            status_code=409,
                        )
                    captured, profile_id = self._capture(session, user_id, snapshot)
                    if row.enabled and captured == row.snapshot_json:
                        return False, response(row, session)
                    row.snapshot_json, row.profile_id = captured, profile_id
                    row.latest_prompt = snapshot.assistant.prompt if snapshot.assistant else None
                if (retry or reset_limit) and not row.snapshot_json:
                    raise AppError("snapshot_required", "Enable auto generation first.")
                if enabled is True:
                    row.accepted_count = 0
                if reset_limit:
                    row.snapshot_json = {**row.snapshot_json, "max_generations": limit}
                self._invalidate(session, row)
                if enabled is not None:
                    row.enabled = enabled
                row.status = "waiting" if row.enabled else "off"
                maximum = row.snapshot_json.get("max_generations")
                if row.enabled and maximum is not None and row.accepted_count >= maximum:
                    row.enabled, row.status = False, "completed"
                    row.message = "Image queue limit reached. Accepted images will finish."
                session.commit()
                result = response(row, session)
            return True, result

        changed, result = await _run_blocking(change_transaction)
        if not changed:
            return result
        logger.info(
            "auto_generation_configuration_changed",
            extra={
                "actor_user_id": user_id,
                "action": "limit_changed" if reset_limit else "configuration",
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

                    def clear_abandoned_claims() -> None:
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

                    await _run_blocking(clear_abandoned_claims)
                    recovered = True
                self._ready = True
                for user_id, task in list(self._users.items()):
                    if task.done():
                        if not task.cancelled():
                            task.result()
                        del self._users[user_id]

                def enabled_owners() -> list[str]:
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
                    return owners

                owners = await _run_blocking(enabled_owners)
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
        revision = None
        prepared = None
        try:
            preparing = asyncio.create_task(_run_blocking(self._prepare_cycle, user_id))
            try:
                prepared = await asyncio.shield(preparing)
            except asyncio.CancelledError:
                prepared = await preparing
                raise
            if prepared is None:
                return
            revision = prepared["revision"]
            if prepared["action"] == "changed":
                await notify_user(self.container.broker, user_id, "auto_generation.updated")
                return
            if prepared["action"] == "compose":
                await notify_user(self.container.broker, user_id, "auto_generation.updated")
                composed = None
                if prepared["assistant"]:
                    composed = await compose_prompt(self.container, user_id, prepared["assistant"])
                completed = await _run_blocking(
                    self._complete_composition, user_id, prepared, composed
                )
                if not completed:
                    return
                if prepared["assistant"]:
                    await notify_user(self.container.broker, user_id, "auto_generation.updated")
            events = await _run_blocking(
                self._accept_ready, user_id, revision, prepared["cycle_id"]
            )
            for event in events or []:
                await self.container.broker.publish(user_id, event)
            await notify_user(self.container.broker, user_id, "auto_generation.updated")
        except asyncio.CancelledError:
            if prepared and prepared.get("claim"):
                await _run_blocking(self._release_claim, user_id, prepared)
            raise
        except Exception as error:
            logger.warning(
                "auto_generation_cycle_failed",
                extra={"actor_user_id": user_id, "exception_class": type(error).__name__},
            )
            await _run_blocking(self._record_error, user_id, revision, error)
            await notify_user(self.container.broker, user_id, "auto_generation.updated")

    def _normalize_assignments(
        self, session: Session, row: AutoGeneration, snapshot: AutoGenerationSnapshot
    ) -> AutoGenerationSnapshot:
        # These pins describe the next cycle, not a user preference. Accepted jobs
        # and preparations already own independent immutable captured requests.
        editable = snapshot.model_copy(deep=True)
        editable.generation.comfyui_instance_id = None
        if editable.prompt_generation:
            editable.prompt_generation.comfyui_instance_id = None
        captured, profile_id = self._capture(session, row.user_id, editable)
        row.snapshot_json, row.profile_id = captured, profile_id
        return AutoGenerationSnapshot.model_validate(captured)

    def _prepare_cycle(self, user_id: str) -> dict[str, Any] | None:
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
                return None
            try:
                revision = row.revision
                if row.next_retry_at and row.next_retry_at.replace(tzinfo=UTC) > datetime.now(UTC):
                    return None
                snapshot = AutoGenerationSnapshot.model_validate(row.snapshot_json)
                # A failed accepted cycle is observed once; never flood the gallery with failures.
                accepted = session.scalars(
                    select(AutoGenerationCycle).where(
                        AutoGenerationCycle.user_id == user_id,
                        AutoGenerationCycle.state == "accepted",
                    )
                ).all()
                for previous in accepted:
                    if session.scalar(
                        select(GenerationPreparation.id)
                        .where(
                            GenerationPreparation.auto_cycle_id == previous.id,
                            GenerationPreparation.status.in_(["preparing", "refining", "ready"]),
                        )
                        .limit(1)
                    ):
                        continue
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
                        return {"action": "changed", "revision": revision}
                if snapshot.prompt_generation:
                    return self._prepare_prompt_cycle(session, row, snapshot)
                cycle = session.scalar(
                    select(AutoGenerationCycle).where(
                        AutoGenerationCycle.user_id == user_id,
                        AutoGenerationCycle.revision == revision,
                        AutoGenerationCycle.state.in_(["preparing", "ready"]),
                    )
                )
                if cycle is None:
                    snapshot = self._normalize_assignments(session, row, snapshot)
                    cycle = AutoGenerationCycle(user_id=user_id, revision=revision)
                    session.add(cycle)
                    session.flush()
                if cycle.state == "preparing":
                    if cycle.claim:
                        return None
                    claim = str(uuid.uuid4())
                    cycle.claim = claim
                    cycle_id = cycle.id
                    row.status = "preparing"
                    latest = row.latest_prompt
                    session.commit()
                    assistant = snapshot.assistant
                    if assistant:
                        assistant = assistant.model_copy(
                            update={"prompt": latest or assistant.prompt}
                        )
                    return {
                        "action": "compose",
                        "revision": revision,
                        "cycle_id": cycle_id,
                        "claim": claim,
                        "assistant": assistant,
                    }
                return {"action": "ready", "revision": revision, "cycle_id": cycle.id}
            except AppError as error:
                self._set_error(row, error)
                session.commit()
                return {"action": "changed", "revision": row.revision}

    def _prepare_prompt_cycle(
        self, session: Session, row: AutoGeneration, snapshot: AutoGenerationSnapshot
    ) -> dict[str, Any] | None:
        from .prompt_generation import PREPARATION_ACTIVE

        pending = session.scalar(
            select(GenerationPreparation.id)
            .where(
                GenerationPreparation.owner_id == row.user_id,
                GenerationPreparation.status.in_(PREPARATION_ACTIVE),
            )
            .limit(1)
        )
        images = session.scalar(
            select(Generation.id)
            .where(Generation.owner_id == row.user_id, Generation.status.in_(ACTIVE_STATUSES))
            .limit(1)
        )
        if pending or images:
            return None
        limit = snapshot.max_generations
        budget = (
            min(len(snapshot.variants) * snapshot.quantity, limit - row.accepted_count)
            if limit is not None
            else len(snapshot.variants) * snapshot.quantity
        )
        if budget <= 0:
            row.enabled, row.status, row.message = False, "completed", "Generation limit reached."
            session.commit()
            return {"action": "changed", "revision": row.revision}
        snapshot = self._normalize_assignments(session, row, snapshot)
        cycle = AutoGenerationCycle(user_id=row.user_id, revision=row.revision, state="accepted")
        session.add(cycle)
        session.flush()
        assert snapshot.prompt_generation is not None
        items = [
            GenerationPreparationItem(
                generation=snapshot.generation.model_copy(
                    update={
                        "parameters": {**snapshot.generation.public_parameters, **variant},
                        "controls": None,
                    }
                ),
                prompt_generation=snapshot.prompt_generation,
                assistant=snapshot.assistant,
            )
            for variant in snapshot.variants
            for _ in range(snapshot.quantity)
        ][:budget]
        profile = session.get(WorkflowProfile, row.profile_id)
        self.container.prompt_generation.create_preparations(
            session,
            row.user_id,
            GenerationPreparationCreate(items=items),
            cycle=cycle,
            profile=profile,
        )
        row.status = "preparing"
        session.commit()
        return {"action": "changed", "revision": row.revision}

    def _complete_composition(self, user_id: str, prepared: dict[str, Any], composed: Any) -> bool:
        with self.container.db.session_factory() as session:
            lock_user_state(session)
            row = session.get(AutoGeneration, user_id)
            cycle = session.get(AutoGenerationCycle, prepared["cycle_id"])
            if (
                not row
                or not row.enabled
                or row.revision != prepared["revision"]
                or not cycle
                or cycle.claim != prepared["claim"]
            ):
                return False
            cycle.prompt = composed.prompt if composed else None
            cycle.prompt_run_id = composed.composition_id if composed else None
            cycle.state = "ready"
            cycle.claim = None
            session.commit()
            return True

    def _release_claim(self, user_id: str, prepared: dict[str, Any]) -> None:
        with self.container.db.session_factory() as session:
            lock_user_state(session)
            cycle = session.get(AutoGenerationCycle, prepared["cycle_id"])
            if cycle and cycle.user_id == user_id and cycle.claim == prepared["claim"]:
                cycle.claim = None
                session.commit()

    def _accept_ready(
        self, user_id: str, revision: int, cycle_id: str
    ) -> list[dict[str, Any]] | None:
        with self.container.db.session_factory() as session:
            lock_user_state(session)
            row = session.get(AutoGeneration, user_id)
            user = session.get(User, user_id)
            cycle = session.get(AutoGenerationCycle, cycle_id)
            if (
                not row
                or not row.enabled
                or row.revision != revision
                or not user
                or user.state != UserState.ACTIVE
                or not cycle
                or cycle.state != "ready"
            ):
                return None
            snapshot = AutoGenerationSnapshot.model_validate(row.snapshot_json)
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
                return None
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
                return None
            snapshot = self._normalize_assignments(session, row, snapshot)
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
                            "prompt_assistant_run_id": cycle.prompt_run_id if not events else None,
                        }
                    )
                    generation, event = self.container.generations._prepare_accept(
                        session,
                        user=user,
                        request=request,
                        frozen_profile=profile,
                    )
                    generation.auto_cycle_id = cycle.id
                    generation.timing_batch_id = cycle.id
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
            return [event_payload(event) for event in events]

    def _record_error(self, user_id: str, revision: int | None, error: Exception) -> None:
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
            self._set_error(row, error)
            session.commit()

    @staticmethod
    def _set_error(row: AutoGeneration, error: Exception) -> None:
        retryable = not isinstance(error, AppError) or error.code in _RETRYABLE
        row.failures += 1
        row.status = "retrying" if retryable else "blocked"
        row.error_code = error.code if isinstance(error, AppError) else "automation_unavailable"
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
