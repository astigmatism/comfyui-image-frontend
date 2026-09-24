"""Durable text jobs and per-image preparation, sharing the image dispatcher."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..blocking import run_blocking
from ..domain.prompt_generation import adapt_seed, collect_text
from ..domain.publication import publication_kind
from ..errors import AppError
from ..models import (
    AutoGeneration,
    AutoGenerationCycle,
    Generation,
    GenerationPreparation,
    GenerationRun,
    GenerationRunMember,
    GenerationSubmission,
    PromptGenerationRun,
    User,
    UserState,
    WorkflowProfile,
)
from ..schemas import (
    GenerationCreate,
    GenerationPreparationCreate,
    GenerationPreparationItem,
    PromptAssistantSnapshot,
    PromptGenerationCreate,
)
from .comfyui import _queue_prompt_ids, prompt_rejection_diagnostics
from .events import event_payload
from .generation_activity import begin_run
from .prompt_assistant import compose_prompt
from .user_state import lock_user_state, notify_user, require_manual_generation

if TYPE_CHECKING:
    from ..container import AppContainer
    from .generations import GenerationService
    from .queue_worker import QueueWorker

logger = logging.getLogger(__name__)
PREPARATION_ACTIVE = ("preparing", "refining", "ready")
TEXT_ACTIVE = ("queued", "dispatching", "submitting", "running")


def text_summary(run: PromptGenerationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "prompt": run.prompt,
        "source_key": run.request_json["source_key"],
        "revision": run.request_json["revision"],
        "parameters": run.request_json["parameters"],
        "resolved_seeds": run.resolved_seeds_json,
        "compiled_graph_sha256": run.compiled_graph_sha256,
        "error": {"code": run.error_code, "message": run.error_message} if run.error_code else None,
    }


def preparation_summary(
    service: GenerationService, session: Session, rows: list[GenerationPreparation]
) -> dict[str, Any]:
    items = []
    for row in sorted(rows, key=lambda item: item.position):
        text_run = session.get(PromptGenerationRun, row.prompt_run_id)
        generation = session.get(Generation, row.generation_id) if row.generation_id else None
        items.append(
            {
                "id": row.id,
                "status": row.status,
                "prompt_run_id": row.prompt_run_id,
                "raw_prompt": text_run.prompt if text_run else None,
                "prompt": row.prompt,
                "generation": service.summary(session, generation).model_dump(mode="json")
                if generation
                else None,
                "error": {"code": row.error_code, "message": row.error_message}
                if row.error_code
                else None,
            }
        )
    return {"id": rows[0].group_id, "items": items}


def project_receipt(
    service: GenerationService, session: Session, receipt: GenerationSubmission
) -> dict[str, Any]:
    if receipt.endpoint == "prompt":
        run = session.get(PromptGenerationRun, receipt.outcomes[0]["prompt_run_id"])
        if not run or run.owner_id != receipt.owner_id:
            raise AppError(
                "submission_result_unavailable", "This prompt run is unavailable.", status_code=410
            )
        return text_summary(run)
    rows = [session.get(GenerationPreparation, item["preparation_id"]) for item in receipt.outcomes]
    if not rows or any(row is None or row.owner_id != receipt.owner_id for row in rows):
        raise AppError(
            "submission_result_unavailable", "This preparation is unavailable.", status_code=410
        )
    return preparation_summary(service, session, [row for row in rows if row is not None])


class PromptGenerationService:
    def __init__(self, container: AppContainer):
        self.container = container
        self._task: asyncio.Task[None] | None = None
        self._active: dict[str, asyncio.Task[None]] = {}
        self._batch_locks: dict[str, asyncio.Lock] = {}

    def validate_source(
        self, session: Session, payload: PromptGenerationCreate
    ) -> tuple[WorkflowProfile, Any]:
        profile = self.container.generations._profile_for_request(
            session, GenerationCreate(**payload.model_dump())
        )
        if publication_kind(profile.resolved_contract_json) != "text":
            raise AppError("source_kind_invalid", "Choose a text prompt source.", status_code=422)
        self.container.comfyui_instances.get(profile.instance_id or "default")
        compiled = self.container.compiler.compile(
            contract=profile.resolved_contract_json,
            api_document=profile.source_api_json,
            requested_controls=payload.parameters,
        )
        if compiled.selected_uploads:
            raise AppError(
                "prompt_input_unsupported",
                "Text generators with media inputs are not supported yet.",
                status_code=422,
            )
        return profile, compiled

    def create_text(
        self,
        session: Session,
        owner_id: str,
        payload: PromptGenerationCreate,
        *,
        automatic: bool = False,
    ) -> PromptGenerationRun:
        profile, compiled = self.validate_source(session, payload)
        graph_hash = adapt_seed(profile, compiled, self.container.compiler)
        run = PromptGenerationRun(
            owner_id=owner_id,
            profile_id=profile.id,
            instance_id=profile.instance_id or self.container.settings.comfyui_instance_id,
            automatic=automatic,
            queue_seq=self.container.generations._next_queue_sequence(session),
            request_json=payload.model_dump(mode="json"),
            contract_json=copy.deepcopy(profile.resolved_contract_json),
            compiled_graph_json=compiled.compiled_graph,
            compiled_graph_sha256=graph_hash,
            resolved_seeds_json=compiled.resolved_seeds,
        )
        session.add(run)
        session.flush()
        return run

    def capture_image(
        self,
        session: Session,
        owner_id: str,
        item: GenerationPreparationItem,
        *,
        profile: WorkflowProfile | None = None,
    ) -> tuple[WorkflowProfile, GenerationPreparationItem]:
        user = session.get(User, owner_id)
        if not user or user.state != UserState.ACTIVE:
            raise AppError("authentication_required", "Sign in is required.", status_code=401)
        service = self.container.generations
        profile = profile or service._profile_for_request(session, item.generation)
        if publication_kind(profile.resolved_contract_json) != "image":
            raise AppError("source_kind_invalid", "Choose an image source.", status_code=422)
        captured = item.model_copy(deep=True)
        runtime = service._instance_for_request(
            session, captured.generation, require_available=False
        )
        service._collection_for_owner(session, owner_id, captured.generation.collection_id)
        captured.generation.comfyui_instance_id = runtime.id
        prompt_id = next(
            i["id"]
            for i in profile.resolved_contract_json["inputs"]
            if i["semantic_role"] == "positive_prompt"
        )
        parameters = {
            **captured.generation.public_parameters,
            prompt_id: "Prompt preparation pending.",
        }
        captured.generation.parameters = parameters
        captured.generation.controls = None
        compiled = service._compile(
            session, user=user, profile=profile, request=captured.generation
        )
        parameters.update(compiled.resolved_seeds)
        if captured.assistant:
            captured.generation.prompt_assistant = PromptAssistantSnapshot(
                mode="refine",
                creative_direction=captured.assistant.creative_direction,
                thinking_enabled=captured.assistant.think,
                instructions=captured.assistant.instructions,
            )
        return profile, captured

    @staticmethod
    def _validate_shared_items(payload: GenerationPreparationCreate) -> None:
        """A group produces one prompt, so every item must request the same one."""
        first = payload.items[0]

        def digest(value: Any) -> str:
            return json.dumps(value, sort_keys=True, separators=(",", ":"))

        base_prompt = digest(first.prompt_generation.model_dump(mode="json"))
        base_assistant = (
            digest(first.assistant.model_dump(mode="json")) if first.assistant else None
        )
        for item in payload.items[1:]:
            if digest(item.prompt_generation.model_dump(mode="json")) != base_prompt:
                raise AppError(
                    "preparation_items_mismatch",
                    "All items in a batch must request the same prompt.",
                    status_code=422,
                )
            assistant = digest(item.assistant.model_dump(mode="json")) if item.assistant else None
            if assistant != base_assistant:
                raise AppError(
                    "preparation_items_mismatch",
                    "All items in a batch must use the same Creative Direction refinement.",
                    status_code=422,
                )

    def create_preparations(
        self,
        session: Session,
        owner_id: str,
        payload: GenerationPreparationCreate,
        *,
        cycle: AutoGenerationCycle | None = None,
        profile: WorkflowProfile | None = None,
    ) -> list[GenerationPreparation]:
        self._validate_shared_items(payload)
        group = str(uuid.uuid4())
        activity = begin_run(session, owner_id, len(payload.items))
        rows = []
        # One text run per group: every item, manual or automatic, shares the
        # prompt generated for it (at most one refinement when the leader has one).
        shared_text = self.create_text(
            session, owner_id, payload.items[0].prompt_generation, automatic=cycle is not None
        )
        automation = session.get(AutoGeneration, owner_id) if cycle else None
        compare_checkpoints = bool(automation and automation.snapshot_json.get("quantity") == 1)
        shared_image_seeds: dict[str, Any] = {}
        for position, item in enumerate(payload.items):
            image_profile, captured = self.capture_image(session, owner_id, item, profile=profile)
            if compare_checkpoints:
                if position == 0:
                    shared_image_seeds = {
                        entry["id"]: captured.generation.public_parameters[entry["id"]]
                        for entry in image_profile.resolved_contract_json["inputs"]
                        if entry["type"] == "seed"
                    }
                else:
                    captured.generation.parameters = {
                        **captured.generation.public_parameters,
                        **shared_image_seeds,
                    }
            row = GenerationPreparation(
                group_id=group,
                owner_id=owner_id,
                profile_id=image_profile.id,
                prompt_run_id=shared_text.id,
                auto_cycle_id=cycle.id if cycle else None,
                activity_run_id=activity.id,
                position=position,
                request_json=captured.model_dump(mode="json"),
            )
            session.add(row)
            rows.append(row)
        session.flush()
        return rows

    async def accept(
        self, owner_id: str, payload: PromptGenerationCreate | GenerationPreparationCreate, key: str
    ) -> dict[str, Any]:
        endpoint = "prompt" if isinstance(payload, PromptGenerationCreate) else "preparation"
        digest = hashlib.sha256(
            json.dumps(
                {"endpoint": endpoint, "payload": payload.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

        def transaction() -> dict[str, Any]:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                receipt = session.get(GenerationSubmission, (owner_id, key))
                if receipt:
                    if receipt.endpoint != endpoint or receipt.request_digest != digest:
                        raise AppError(
                            "idempotency_conflict",
                            "This submission ID belongs to a different request.",
                            status_code=409,
                        )
                    return project_receipt(self.container.generations, session, receipt)
                user = session.get(User, owner_id)
                if not user or user.state != UserState.ACTIVE:
                    raise AppError(
                        "authentication_required", "Sign in is required.", status_code=401
                    )
                if isinstance(payload, PromptGenerationCreate):
                    outcomes = [{"prompt_run_id": self.create_text(session, owner_id, payload).id}]
                else:
                    require_manual_generation(session, owner_id)
                    outcomes = [
                        {"preparation_id": row.id}
                        for row in self.create_preparations(session, owner_id, payload)
                    ]
                receipt = GenerationSubmission(
                    owner_id=owner_id,
                    key=key,
                    endpoint=endpoint,
                    request_digest=digest,
                    outcomes=outcomes,
                )
                session.add(receipt)
                result = project_receipt(self.container.generations, session, receipt)
                session.commit()
                return result

        result = await run_blocking(transaction)
        await notify_user(self.container.broker, owner_id, "prompt_generation.updated")
        return result

    def get(self, owner_id: str, identity: str, *, preparation: bool = False) -> dict[str, Any]:
        with self.container.db.session_factory() as session:
            if preparation:
                rows = list(
                    session.scalars(
                        select(GenerationPreparation).where(
                            GenerationPreparation.owner_id == owner_id,
                            GenerationPreparation.group_id == identity,
                        )
                    )
                )
                if rows:
                    return preparation_summary(self.container.generations, session, rows)
            else:
                run = session.get(PromptGenerationRun, identity)
                if run and run.owner_id == owner_id:
                    return text_summary(run)
        raise AppError("not_found", "The prompt request was not found.", status_code=404)

    def _text_state(self, identity: str, **values: Any) -> PromptGenerationRun | None:
        with self.container.db.session_factory() as session:
            run = session.get(PromptGenerationRun, identity)
            if run:
                for key, value in values.items():
                    setattr(run, key, value)
                session.commit()
                session.refresh(run)
                session.expunge(run)
            return run

    async def recover(self, worker: QueueWorker) -> None:
        await run_blocking(self._retire_legacy_batches)

        def load() -> list[PromptGenerationRun]:
            with self.container.db.session_factory() as session:
                return list(
                    session.scalars(
                        select(PromptGenerationRun).where(
                            PromptGenerationRun.status.in_(TEXT_ACTIVE)
                        )
                    )
                )

        for run in await run_blocking(load):
            if run.status == "dispatching":
                await run_blocking(self._text_state, run.id, status="queued")
            elif run.status == "submitting" and not run.comfyui_prompt_id:
                await run_blocking(
                    self._text_state,
                    run.id,
                    status="failed",
                    error_code="comfyui_submission_uncertain",
                    error_message=(
                        "ComfyUI acceptance could not be confirmed after restart. "
                        "Review before retrying."
                    ),
                )
            elif run.comfyui_prompt_id:
                worker._start_generation_task(
                    "text:" + run.id,
                    self.execute(run.id),
                    name="prompt-recovery-" + run.id,
                    instance_id=run.instance_id,
                )

    async def execute(self, identity: str) -> None:
        run = await run_blocking(self._text_state, identity)
        if not run or run.status not in TEXT_ACTIVE:
            return
        owner_id = run.owner_id
        compiled_graph = run.compiled_graph_json
        adapter = self.container.comfyui_instances.get(run.instance_id)
        try:
            if not run.comfyui_prompt_id:
                await run_blocking(self._text_state, identity, status="submitting")

                async def submit() -> PromptGenerationRun | None:
                    prompt_id = await adapter.submit_prompt(compiled_graph, "cif-text-" + identity)
                    return await run_blocking(
                        self._text_state, identity, status="running", comfyui_prompt_id=prompt_id
                    )

                task = asyncio.create_task(submit())
                try:
                    run = await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
                if not run:
                    return
            absent_since: float | None = None
            while True:
                current = await run_blocking(self._text_state, identity)
                if not current or current.status not in TEXT_ACTIVE:
                    return
                try:
                    history = await adapter.history(str(run.comfyui_prompt_id))
                    if history and history.get("status", {}).get("completed"):
                        if history.get("status", {}).get("status_str") != "success":
                            raise AppError(
                                "prompt_execution_failed", "ComfyUI prompt generation failed."
                            )
                        prompt = collect_text(run.contract_json, history)
                        await run_blocking(
                            self._text_state, identity, status="succeeded", prompt=prompt
                        )
                        break
                    if history and history.get("status", {}).get("status_str") == "error":
                        raise AppError(
                            "prompt_execution_failed", "ComfyUI prompt generation failed."
                        )
                    running, pending = _queue_prompt_ids(await adapter.queue())
                    if run.comfyui_prompt_id in running | pending:
                        absent_since = None
                    else:
                        absent_since = absent_since or time.monotonic()
                        if time.monotonic() - absent_since > max(
                            1, self.container.settings.reconciliation_grace_seconds
                        ):
                            raise AppError(
                                "prompt_result_missing",
                                "ComfyUI no longer has this prompt request or its result.",
                            )
                except (httpx.HTTPError, OSError):
                    absent_since = None
                await asyncio.sleep(max(0.1, self.container.settings.dispatch_poll_seconds))
        except httpx.ConnectError:
            await run_blocking(self._text_state, identity, status="queued")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await run_blocking(
                self._text_state,
                identity,
                status="failed",
                error_code=error.code if isinstance(error, AppError) else "prompt_execution_failed",
                error_message=error.message
                if isinstance(error, AppError) and error.code != "comfyui_prompt_rejected"
                else f"Prompt generation failed: {error.message}"
                if isinstance(error, AppError)
                else "Prompt generation failed.",
                internal_diagnostics_json=prompt_rejection_diagnostics(error.details)
                if isinstance(error, AppError) and error.code == "comfyui_prompt_rejected"
                else {"exception_type": type(error).__name__},
            )
        finally:
            await notify_user(self.container.broker, owner_id, "prompt_generation.updated")

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._coordinate(), name="prompt-preparation-coordinator"
            )

    async def stop(self) -> None:
        tasks = [*self._active.values(), *([self._task] if self._task else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._active.clear()
        self._task = None

    async def _coordinate(self) -> None:
        while True:
            try:
                for key, task in list(self._active.items()):
                    if task.done():
                        if not task.cancelled():
                            task.result()
                        del self._active[key]

                def pending() -> list[str]:
                    with self.container.db.session_factory() as session:
                        rows = session.scalars(
                            select(GenerationPreparation)
                            .where(GenerationPreparation.status.in_(PREPARATION_ACTIVE))
                            .order_by(
                                GenerationPreparation.created_at, GenerationPreparation.position
                            )
                            .limit(256)
                        ).all()
                        grouped: dict[str, list[GenerationPreparation]] = {}
                        for row in rows:
                            grouped.setdefault(row.group_id, []).append(row)
                        identities = []
                        for members in grouped.values():
                            if len({row.prompt_run_id for row in members}) == 1:
                                # One shared text run: advance the whole group at once.
                                identities.append(members[0].id)
                            else:
                                # Legacy manual batch predating shared prompts: per-row advance.
                                identities.extend(row.id for row in members)
                        return identities

                for key in await run_blocking(pending):
                    if len(self._active) >= 4:
                        break
                    if key not in self._active:
                        self._active[key] = asyncio.create_task(self.advance(key))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("prompt_preparation_coordinator_failed")
            await asyncio.sleep(0.25)

    @staticmethod
    def valid_cycle(session: Session, row: GenerationPreparation) -> bool:
        if not row.auto_cycle_id:
            return True
        cycle = session.get(AutoGenerationCycle, row.auto_cycle_id)
        auto = session.get(AutoGeneration, row.owner_id)
        return bool(
            cycle
            and auto
            and auto.enabled
            and auto.status != "blocked"
            and auto.revision == cycle.revision
            and cycle.state != "discarded"
        )

    async def advance(self, identity: str) -> None:
        def batch_identity() -> tuple[str, str] | None:
            with self.container.db.session_factory() as session:
                row = session.get(GenerationPreparation, identity)
                if not row:
                    return None
                # Homogeneity is a group property across every row: new batches
                # share one text run, legacy manual batches never do.
                rows = self._batch_rows(session, row.group_id)
                if len({member.prompt_run_id for member in rows}) == 1:
                    return row.owner_id, row.group_id
                return None

        batch = await run_blocking(batch_identity)
        if batch:
            owner, group = batch
            async with self._batch_locks.setdefault(owner, asyncio.Lock()):
                await self._advance_batch(group)
            return

        def load() -> tuple[GenerationPreparation, PromptGenerationRun] | None:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                row = session.get(GenerationPreparation, identity)
                if not row or row.status not in PREPARATION_ACTIVE:
                    return None
                if not self.valid_cycle(session, row):
                    self.fail_preparation(
                        session, row, "discarded", "Settings changed before image acceptance."
                    )
                    session.commit()
                    return None
                text = session.get(PromptGenerationRun, row.prompt_run_id)
                assert text is not None
                if (
                    text.status == "succeeded"
                    and row.request_json.get("assistant")
                    and not row.assistant_run_id
                ):
                    row.status = "refining"
                    session.commit()
                return row, text

        loaded = await run_blocking(load)
        if not loaded:
            return
        row, text = loaded
        if text.status in TEXT_ACTIVE:
            return
        try:
            if text.status != "succeeded":
                raise AppError(
                    text.error_code or "prompt_generation_failed",
                    text.error_message or "Prompt generation failed.",
                )
            payload = GenerationPreparationItem.model_validate(row.request_json)
            if payload.assistant and not row.assistant_run_id:
                assistant = payload.assistant.model_copy(update={"prompt": text.prompt})
                await compose_prompt(self.container, row.owner_id, assistant, preparation_id=row.id)

            def accept_image() -> dict[str, Any] | None:
                with self.container.db.session_factory() as session:
                    lock_user_state(session)
                    current = session.get(GenerationPreparation, identity)
                    if not current or current.status not in PREPARATION_ACTIVE:
                        return None
                    if not self.valid_cycle(session, current):
                        self.fail_preparation(
                            session,
                            current,
                            "discarded",
                            "Settings changed before image acceptance.",
                        )
                        session.commit()
                        return None
                    profile = session.get(WorkflowProfile, current.profile_id)
                    user = session.get(User, current.owner_id)
                    if not user or user.state != UserState.ACTIVE or not profile:
                        return None
                    prompt_id = next(
                        i["id"]
                        for i in profile.resolved_contract_json["inputs"]
                        if i["semantic_role"] == "positive_prompt"
                    )
                    prompt = current.prompt or text.prompt
                    request = payload.generation.model_copy(
                        update={
                            "parameters": {
                                **payload.generation.public_parameters,
                                prompt_id: prompt,
                            },
                            "prompt_assistant_run_id": current.assistant_run_id,
                        }
                    )
                    generation, event = self.container.generations._prepare_accept(
                        session, user=user, request=request, frozen_profile=profile
                    )
                    generation.auto_cycle_id = current.auto_cycle_id
                    current.status, current.generation_id, current.prompt = (
                        "accepted",
                        generation.id,
                        prompt,
                    )
                    session.add(
                        GenerationRunMember(
                            generation_id=generation.id, run_id=current.activity_run_id
                        )
                    )
                    if current.auto_cycle_id:
                        auto = session.get(AutoGeneration, current.owner_id)
                        assert auto is not None
                        auto.accepted_count += 1
                        auto.latest_prompt = prompt
                        auto.status = "generating"
                        limit = auto.snapshot_json.get("max_generations")
                        if limit is not None and auto.accepted_count >= limit:
                            auto.enabled, auto.status, auto.message = (
                                False,
                                "completed",
                                "Generation limit reached. The final jobs will finish.",
                            )
                    session.commit()
                    return event_payload(event)

            event = await run_blocking(accept_image)
            if event:
                await self.container.broker.publish(row.owner_id, event)
        except asyncio.CancelledError:
            raise
        except AppError as error:
            if error.code == "comfyui_instance_unavailable":
                return  # Keep the completed prompt while its image runtime reconnects.
            await run_blocking(self._fail, identity, error.code, error.message)
        except Exception:
            logger.exception("prompt_preparation_failed", extra={"preparation_id": identity})
            await run_blocking(
                self._fail, identity, "preparation_failed", "Image preparation failed."
            )
        await notify_user(self.container.broker, row.owner_id, "prompt_generation.updated")
        if row.auto_cycle_id:
            await notify_user(self.container.broker, row.owner_id, "auto_generation.updated")

    def _retire_legacy_batches(self) -> None:
        """Discard only unfinished automatic work with pre-batch prompt semantics."""
        with self.container.db.session_factory() as session:
            lock_user_state(session)
            groups = session.scalars(
                select(GenerationPreparation.group_id)
                .where(
                    GenerationPreparation.auto_cycle_id.is_not(None),
                    GenerationPreparation.status.in_(PREPARATION_ACTIVE),
                )
                .distinct()
            ).all()
            for group in groups:
                rows = self._batch_rows(session, group)
                text = session.get(PromptGenerationRun, rows[0].prompt_run_id)
                seed = (text.resolved_seeds_json if text else {}).get("stablellama.dataset_seed")
                if len({row.prompt_run_id for row in rows}) == 1 and (
                    seed is None or 0 <= int(seed) <= 2**31 - 1
                ):
                    continue
                for row in rows:
                    self.fail_preparation(
                        session, row, "discarded", "Automatic batch preparation was upgraded."
                    )
                cycle = session.get(AutoGenerationCycle, rows[0].auto_cycle_id)
                if cycle:
                    cycle.state = "discarded"
            session.commit()

    @staticmethod
    def _batch_rows(session: Session, group: str) -> list[GenerationPreparation]:
        return list(
            session.scalars(
                select(GenerationPreparation)
                .where(GenerationPreparation.group_id == group)
                .order_by(GenerationPreparation.position)
            )
        )

    async def _advance_batch(self, group: str) -> None:
        """Advance one batch that shares a single text run (manual or automatic)."""

        def load() -> tuple[GenerationPreparation, PromptGenerationRun, bool] | None:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                rows = self._batch_rows(session, group)
                if not rows or any(row.status not in PREPARATION_ACTIVE for row in rows):
                    return None
                leader = rows[0]
                if not self.valid_cycle(session, leader):
                    for row in rows:
                        self.fail_preparation(
                            session,
                            row,
                            "discarded",
                            "Automatic settings changed before image acceptance.",
                        )
                    session.commit()
                    return None
                text = session.get(PromptGenerationRun, leader.prompt_run_id)
                assert text is not None
                changed = False
                if text.status == "succeeded":
                    status = (
                        "refining"
                        if leader.request_json.get("assistant") and not leader.assistant_run_id
                        else "ready"
                    )
                    changed = any(row.status != status for row in rows)
                    for row in rows:
                        row.status = status
                    session.commit()
                return leader, text, changed

        loaded = await run_blocking(load)
        if not loaded:
            return
        leader, text, changed = loaded
        if text.status in TEXT_ACTIVE:
            return
        try:
            if text.status != "succeeded":
                raise AppError(
                    text.error_code or "prompt_generation_failed",
                    text.error_message or "Prompt generation failed.",
                )
            payload = GenerationPreparationItem.model_validate(leader.request_json)
            # Publish the raw prompt and durable phase before starting refinement.
            if changed:
                await notify_user(
                    self.container.broker, leader.owner_id, "prompt_generation.updated"
                )
                if leader.auto_cycle_id:
                    await notify_user(
                        self.container.broker, leader.owner_id, "auto_generation.updated"
                    )
            if payload.assistant and not leader.assistant_run_id:
                # compose_prompt durably saves on the leader before returning. A restart
                # reuses that result, and siblings never consume the provenance twice.
                await compose_prompt(
                    self.container,
                    leader.owner_id,
                    payload.assistant.model_copy(update={"prompt": text.prompt}),
                    preparation_id=leader.id,
                )
                # The saved output is visible even when image acceptance must wait.
                await notify_user(
                    self.container.broker, leader.owner_id, "prompt_generation.updated"
                )
                if leader.auto_cycle_id:
                    await notify_user(
                        self.container.broker, leader.owner_id, "auto_generation.updated"
                    )

            def accept_batch() -> list[dict[str, Any]]:
                with self.container.db.session_factory() as session:
                    lock_user_state(session)
                    rows = self._batch_rows(session, group)
                    if not rows or any(row.status not in PREPARATION_ACTIVE for row in rows):
                        return []
                    current = rows[0]
                    if not self.valid_cycle(session, current):
                        return []
                    # Manual batches have no AutoGeneration controller of their own.
                    auto = (
                        session.get(AutoGeneration, current.owner_id)
                        if current.auto_cycle_id
                        else None
                    )
                    user = session.get(User, current.owner_id)
                    if current.auto_cycle_id:
                        assert auto is not None
                    if not user or user.state != UserState.ACTIVE:
                        return []
                    prompt = current.prompt or text.prompt
                    events = []
                    for position, row in enumerate(rows):
                        profile = session.get(WorkflowProfile, row.profile_id)
                        if not profile:
                            raise AppError(
                                "source_unavailable", "The captured workflow is unavailable."
                            )
                        item = GenerationPreparationItem.model_validate(row.request_json)
                        prompt_id = next(
                            i["id"]
                            for i in profile.resolved_contract_json["inputs"]
                            if i["semantic_role"] == "positive_prompt"
                        )
                        request = item.generation.model_copy(
                            update={
                                "parameters": {
                                    **item.generation.public_parameters,
                                    prompt_id: prompt,
                                },
                                "prompt_assistant_run_id": current.assistant_run_id
                                if position == 0
                                else None,
                            }
                        )
                        generation, event = self.container.generations._prepare_accept(
                            session,
                            user=user,
                            request=request,
                            frozen_profile=profile,
                        )
                        if position == 0:
                            assistant_snapshot = generation.prompt_assistant_json
                        else:
                            generation.prompt_assistant_json = copy.deepcopy(assistant_snapshot)
                        generation.auto_cycle_id = row.auto_cycle_id
                        row.status, row.generation_id, row.prompt = (
                            "accepted",
                            generation.id,
                            prompt,
                        )
                        row.assistant_run_id = current.assistant_run_id
                        session.add(
                            GenerationRunMember(
                                generation_id=generation.id, run_id=row.activity_run_id
                            )
                        )
                        events.append(event_payload(event))
                    if auto is not None:
                        auto.accepted_count += len(events)
                        auto.latest_prompt, auto.status = prompt, "generating"
                        auto.error_code, auto.message, auto.failures = None, None, 0
                        maximum = auto.snapshot_json.get("max_generations")
                        if maximum is not None and auto.accepted_count >= maximum:
                            auto.enabled, auto.status = False, "completed"
                            auto.message = "Image queue limit reached. Accepted images will finish."
                    session.commit()
                    return events

            events = await run_blocking(accept_batch)
            for event in events:
                try:
                    await self.container.broker.publish(leader.owner_id, event)
                except Exception:
                    logger.exception("automatic_batch_notification_failed")
        except asyncio.CancelledError:
            raise
        except AppError as error:
            if error.code == "comfyui_instance_unavailable":
                return
            await run_blocking(self._fail, leader.id, error.code, error.message)
        except Exception:
            logger.exception("automatic_batch_preparation_failed")
            await run_blocking(
                self._fail, leader.id, "preparation_failed", "Image batch preparation failed."
            )
        await notify_user(self.container.broker, leader.owner_id, "prompt_generation.updated")
        if leader.auto_cycle_id:
            await notify_user(self.container.broker, leader.owner_id, "auto_generation.updated")

    @staticmethod
    def fail_preparation(
        session: Session, row: GenerationPreparation, code: str, message: str
    ) -> None:
        if row.status not in PREPARATION_ACTIVE:
            return
        row.status = "discarded" if code == "discarded" else "failed"
        row.error_code, row.error_message = code, message
        activity = session.get(GenerationRun, row.activity_run_id)
        if activity:
            activity.submission_failed_count += 1
        text = session.get(PromptGenerationRun, row.prompt_run_id)
        if text and text.status == "queued":
            text.status = "discarded"

    def _fail(self, identity: str, code: str, message: str) -> None:
        with self.container.db.session_factory() as session:
            lock_user_state(session)
            row = session.get(GenerationPreparation, identity)
            if row:
                # A shared text run is all-or-nothing: fail every row that
                # consumes it (the whole batch); legacy rows keep per-row scope.
                rows = [
                    member
                    for member in self._batch_rows(session, row.group_id)
                    if member.prompt_run_id == row.prompt_run_id
                ]
                for member in rows:
                    self.fail_preparation(session, member, code, message)
                if row.auto_cycle_id and self.valid_cycle(session, row):
                    auto = session.get(AutoGeneration, row.owner_id)
                    assert auto is not None
                    auto.status, auto.error_code, auto.message = "blocked", code, message
            session.commit()
