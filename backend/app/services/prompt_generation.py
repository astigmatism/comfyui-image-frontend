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
from .comfyui import _queue_prompt_ids
from .events import event_payload
from .generation_activity import begin_run
from .prompt_assistant import compose_prompt
from .user_state import lock_user_state, notify_user

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

    def create_preparations(
        self,
        session: Session,
        owner_id: str,
        payload: GenerationPreparationCreate,
        *,
        cycle: AutoGenerationCycle | None = None,
        profile: WorkflowProfile | None = None,
    ) -> list[GenerationPreparation]:
        group = str(uuid.uuid4())
        activity = begin_run(session, owner_id, len(payload.items))
        rows = []
        for position, item in enumerate(payload.items):
            image_profile, captured = self.capture_image(session, owner_id, item, profile=profile)
            text_run = self.create_text(
                session, owner_id, item.prompt_generation, automatic=cycle is not None
            )
            row = GenerationPreparation(
                group_id=group,
                owner_id=owner_id,
                profile_id=image_profile.id,
                prompt_run_id=text_run.id,
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
                if isinstance(error, AppError)
                else "Prompt generation failed.",
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
                        return list(
                            session.scalars(
                                select(GenerationPreparation.id)
                                .where(GenerationPreparation.status.in_(PREPARATION_ACTIVE))
                                .order_by(
                                    GenerationPreparation.created_at, GenerationPreparation.position
                                )
                                .limit(256)
                            )
                        )

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
            and auto.revision == cycle.revision
            and cycle.state != "discarded"
        )

    async def advance(self, identity: str) -> None:
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
                self.fail_preparation(session, row, code, message)
                if row.auto_cycle_id and self.valid_cycle(session, row):
                    auto = session.get(AutoGeneration, row.owner_id)
                    assert auto is not None
                    auto.status, auto.error_code, auto.message = "blocked", code, message
            session.commit()
