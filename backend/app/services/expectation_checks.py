"""Verify Creative Direction against expectations with a vision feedback loop.

A check composes a prompt, generates one probe image, has the vision model score
every expectation, and revises the prompt from that feedback until every
expectation reaches the pass score or the attempt allowance is exhausted. State
is durable: the coordinator resumes each step after a restart, and every step is
a short transaction with the external call (Ollama, ComfyUI) outside it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..blocking import run_blocking
from ..domain.expectations import (
    best_attempt,
    build_revision_direction,
    evaluation_from_json,
    first_attempt_direction,
)
from ..domain.publication import publication_kind
from ..errors import AppError
from ..models import (
    ACTIVE_STATUSES,
    Artifact,
    ExpectationCheck,
    ExpectationCheckAttempt,
    Generation,
    GenerationStatus,
    GenerationSubmission,
    ServiceHealth,
    User,
    UserState,
    utcnow,
)
from ..schemas import (
    ExpectationAttemptGeneration,
    ExpectationAttemptPublic,
    ExpectationCheckCreate,
    ExpectationCheckPublic,
    ExpectationCheckQueued,
    ExpectationResultPublic,
    ExpectationSnapshot,
    GenerationCreate,
    PromptAssistantSnapshot,
    PromptComposeRequest,
)
from .prompt_assistant import compose_prompt
from .prompt_rerun import target_inputs
from .submissions import accept_items, request_digest
from .user_state import lock_user_state, notify_user, require_manual_generation

if TYPE_CHECKING:
    from ..container import AppContainer
    from ..models import PromptAssistantRun
    from .assets import AssetStore

logger = logging.getLogger(__name__)

# GenerationSubmission.endpoint is a 16-character column.
ENDPOINT = "expectation"
EVENT = "expectation_check.updated"
ACTIVE = frozenset({"composing", "generating", "evaluating"})
TERMINAL = frozenset({"passed", "not_met", "failed", "stopped"})
RETRYABLE_CODES = frozenset(
    {
        "ollama_unavailable",
        "ollama_generate_unavailable",
        "ollama_generate_timeout",
        "ollama_generate_transport_error",
        "ollama_generate_incomplete",
        "comfyui_instance_unavailable",
    }
)
UNCHANGED_CODES = frozenset({"prompt_refinement_unchanged", "prompt_creation_unchanged"})
CANCELLED_STATUSES = frozenset(
    {GenerationStatus.CANCELLED_WITH_ARTIFACTS, GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS}
)
MAX_FAILURES = 5
MAX_CONCURRENT_STEPS = 4
VISION_IMAGE_MAX_EDGE = 1024
VISION_JPEG_QUALITY = 90


@dataclass
class _Step:
    kind: str
    check_id: str
    owner_id: str
    attempt_id: str = ""
    number: int = 0
    request: dict[str, Any] = field(default_factory=dict)
    previous_prompts: list[str] = field(default_factory=list)
    previous_evaluation: dict[str, Any] | None = None
    storage_path: str | None = None
    changed: bool = False


def encode_for_vision(
    assets: AssetStore, storage_path: str, max_edge: int = VISION_IMAGE_MAX_EDGE
) -> str:
    """Re-encode an artifact as a bounded JPEG ``data:`` URL.

    llama.cpp decodes PNG and JPEG but not WebP, and the router accepts inline
    ``data:`` URLs in native ``messages[].images``. Downscaling bounds the vision
    token cost; transparency is flattened onto white.
    """

    content = assets.read(storage_path)
    with Image.open(io.BytesIO(content)) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.getchannel("A"))
            image = flattened
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=VISION_JPEG_QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _attempts(session: Session, check_id: str) -> list[ExpectationCheckAttempt]:
    return list(
        session.scalars(
            select(ExpectationCheckAttempt)
            .where(ExpectationCheckAttempt.check_id == check_id)
            .order_by(ExpectationCheckAttempt.number)
        )
    )


def _snapshot(payload: ExpectationCheckCreate) -> dict[str, Any]:
    supplied = payload.items[0].prompt_assistant
    assistant = payload.assistant
    snapshot = supplied or PromptAssistantSnapshot(
        mode=assistant.mode,
        creative_direction=assistant.creative_direction,
        instructions=assistant.instructions,
        thinking_enabled=assistant.think,
    )
    snapshot = snapshot.model_copy(
        update={
            "expectations": ExpectationSnapshot(
                enabled=True,
                items=payload.expectations,
                threshold=payload.threshold,
                max_attempts=payload.max_attempts,
            )
        }
    )
    return snapshot.model_dump(mode="json")


class ExpectationCheckService:
    def __init__(self, container: AppContainer) -> None:
        self.container = container
        self._task: asyncio.Task[None] | None = None
        self._active: dict[str, asyncio.Task[None]] = {}
        self._wake: asyncio.Event | None = None

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self._task is None:
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(
                self._coordinate(), name="expectation-check-coordinator"
            )

    async def stop(self) -> None:
        tasks = [*self._active.values(), *([self._task] if self._task else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._active.clear()
        self._task = None

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def _startup_settled(self) -> bool:
        worker = getattr(self.container, "worker", None)
        automation = getattr(self.container, "automation", None)
        return bool(
            (worker is None or worker.health_snapshot()["ready"])
            and (automation is None or automation.health_snapshot()["ready"])
        )

    async def _coordinate(self) -> None:
        interval = max(0.25, float(self.container.settings.dispatch_poll_seconds))
        # Stay out of startup recovery: the worker and automation own the first database
        # passes, and checks resume only after both report ready.
        assert self._wake is not None
        while not self._startup_settled():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=0.1)
            # The first loop pass below polls immediately, so an early wake is not lost.
            self._wake.clear()
        while True:
            try:
                for key, task in list(self._active.items()):
                    if task.done():
                        del self._active[key]
                        if not task.cancelled() and task.exception() is not None:
                            logger.error(
                                "expectation_check_step_crashed",
                                exc_info=task.exception(),
                                extra={"expectation_check_id": key},
                            )
                for identity in await run_blocking(self._pending):
                    if len(self._active) >= MAX_CONCURRENT_STEPS:
                        break
                    if identity not in self._active:
                        self._active[identity] = asyncio.create_task(self.advance(identity))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("expectation_check_coordinator_failed")
            assert self._wake is not None
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            self._wake.clear()

    def _pending(self) -> list[str]:
        now = datetime.now(UTC)
        with self.container.db.session_factory() as session:
            rows = session.execute(
                select(ExpectationCheck.id, ExpectationCheck.next_retry_at)
                .join(User, User.id == ExpectationCheck.owner_id)
                .where(ExpectationCheck.status.in_(ACTIVE), User.state == UserState.ACTIVE)
                .order_by(ExpectationCheck.created_at, ExpectationCheck.id)
            ).all()
        ready = []
        for identity, retry_at in rows:
            if retry_at is not None and retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            if retry_at is None or retry_at <= now:
                ready.append(identity)
        return ready

    # ---------- acceptance ----------

    async def accept(
        self, owner_id: str, payload: ExpectationCheckCreate, key: str
    ) -> ExpectationCheckPublic:
        digest = request_digest(ENDPOINT, payload.model_dump(mode="json"))
        service = self.container.generations

        def transaction() -> ExpectationCheckPublic:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                receipt = session.get(GenerationSubmission, (owner_id, key))
                if receipt is not None:
                    if receipt.request_digest != digest or receipt.endpoint != ENDPOINT:
                        raise AppError(
                            "idempotency_conflict",
                            "This submission ID belongs to a different request.",
                            status_code=409,
                        )
                    return self.project_receipt(session, receipt)
                require_manual_generation(session, owner_id)
                user = session.get(User, owner_id)
                if user is None or user.state != UserState.ACTIVE:
                    raise AppError(
                        "authentication_required", "Sign in is required.", status_code=401
                    )
                if session.scalar(
                    select(ExpectationCheck.id).where(
                        ExpectationCheck.owner_id == owner_id,
                        ExpectationCheck.status.in_(ACTIVE),
                    )
                ):
                    raise AppError(
                        "expectation_check_active",
                        "An expectation check is already running. Stop it or wait for it to "
                        "finish before starting another.",
                        status_code=409,
                    )
                assistant = payload.assistant
                if assistant.mode == "refine" and not assistant.prompt.strip():
                    raise AppError(
                        "prompt_required",
                        "Refine mode requires a current prompt.",
                        fields={"prompt": "Enter a prompt first."},
                    )
                self._require_vision(session)
                probe = payload.items[0]
                profile = service._profile_for_request(session, probe)
                if publication_kind(profile.resolved_contract_json) != "image":
                    raise AppError(
                        "source_kind_invalid", "Choose an image generation source.", status_code=422
                    )
                prompt_id = target_inputs(profile.resolved_contract_json).prompt_id
                # Validate the probe exactly as Generate would, before anything is stored.
                service.validate(
                    session,
                    user=user,
                    request=probe.model_copy(
                        update={
                            "parameters": {
                                **probe.public_parameters,
                                prompt_id: assistant.prompt.strip() or "expectation check",
                            },
                            "controls": None,
                        }
                    ),
                )
                request_json = {
                    "assistant": assistant.model_dump(mode="json"),
                    "expectations": payload.expectations,
                    "threshold": payload.threshold,
                    "max_attempts": payload.max_attempts,
                    "items": [item.model_dump(mode="json") for item in payload.items],
                    "snapshot": _snapshot(payload),
                }
                check = ExpectationCheck(
                    owner_id=owner_id,
                    status="composing",
                    purpose=payload.purpose,
                    request_json=request_json,
                    collection_id=probe.collection_id,
                    queued_json={},
                )
                session.add(check)
                session.flush()
                verbatim = assistant.mode == "refine" and not assistant.creative_direction.strip()
                session.add(
                    ExpectationCheckAttempt(
                        check_id=check.id,
                        number=1,
                        # A blank Refine direction verifies the current prompt as written.
                        status="ready" if verbatim else "composing",
                        prompt=assistant.prompt.strip() if verbatim else None,
                        evaluation_json={},
                    )
                )
                receipt = GenerationSubmission(
                    owner_id=owner_id,
                    key=key,
                    endpoint=ENDPOINT,
                    request_digest=digest,
                    outcomes=[{"expectation_check_id": check.id}],
                )
                session.add(receipt)
                session.flush()
                result = self.project(session, check)
                session.commit()
                return result

        result = await run_blocking(transaction)
        await self._notify(owner_id)
        self.wake()
        return result

    @staticmethod
    def _require_vision(session: Session) -> None:
        health = session.get(ServiceHealth, "ollama")
        if not (health and health.available and (health.capabilities_json or {}).get("vision")):
            raise AppError(
                "vision_unavailable",
                "The Creative Direction model can't inspect images right now, so expectations "
                "can't be verified. Apply Creative Direction still works without the check.",
                status_code=503,
            )

    # ---------- reads ----------

    def project_receipt(
        self, session: Session, receipt: GenerationSubmission
    ) -> ExpectationCheckPublic:
        identity = receipt.outcomes[0].get("expectation_check_id") if receipt.outcomes else None
        check = session.get(ExpectationCheck, identity) if identity else None
        if check is None or check.owner_id != receipt.owner_id:
            raise AppError(
                "submission_result_unavailable",
                "This expectation check was accepted, but it is no longer available.",
                status_code=410,
            )
        return self.project(session, check)

    def get(self, owner_id: str, identity: str) -> ExpectationCheckPublic:
        with self.container.db.session_factory() as session:
            return self.project(session, self._owned(session, owner_id, identity))

    def latest(self, owner_id: str) -> ExpectationCheckPublic | None:
        with self.container.db.session_factory() as session:
            check = session.scalar(
                select(ExpectationCheck)
                .where(ExpectationCheck.owner_id == owner_id)
                .order_by(ExpectationCheck.created_at.desc(), ExpectationCheck.id.desc())
                .limit(1)
            )
            return self.project(session, check) if check else None

    @staticmethod
    def _owned(session: Session, owner_id: str, identity: str) -> ExpectationCheck:
        check = session.get(ExpectationCheck, identity)
        if check is None or check.owner_id != owner_id:
            raise AppError("not_found", "The expectation check was not found.", status_code=404)
        return check

    def project(self, session: Session, check: ExpectationCheck) -> ExpectationCheckPublic:
        request = check.request_json
        attempts = _attempts(session, check.id)
        generation_ids = [item.generation_id for item in attempts if item.generation_id]
        generations = (
            {
                row.id: row
                for row in session.scalars(
                    select(Generation).where(
                        Generation.id.in_(generation_ids), Generation.owner_id == check.owner_id
                    )
                )
            }
            if generation_ids
            else {}
        )
        public_attempts = []
        for item in attempts:
            generation = generations.get(item.generation_id or "")
            projected_generation = None
            if generation is not None and not generation.pending_delete:
                artifact: Artifact | None = self.container.generations._display_artifact(
                    session, generation
                )
                summary = (
                    self.container.generations.artifact_summary(artifact)
                    if artifact is not None and artifact.kind == "image"
                    else None
                )
                projected_generation = ExpectationAttemptGeneration(
                    id=generation.id,
                    status=generation.status.value,
                    thumbnail_url=(summary.thumbnail_url or summary.content_url)
                    if summary
                    else None,
                    content_url=summary.content_url if summary else None,
                )
            evaluation = evaluation_from_json(item.evaluation_json)
            public_attempts.append(
                ExpectationAttemptPublic(
                    number=item.number,
                    status=item.status,
                    prompt=item.prompt,
                    composition_id=item.composition_id,
                    generation=projected_generation,
                    score=item.score,
                    passed=evaluation.passed if evaluation else None,
                    results=[
                        ExpectationResultPublic(**result.as_json())
                        for result in (evaluation.results if evaluation else ())
                    ],
                    summary=evaluation.summary if evaluation else None,
                    error=(
                        {"code": item.error_code, "message": item.error_message or ""}
                        if item.error_code
                        else None
                    ),
                )
            )
        queued = check.queued_json or {}
        assistant = request.get("assistant", {})
        return ExpectationCheckPublic(
            id=check.id,
            status=check.status,
            purpose=check.purpose,  # type: ignore[arg-type]
            mode=assistant.get("mode", "refine"),
            expectations=list(request.get("expectations", [])),
            threshold=int(request.get("threshold", 80)),
            max_attempts=int(request.get("max_attempts", 5)),
            starting_prompt=str(assistant.get("prompt", "")),
            collection_id=check.collection_id,
            planned_count=len(request.get("items", [])),
            attempts=public_attempts,
            best_attempt=check.best_attempt,
            final_prompt=check.final_prompt,
            final_composition_id=check.final_composition_id,
            queued=ExpectationCheckQueued(
                generation_ids=list(queued.get("generation_ids", [])),
                errors=list(queued.get("errors", [])),
            ),
            error=(
                {"code": check.error_code, "message": check.error_message or ""}
                if check.error_code and check.status in TERMINAL
                else None
            ),
            created_at=check.created_at,
            updated_at=check.updated_at,
            completed_at=check.completed_at,
        )

    # ---------- stop ----------

    async def stop_check(self, owner_id: str, identity: str) -> ExpectationCheckPublic:
        def transaction() -> tuple[ExpectationCheckPublic, bool]:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                check = self._owned(session, owner_id, identity)
                changed = check.status in ACTIVE
                if changed:
                    self._stop_in_session(session, check)
                session.commit()
                return self.project(session, check), changed

        result, changed = await run_blocking(transaction)
        if changed:
            task = self._active.pop(identity, None)
            if task is not None:
                task.cancel()
            await self._notify(owner_id)
        return result

    @staticmethod
    def _stop_in_session(
        session: Session, check: ExpectationCheck, message: str | None = None
    ) -> None:
        check.status = "stopped"
        check.completed_at = utcnow()
        if message:
            check.error_code, check.error_message = "expectation_check_stopped", message
        for attempt in _attempts(session, check.id):
            if attempt.status not in {"passed", "not_met", "failed"}:
                attempt.status = "stopped"

    # ---------- steps ----------

    async def advance(self, identity: str) -> None:
        step = await run_blocking(self._load_step, identity)
        if step is None:
            return
        owner_id = step.owner_id
        try:
            if step.kind == "compose":
                await self._compose(step)
            elif step.kind == "probe":
                await self._probe(step)
            elif step.kind == "evaluate":
                await self._evaluate(step)
            elif not step.changed:
                return
        except asyncio.CancelledError:
            raise
        except AppError as error:
            await run_blocking(self._record_error, identity, error)
        except Exception:
            logger.exception(
                "expectation_check_step_failed", extra={"expectation_check_id": identity}
            )
            await run_blocking(
                self._record_error,
                identity,
                AppError("expectation_check_failed", "The expectation check failed unexpectedly."),
            )
        await self._notify(owner_id)

    def _waiting_on_probe(self, identity: str) -> bool:
        """Read-only fast path: a running probe needs no write lock to keep waiting."""

        with self.container.db.session_factory() as session:
            check = session.get(ExpectationCheck, identity)
            if check is None or check.status != "generating":
                return False
            attempt = session.scalar(
                select(ExpectationCheckAttempt)
                .where(ExpectationCheckAttempt.check_id == identity)
                .order_by(ExpectationCheckAttempt.number.desc())
                .limit(1)
            )
            if attempt is None or attempt.status != "generating" or not attempt.generation_id:
                return False
            status = session.scalar(
                select(Generation.status).where(Generation.id == attempt.generation_id)
            )
            return status in ACTIVE_STATUSES

    def _load_step(self, identity: str) -> _Step | None:
        if self._waiting_on_probe(identity):
            return None
        with self.container.db.session_factory() as session:
            lock_user_state(session)
            check = session.get(ExpectationCheck, identity)
            if check is None or check.status not in ACTIVE:
                return None
            base = _Step(kind="none", check_id=check.id, owner_id=check.owner_id)
            user = session.get(User, check.owner_id)
            if user is None or user.state != UserState.ACTIVE:
                self._stop_in_session(session, check, "The account is no longer active.")
                session.commit()
                base.changed = True
                return base
            attempts = _attempts(session, check.id)
            if not attempts:
                self._fail(check, None, "expectation_check_failed", "The check has no attempts.")
                session.commit()
                base.changed = True
                return base
            attempt = attempts[-1]
            base.attempt_id, base.number, base.request = (
                attempt.id,
                attempt.number,
                check.request_json,
            )
            if attempt.status == "composing":
                previous = attempts[:-1]
                base.kind = "compose"
                base.previous_prompts = [item.prompt for item in reversed(previous) if item.prompt]
                base.previous_evaluation = previous[-1].evaluation_json if previous else None
                if check.status != "composing":
                    check.status = "composing"
                    session.commit()
                return base
            if attempt.status == "ready":
                base.kind = "probe"
                return base
            if attempt.status == "generating":
                generation = (
                    session.get(Generation, attempt.generation_id)
                    if attempt.generation_id
                    else None
                )
                if generation is None or generation.pending_delete:
                    self._stop_in_session(
                        session, check, "The attempt image was cancelled or deleted."
                    )
                    session.commit()
                    base.changed = True
                    return base
                if generation.status in ACTIVE_STATUSES:
                    return None
                if generation.status in CANCELLED_STATUSES:
                    self._stop_in_session(session, check, "The attempt image was cancelled.")
                    session.commit()
                    base.changed = True
                    return base
                if generation.status != GenerationStatus.SUCCEEDED:
                    self._fail(
                        check,
                        attempt,
                        "expectation_check_image_failed",
                        generation.error_message or "The attempt image did not finish.",
                    )
                    session.commit()
                    base.changed = True
                    return base
                attempt.status, check.status = "evaluating", "evaluating"
                session.commit()
                base.changed = True
            if attempt.status == "evaluating":
                generation = (
                    session.get(Generation, attempt.generation_id)
                    if attempt.generation_id
                    else None
                )
                artifact = (
                    self.container.generations._display_artifact(session, generation)
                    if generation is not None
                    else None
                )
                if (
                    artifact is None
                    or artifact.kind != "image"
                    or artifact.owner_id != check.owner_id
                ):
                    self._fail(
                        check,
                        attempt,
                        "expectation_check_image_missing",
                        "The attempt image has no picture to inspect.",
                    )
                    session.commit()
                    base.changed = True
                    return base
                base.kind = "evaluate"
                base.storage_path = artifact.storage_path
                return base
            return None

    async def _compose(self, step: _Step) -> None:
        request = step.request
        assistant = PromptComposeRequest.model_validate(request["assistant"])
        expectations: list[str] = list(request["expectations"])
        if step.number == 1:
            compose_request = assistant.model_copy(
                update={
                    "creative_direction": first_attempt_direction(
                        assistant.creative_direction, expectations
                    )
                }
            )
            chain: Sequence[str] = ()
        else:
            evaluation = evaluation_from_json(step.previous_evaluation)
            if evaluation is None or not step.previous_prompts:
                raise AppError(
                    "expectation_check_failed", "The previous attempt has no saved evaluation."
                )
            compose_request = PromptComposeRequest(
                mode="refine",
                prompt=step.previous_prompts[0],
                creative_direction=build_revision_direction(
                    assistant.creative_direction, evaluation
                ),
                think=assistant.think,
                instructions=assistant.instructions if assistant.mode == "refine" else None,
            )
            chain = step.previous_prompts

        def link(session: Session, run: PromptAssistantRun) -> None:
            check = session.get(ExpectationCheck, step.check_id)
            attempt = session.get(ExpectationCheckAttempt, step.attempt_id)
            if (
                check is not None
                and attempt is not None
                and check.status in ACTIVE
                and attempt.status == "composing"
            ):
                attempt.prompt, attempt.composition_id = run.ollama_output, run.id
                attempt.status = "ready"
                check.failures, check.next_retry_at = 0, None

        try:
            await compose_prompt(
                self.container,
                step.owner_id,
                compose_request,
                chain_history=chain,
                link=link,
            )
        except AppError as error:
            if error.code in UNCHANGED_CODES and step.number > 1:
                await run_blocking(
                    self._finish_unchanged, step.check_id, step.attempt_id, error.message
                )
                return
            raise
        self.wake()

    async def _probe(self, step: _Step) -> None:
        service = self.container.generations

        def accept() -> list[dict[str, Any]]:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                check = session.get(ExpectationCheck, step.check_id)
                attempt = session.get(ExpectationCheckAttempt, step.attempt_id)
                if (
                    check is None
                    or attempt is None
                    or check.status not in ACTIVE
                    or attempt.status != "ready"
                    or not attempt.prompt
                ):
                    return []
                user = session.get(User, check.owner_id)
                if user is None or user.state != UserState.ACTIVE:
                    return []
                request = check.request_json
                probe = GenerationCreate.model_validate(request["items"][0])
                profile = service._profile_for_request(session, probe)
                prompt_id = target_inputs(profile.resolved_contract_json).prompt_id
                item = probe.model_copy(
                    update={
                        "parameters": {**probe.public_parameters, prompt_id: attempt.prompt},
                        "controls": None,
                        "prompt_assistant_run_id": attempt.composition_id,
                        "prompt_assistant": None,
                    }
                )
                outcomes, events = accept_items(service, session, user, [item], batch=False)
                generation = session.get(Generation, outcomes[0]["generation_id"])
                assert generation is not None
                generation.prompt_assistant_json = {
                    **request["snapshot"],
                    "composition_id": attempt.composition_id,
                    "expectation_check": {"id": check.id, "attempt": attempt.number},
                }
                attempt.generation_id, attempt.status = generation.id, "generating"
                check.status, check.failures, check.next_retry_at = "generating", 0, None
                session.commit()
                return events

        for event in await run_blocking(accept):
            await self._publish(step.owner_id, event)

    async def _evaluate(self, step: _Step) -> None:
        assert step.storage_path is not None
        request = step.request
        expectations: list[str] = list(request["expectations"])
        threshold = int(request["threshold"])
        image = await asyncio.to_thread(encode_for_vision, self.container.assets, step.storage_path)
        result = await self.container.ollama.evaluate_image(
            image_data_url=image,
            expectations=expectations,
            threshold=threshold,
            think=bool(request["assistant"].get("think", True)),
        )
        service = self.container.generations

        def finish() -> list[dict[str, Any]]:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                check = session.get(ExpectationCheck, step.check_id)
                attempt = session.get(ExpectationCheckAttempt, step.attempt_id)
                if (
                    check is None
                    or attempt is None
                    or check.status not in ACTIVE
                    or attempt.status != "evaluating"
                ):
                    return []
                evaluation = result.evaluation
                attempt.evaluation_json = {
                    **evaluation.as_json(),
                    "model": result.model,
                    "duration_ms": result.duration_ms,
                    "diagnostics": result.diagnostics,
                }
                attempt.score = evaluation.score
                check.failures, check.next_retry_at = 0, None
                events: list[dict[str, Any]] = []
                if evaluation.passed:
                    attempt.status = "passed"
                    check.status, check.completed_at = "passed", utcnow()
                    check.final_prompt = attempt.prompt
                    check.final_composition_id = attempt.composition_id
                    check.best_attempt = attempt.number
                    if check.purpose == "generate" and len(request["items"]) > 1:
                        events = self._queue_remaining(session, service, check, attempt)
                elif attempt.number >= int(request["max_attempts"]):
                    attempt.status = "not_met"
                    check.status, check.completed_at = "not_met", utcnow()
                    check.best_attempt = best_attempt(
                        (item.number, item.score)
                        for item in _attempts(session, check.id)
                        if item.score is not None
                    )
                else:
                    attempt.status = "not_met"
                    check.status = "composing"
                    session.add(
                        ExpectationCheckAttempt(
                            check_id=check.id,
                            number=attempt.number + 1,
                            status="composing",
                            evaluation_json={},
                        )
                    )
                session.commit()
                return events

        for event in await run_blocking(finish):
            await self._publish(step.owner_id, event)
        self.wake()

    @staticmethod
    def _queue_remaining(
        session: Session,
        service: Any,
        check: ExpectationCheck,
        attempt: ExpectationCheckAttempt,
    ) -> list[dict[str, Any]]:
        """Queue the rest of the batch with the qualified prompt; the probe is item one."""

        user = session.get(User, check.owner_id)
        if user is None:
            return []
        request = check.request_json
        remaining = [GenerationCreate.model_validate(item) for item in request["items"][1:]]
        profile = service._profile_for_request(session, remaining[0])
        prompt_id = target_inputs(profile.resolved_contract_json).prompt_id
        requests = [
            item.model_copy(
                update={
                    "parameters": {**item.public_parameters, prompt_id: attempt.prompt},
                    "controls": None,
                    "prompt_assistant_run_id": None,
                    "prompt_assistant": None,
                }
            )
            for item in remaining
        ]
        outcomes, events = accept_items(service, session, user, requests, batch=True)
        generation_ids = [item["generation_id"] for item in outcomes if "generation_id" in item]
        for generation_id in generation_ids:
            generation = session.get(Generation, generation_id)
            if generation is not None:
                generation.prompt_assistant_json = {
                    **request["snapshot"],
                    "composition_id": attempt.composition_id,
                    "expectation_check": {"id": check.id, "attempt": attempt.number},
                }
        check.queued_json = {
            "generation_ids": generation_ids,
            "errors": [item["error"] for item in outcomes if "error" in item],
        }
        return events

    # ---------- failures ----------

    @staticmethod
    def _fail(
        check: ExpectationCheck,
        attempt: ExpectationCheckAttempt | None,
        code: str,
        message: str,
    ) -> None:
        check.status, check.completed_at = "failed", utcnow()
        check.error_code, check.error_message = code, message
        if attempt is not None:
            attempt.status = "failed"
            attempt.error_code, attempt.error_message = code, message

    def _record_error(self, identity: str, error: AppError) -> None:
        with self.container.db.session_factory() as session:
            lock_user_state(session)
            check = session.get(ExpectationCheck, identity)
            if check is None or check.status not in ACTIVE:
                return
            attempts = _attempts(session, check.id)
            attempt = attempts[-1] if attempts else None
            if error.code in RETRYABLE_CODES and check.failures + 1 < MAX_FAILURES:
                check.failures += 1
                check.next_retry_at = utcnow() + timedelta(seconds=min(60, 2**check.failures))
                logger.info(
                    "expectation_check_retry_scheduled",
                    extra={
                        "expectation_check_id": identity,
                        "error_code": error.code,
                        "failures": check.failures,
                    },
                )
            else:
                logger.warning(
                    "expectation_check_failed",
                    extra={"expectation_check_id": identity, "error_code": error.code},
                )
                self._fail(check, attempt, error.code, error.message)
            session.commit()

    def _finish_unchanged(self, identity: str, attempt_id: str, message: str) -> None:
        """A revision that cannot change the prompt ends the check with its best attempt."""

        with self.container.db.session_factory() as session:
            lock_user_state(session)
            check = session.get(ExpectationCheck, identity)
            attempt = session.get(ExpectationCheckAttempt, attempt_id)
            if check is None or check.status not in ACTIVE:
                return
            if attempt is not None and attempt.status == "composing" and not attempt.prompt:
                session.delete(attempt)
                session.flush()
            check.status, check.completed_at = "not_met", utcnow()
            check.error_code = "prompt_refinement_unchanged"
            check.error_message = message
            check.best_attempt = best_attempt(
                (item.number, item.score)
                for item in _attempts(session, check.id)
                if item.score is not None
            )
            session.commit()

    # ---------- notifications ----------

    async def _notify(self, owner_id: str) -> None:
        try:
            await notify_user(self.container.broker, owner_id, EVENT)
        except Exception:
            logger.exception("expectation_check_notification_failed")

    async def _publish(self, owner_id: str, event: dict[str, Any]) -> None:
        try:
            await self.container.broker.publish(owner_id, event)
        except Exception:
            logger.exception("expectation_check_generation_notification_failed")


def active_check_exists(session: Session, owner_id: str) -> bool:
    return bool(
        session.scalar(
            select(ExpectationCheck.id).where(
                ExpectationCheck.owner_id == owner_id, ExpectationCheck.status.in_(ACTIVE)
            )
        )
    )


__all__ = [
    "ACTIVE",
    "ENDPOINT",
    "EVENT",
    "TERMINAL",
    "ExpectationCheckService",
    "active_check_exists",
    "encode_for_vision",
]
