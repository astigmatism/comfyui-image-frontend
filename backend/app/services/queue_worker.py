from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Callable, Coroutine, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..domain.publication import sha256_json
from ..domain.results import (
    NativeFileOutput,
    NormalizedHistory,
    history_status_indicates_interruption,
    normalize_history,
)
from ..errors import AppError
from ..models import (
    TERMINAL_STATUSES,
    Artifact,
    ArtifactState,
    Generation,
    GenerationEvent,
    GenerationStatus,
    GenerationUpload,
    SchedulerState,
    ServiceHealth,
    Upload,
    WorkflowProfile,
)
from .assets import AssetStore, StoredImage
from .comfyui import ComfyUIAdapter
from .event_broker import EventBroker
from .events import add_generation_event, event_payload, publish_event
from .generations import GenerationService
from .ollama import OllamaAdapter

logger = logging.getLogger(__name__)

DispatcherState = Literal[
    "not_started",
    "recovering",
    "running",
    "backing_off",
    "stopping",
    "stopped",
    "failed",
]
RecoveryNotification = tuple[str, dict[str, Any]]
RecoveryPlan = tuple[tuple[RecoveryNotification, ...], tuple[tuple[str, str], ...]]


async def _run_blocking[T](operation: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Finish a thread-owned database operation before propagating cancellation."""

    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


class QueueWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session],
        comfyui: ComfyUIAdapter,
        ollama: OllamaAdapter,
        assets: AssetStore,
        broker: EventBroker,
        generations: GenerationService,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.comfyui = comfyui
        self.ollama = ollama
        self.assets = assets
        self.broker = broker
        self.generations = generations
        self._stop = asyncio.Event()
        self._main_task: asyncio.Task[None] | None = None
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._active: dict[str, asyncio.Task[None]] = {}
        self._dispatcher_started = asyncio.Event()
        self._dispatcher_state: DispatcherState = "not_started"
        self._dispatcher_done = False
        self._last_heartbeat_monotonic: float | None = None
        self._last_heartbeat_at: datetime | None = None
        self._last_failure_at: datetime | None = None
        self._last_exception_class: str | None = None
        self._consecutive_failures = 0
        self._restart_count = 0

    async def start(self) -> None:
        if self._main_task is not None:
            if not self._main_task.done():
                return
            raise RuntimeError("generation dispatcher supervisor is not running")
        self._stop.clear()
        self._dispatcher_started.clear()
        self._dispatcher_state = "recovering"
        self._dispatcher_done = False
        self._health_task = asyncio.create_task(self._health_loop(), name="external-health-monitor")
        self._main_task = asyncio.create_task(
            self._supervise_dispatcher(),
            name="generation-queue-supervisor",
        )
        self._main_task.add_done_callback(self._observe_supervisor_done)
        await asyncio.sleep(0)
        if self._main_task.done():
            await self.stop()
            raise RuntimeError("generation dispatcher supervisor stopped during startup")

    async def stop(self) -> None:
        self._stop.set()
        if self._main_task is not None or self._dispatcher_task is not None:
            self._dispatcher_state = "stopping"
        tasks = [
            task for task in (self._main_task, self._dispatcher_task, self._health_task) if task
        ]
        active_tasks = list(self._active.values())
        for task in tasks:
            task.cancel()
        for task in active_tasks:
            task.cancel()
        await asyncio.gather(*tasks, *active_tasks, return_exceptions=True)
        self._active.clear()
        self._main_task = None
        self._dispatcher_task = None
        self._health_task = None
        self._dispatcher_state = "stopped"
        logger.info("generation_dispatcher_stopped")

    async def _supervise_dispatcher(self) -> None:
        while not self._stop.is_set():
            self._dispatcher_state = "recovering"
            self._mark_dispatcher_heartbeat()
            try:
                await self._reconcile_startup()
                self._consecutive_failures = 0
                break
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self._record_dispatcher_failure(exc)
                backoff = self._dispatcher_backoff(self._consecutive_failures)
                self._dispatcher_state = "backing_off"
                logger.error(
                    "generation_startup_recovery_failed",
                    extra={
                        "consecutive_failures": self._consecutive_failures,
                        "backoff_seconds": backoff,
                        "exception_class": type(exc).__name__,
                    },
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                if await self._wait_for_stop_or_backoff(backoff):
                    return
        if self._stop.is_set():
            return
        while not self._stop.is_set():
            task = asyncio.create_task(
                self._run_dispatcher(),
                name="generation-queue-worker",
            )
            self._dispatcher_task = task
            self._dispatcher_done = False
            self._dispatcher_state = "running"
            self._mark_dispatcher_heartbeat()
            self._dispatcher_started.set()
            logger.info(
                "generation_dispatcher_started",
                extra={"restart_count": self._restart_count},
            )
            unexpected: BaseException | None = None
            try:
                await task
                if not self._stop.is_set():
                    unexpected = RuntimeError("dispatcher task returned unexpectedly")
            except asyncio.CancelledError as exc:
                supervisor = asyncio.current_task()
                if self._stop.is_set() or (supervisor is not None and supervisor.cancelling()):
                    raise
                unexpected = exc
            except BaseException as exc:
                unexpected = exc
            finally:
                self._dispatcher_done = task.done()
                if self._dispatcher_task is task:
                    self._dispatcher_task = None

            if unexpected is None:
                break
            self._record_dispatcher_failure(unexpected)
            backoff = self._dispatcher_backoff(self._consecutive_failures)
            self._dispatcher_state = "backing_off"
            logger.error(
                "generation_dispatcher_unexpected_completion",
                extra={
                    "consecutive_failures": self._consecutive_failures,
                    "backoff_seconds": backoff,
                    "exception_class": self._last_exception_class,
                    "restart_count": self._restart_count,
                },
                exc_info=(type(unexpected), unexpected, unexpected.__traceback__),
            )
            if await self._wait_for_stop_or_backoff(backoff):
                break
            self._restart_count += 1

    async def _run_dispatcher(self) -> None:
        while not self._stop.is_set():
            try:
                await self._dispatch_iteration()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_dispatcher_failure(exc)
                backoff = self._dispatcher_backoff(self._consecutive_failures)
                self._dispatcher_state = "backing_off"
                logger.exception(
                    "generation_dispatcher_iteration_failed",
                    extra={
                        "consecutive_failures": self._consecutive_failures,
                        "backoff_seconds": backoff,
                        "exception_class": self._last_exception_class,
                        "restart_count": self._restart_count,
                    },
                )
                logger.info(
                    "generation_dispatcher_backoff_started",
                    extra={
                        "consecutive_failures": self._consecutive_failures,
                        "backoff_seconds": backoff,
                    },
                )
                if await self._wait_for_stop_or_backoff(backoff):
                    return
                continue
            if self._consecutive_failures:
                logger.info(
                    "generation_dispatcher_recovered",
                    extra={
                        "consecutive_failures": self._consecutive_failures,
                        "restart_count": self._restart_count,
                    },
                )
            self._consecutive_failures = 0
            self._dispatcher_state = "running"
            self._mark_dispatcher_heartbeat()
            if await self._wait_for_stop_or_backoff(self.settings.dispatch_poll_seconds):
                return

    async def _dispatch_iteration(self) -> None:
        self._reap_active_tasks()
        available_slots = self.settings.comfyui_concurrency - len(self._active)
        if available_slots <= 0 or not self._comfyui_available():
            return
        for _ in range(available_slots):
            claim = self._claim_next()
            if claim is None:
                break
            generation_id, event = claim
            execution = self._execute(generation_id)
            try:
                self._start_generation_task(
                    generation_id,
                    execution,
                    name=f"generation-{generation_id}",
                )
            except BaseException:
                await self._requeue_unstarted_claim(generation_id)
                raise
            await self._publish_event_best_effort(event, generation_id=generation_id)

    def _start_generation_task(
        self,
        generation_id: str,
        coroutine: Coroutine[Any, Any, None],
        *,
        name: str,
    ) -> None:
        existing = self._active.get(generation_id)
        if existing is not None:
            if not existing.done():
                # Startup reconciliation is retryable. A later attempt may rediscover a prompt
                # whose monitor was already started by an earlier partial attempt; retain the
                # live monitor and close the duplicate coroutine rather than losing its handle.
                coroutine.close()
                return
            self._generation_task_done(generation_id, existing)
        try:
            task = asyncio.create_task(coroutine, name=name)
        except BaseException:
            coroutine.close()
            raise
        self._active[generation_id] = task

        def task_done(completed: asyncio.Task[None]) -> None:
            self._generation_task_done(generation_id, completed)

        task.add_done_callback(task_done)

    def _generation_task_done(
        self,
        generation_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._active.get(generation_id) is not task:
            return
        self._active.pop(generation_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("generation_task_failed", extra={"generation_id": generation_id})

    def _reap_active_tasks(self) -> None:
        for generation_id, task in list(self._active.items()):
            if task.done():
                self._generation_task_done(generation_id, task)

    async def _requeue_unstarted_claim(self, generation_id: str) -> None:
        event = None
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if (
                generation is not None
                and generation.status == GenerationStatus.DISPATCHING
                and not generation.comfyui_prompt_id
            ):
                generation.status = GenerationStatus.QUEUED
                event = add_generation_event(
                    session,
                    generation,
                    "generation.requeued",
                    {"reason": "Dispatch task could not be scheduled."},
                )
                session.commit()
        if event is not None:
            await self._publish_event_best_effort(event, generation_id=generation_id)

    def _record_dispatcher_failure(self, exc: BaseException) -> None:
        self._consecutive_failures += 1
        self._last_failure_at = datetime.now(UTC)
        self._last_exception_class = type(exc).__name__
        self._dispatcher_state = "failed"

    def _dispatcher_backoff(self, consecutive_failures: int) -> float:
        base = float(max(self.settings.dispatch_poll_seconds, 0.1))
        exponent = max(0, min(consecutive_failures - 1, 8))
        return float(min(base * (2**exponent), 5.0))

    async def _wait_for_stop_or_backoff(self, delay: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True

    def _mark_dispatcher_heartbeat(self) -> None:
        self._last_heartbeat_monotonic = time.monotonic()
        self._last_heartbeat_at = datetime.now(UTC)

    def health_snapshot(self) -> dict[str, Any]:
        enabled = self.settings.enable_background_worker
        supervisor_running = bool(self._main_task and not self._main_task.done())
        dispatcher_running = bool(
            supervisor_running and self._dispatcher_task and not self._dispatcher_task.done()
        )
        heartbeat_age = (
            time.monotonic() - self._last_heartbeat_monotonic
            if self._last_heartbeat_monotonic is not None
            else None
        )
        heartbeat_fresh = bool(
            dispatcher_running
            and heartbeat_age is not None
            and heartbeat_age <= self.settings.dispatcher_heartbeat_stale_seconds
        )
        ready = not enabled or (
            supervisor_running
            and (
                self._dispatcher_state == "recovering"
                or (dispatcher_running and heartbeat_fresh and self._dispatcher_state == "running")
            )
        )
        return {
            "enabled": enabled,
            "ready": ready,
            "dispatcher_running": dispatcher_running,
            "dispatcher_done": self._dispatcher_done,
            "heartbeat_fresh": heartbeat_fresh,
            "state": self._dispatcher_state,
            "last_heartbeat_at": (
                self._last_heartbeat_at.isoformat() if self._last_heartbeat_at else None
            ),
            "last_failure_at": (
                self._last_failure_at.isoformat() if self._last_failure_at else None
            ),
            "consecutive_failures": self._consecutive_failures,
            "last_exception_class": self._last_exception_class,
            "restart_count": self._restart_count,
        }

    def _observe_supervisor_done(self, task: asyncio.Task[None]) -> None:
        if self._stop.is_set():
            with suppress(asyncio.CancelledError, Exception):
                task.result()
            return
        try:
            task.result()
        except asyncio.CancelledError as exc:
            self._record_dispatcher_failure(exc)
            logger.error(
                "generation_dispatcher_supervisor_cancelled",
                extra={"exception_class": type(exc).__name__},
            )
        except BaseException as exc:
            self._record_dispatcher_failure(exc)
            logger.error(
                "generation_dispatcher_supervisor_failed",
                extra={"exception_class": type(exc).__name__},
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        else:
            unexpected_exit = RuntimeError("dispatcher supervisor returned unexpectedly")
            self._record_dispatcher_failure(unexpected_exit)
            logger.error(
                "generation_dispatcher_supervisor_completed",
                extra={"exception_class": type(unexpected_exit).__name__},
            )

    async def _publish_event_best_effort(
        self,
        event: Any,
        *,
        generation_id: str | None = None,
    ) -> None:
        try:
            await publish_event(self.broker, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "generation_event_notification_failed",
                extra={
                    "generation_id": generation_id or getattr(event, "generation_id", None),
                    "event_type": getattr(event, "event_type", None),
                    "exception_class": type(exc).__name__,
                },
            )

    async def _publish_broker_best_effort(
        self,
        owner_id: str,
        payload: dict[str, Any],
        *,
        generation_id: str,
    ) -> None:
        try:
            await self.broker.publish(owner_id, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "generation_event_notification_failed",
                extra={
                    "generation_id": generation_id,
                    "event_type": payload.get("type"),
                    "exception_class": type(exc).__name__,
                },
            )

    def _comfyui_available(self) -> bool:
        with self.session_factory() as session:
            health = session.get(ServiceHealth, "comfyui")
            return bool(health and health.available)

    def _claim_next(self) -> tuple[str, Any] | None:
        with self.session_factory() as session:
            rows = session.execute(
                select(Generation.owner_id, func.min(Generation.queue_seq).label("first_seq"))
                .where(Generation.status == GenerationStatus.QUEUED)
                .group_by(Generation.owner_id)
                .order_by("first_seq", Generation.owner_id)
            ).all()
            if not rows:
                return None
            owner_ids = [str(row.owner_id) for row in rows]
            state = session.get(SchedulerState, "round_robin")
            if state is None:
                state = SchedulerState(key="round_robin")
                session.add(state)
                session.flush()
            if state.last_user_id in owner_ids:
                start = (owner_ids.index(state.last_user_id) + 1) % len(owner_ids)
                owner_id = owner_ids[start]
            else:
                owner_id = owner_ids[0]
            generation = session.scalar(
                select(Generation)
                .where(
                    Generation.owner_id == owner_id,
                    Generation.status == GenerationStatus.QUEUED,
                )
                .order_by(Generation.queue_seq)
                .limit(1)
            )
            if generation is None:
                return None
            generation.status = GenerationStatus.DISPATCHING
            state.last_user_id = owner_id
            event = add_generation_event(
                session,
                generation,
                "generation.dispatching",
                {"status": GenerationStatus.DISPATCHING.value},
            )
            session.commit()
            return generation.id, event

    async def _execute(self, generation_id: str) -> None:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            if generation.status == GenerationStatus.CANCEL_REQUESTED:
                await self._finish_without_execution(generation_id, cancelled=True)
                return
            graph = copy.deepcopy(generation.compiled_graph_json)
            profile = session.get(WorkflowProfile, generation.workflow_profile_id)
            attach_workflow = bool(
                generation.resolved_contract_json.get("runtime", {}).get(
                    "attach_workflow_as_extra_pnginfo", False
                )
            )
            editable_workflow = copy.deepcopy(profile.source_ui_json) if profile else None
        try:
            materialized = await self._materialize_uploads(generation_id, graph)
            with self.session_factory() as session:
                generation = session.get(Generation, generation_id)
                if generation is None:
                    return
                if generation.status == GenerationStatus.CANCEL_REQUESTED:
                    await self._finish_without_execution(generation_id, cancelled=True)
                    return
                generation.submitted_graph_json = materialized
                generation.submitted_graph_sha256 = sha256_json(materialized)
                session.commit()
            extra_data = None
            if attach_workflow:
                if editable_workflow is None:
                    raise RuntimeError(
                        "accepted source revision is missing editable workflow metadata"
                    )
                extra_data = {"extra_pnginfo": {"workflow": editable_workflow}}
        except (httpx.TransportError, OSError):
            # No prompt submission has started yet, so retrying after connectivity returns is
            # unambiguous and cannot duplicate native work.
            await self._requeue_after_outage(generation_id)
            return
        except Exception as exc:
            await self._fail_before_start(generation_id, exc)
            return

        try:
            submission_task = asyncio.create_task(
                self._submit_and_mark_running(
                    generation_id,
                    materialized,
                    generation.comfyui_client_id,
                    extra_data=extra_data,
                ),
                name=f"generation-submit-{generation_id}",
            )
            try:
                prompt_id, event = await asyncio.shield(submission_task)
            except asyncio.CancelledError:
                # A remote submission and its durable prompt-ID commit are one critical
                # section. Let that bounded operation settle before shutdown continues so a
                # restart never requeues a prompt that ComfyUI may already have accepted.
                try:
                    await submission_task
                except httpx.ConnectError:
                    await self._requeue_after_outage(generation_id)
                except (httpx.TransportError, OSError) as exc:
                    await self._fail_ambiguous_submission(generation_id, exc)
                except Exception as exc:
                    await self._fail_before_start(generation_id, exc)
                raise
        except httpx.ConnectError:
            await self._requeue_after_outage(generation_id)
            return
        except (httpx.TransportError, OSError) as exc:
            # A response/read/write failure can occur after ComfyUI accepted the prompt. Since
            # the native API has no idempotency key, never requeue this ambiguous submission.
            await self._fail_ambiguous_submission(generation_id, exc)
            return
        except Exception as exc:
            await self._fail_before_start(generation_id, exc)
            return

        if event is not None:
            await self._publish_event_best_effort(event, generation_id=generation_id)
        await self._monitor(generation_id, prompt_id)

    async def _submit_and_mark_running(
        self,
        generation_id: str,
        materialized: dict[str, Any],
        client_id: str,
        *,
        extra_data: dict[str, Any] | None,
    ) -> tuple[str, Any | None]:
        prompt_id = await self.comfyui.submit_prompt(
            materialized,
            client_id,
            extra_data=extra_data,
        )
        cancel_prompt = False
        event = None
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                raise RuntimeError("generation disappeared after ComfyUI submission")
            generation.comfyui_prompt_id = prompt_id
            generation.dispatched_at = datetime.now(UTC)
            if generation.status == GenerationStatus.CANCEL_REQUESTED:
                cancel_prompt = True
            else:
                generation.status = GenerationStatus.RUNNING
                generation.started_at = datetime.now(UTC)
                event = add_generation_event(
                    session,
                    generation,
                    "generation.running",
                    {"status": GenerationStatus.RUNNING.value},
                )
            session.commit()
        if cancel_prompt:
            with suppress(Exception):
                await self.comfyui.cancel(prompt_id, running=True)
        return prompt_id, event

    async def _fail_ambiguous_submission(self, generation_id: str, exc: Exception) -> None:
        await self._fail_before_start(
            generation_id,
            AppError(
                "comfyui_submission_uncertain",
                "ComfyUI did not confirm whether the workflow request was accepted.",
                status_code=503,
                details={"transport": type(exc).__name__},
            ),
        )

    async def _materialize_uploads(
        self, generation_id: str, graph: dict[str, Any]
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                raise RuntimeError("generation disappeared")
            links = {
                link.upload_id: session.get(Upload, link.upload_id)
                for link in session.scalars(
                    select(GenerationUpload).where(GenerationUpload.generation_id == generation_id)
                )
            }
        cache: dict[str, str] = {}

        async def replace(value: Any) -> Any:
            if isinstance(value, dict) and isinstance(value.get("__app_upload_id__"), str):
                upload_id = value["__app_upload_id__"]
                upload = links.get(upload_id)
                if upload is None:
                    raise RuntimeError("referenced upload is unavailable")
                if upload_id not in cache:
                    content = await asyncio.to_thread(self.assets.read, upload.storage_path)
                    extension = {
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/webp": ".webp",
                    }.get(upload.mime_type, ".png")
                    cache[upload_id] = await self.comfyui.upload_image(
                        content,
                        f"{upload.id}{extension}",
                        kind=upload.kind.value,
                        mime_type=upload.mime_type,
                        subfolder=f"comfyui-image-frontend/{generation_id}",
                    )
                return cache[upload_id]
            if isinstance(value, dict):
                return {key: await replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [await replace(item) for item in value]
            return value

        materialized = await replace(graph)
        if not isinstance(materialized, dict):
            raise RuntimeError("compiled graph materialization returned an invalid value")
        return materialized

    async def _monitor(self, generation_id: str, prompt_id: str) -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)

        async def websocket_pump() -> None:
            try:
                with self.session_factory() as session:
                    generation = session.get(Generation, generation_id)
                    if generation is None:
                        return
                    client_id = generation.comfyui_client_id
                async for event in self.comfyui.events(client_id):
                    data = event.get("data", {}) if isinstance(event, Mapping) else {}
                    event_prompt = data.get("prompt_id") if isinstance(data, Mapping) else None
                    if event_prompt in {None, prompt_id}:
                        await queue.put(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.info(
                    "comfyui_websocket_disconnected", extra={"generation_id": generation_id}
                )

        pump = asyncio.create_task(websocket_pump(), name=f"comfy-ws-{generation_id}")
        reconciliation_requested = False
        unknown_reachable_since: float | None = None
        latest_history: dict[str, Any] | None = None
        try:
            while not self._stop.is_set():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.75)
                    if await self._process_runtime_event(generation_id, prompt_id, event):
                        # WebSocket completion/error/cache messages are only wake-up hints.
                        # Native history remains authoritative for every terminal outcome.
                        reconciliation_requested = True
                except TimeoutError:
                    pass
                await self._ensure_cancel_sent(generation_id, prompt_id)
                history = None
                try:
                    history = await self.comfyui.history(prompt_id)
                except AppError as exc:
                    await self._record_reconciliation_error(generation_id, exc)
                    reconciliation_requested = True
                except (httpx.HTTPError, OSError):
                    history = None
                if history is not None:
                    latest_history = history
                terminal = _history_terminal(history)
                if terminal:
                    await self._finalize(
                        generation_id,
                        history=history or {},
                        outcome=terminal,
                    )
                    return

                if reconciliation_requested:
                    reconciled = await self._wait_for_history(
                        prompt_id,
                        generation_id=generation_id,
                        initial_history=latest_history,
                    )
                    if reconciled is not None:
                        latest_history = reconciled
                    terminal = _history_terminal(latest_history)
                    reconciliation_requested = False
                    if terminal:
                        await self._finalize(
                            generation_id,
                            history=latest_history or {},
                            outcome=terminal,
                        )
                        return

                # A non-terminal history snapshot can outlive the actual prompt after an
                # external interruption or ComfyUI reset. Always reconcile it against the live
                # queue so an orphaned prompt cannot retain an application concurrency slot.
                try:
                    queue_state = await self.comfyui.queue()
                    present = prompt_id in _collect_prompt_ids(queue_state)
                except AppError as exc:
                    await self._record_reconciliation_error(generation_id, exc)
                    present = True
                    unknown_reachable_since = None
                except (httpx.HTTPError, OSError):
                    present = True
                    unknown_reachable_since = None
                if present:
                    unknown_reachable_since = None
                elif unknown_reachable_since is None:
                    unknown_reachable_since = time.monotonic()
                elif (
                    time.monotonic() - unknown_reachable_since
                    >= self.settings.reconciliation_grace_seconds
                ):
                    # A prompt that was previously visible may have just left the queue before
                    # its terminal history entry became readable. Give durable history one
                    # bounded retry window and retain any latest partial entry.
                    reconciled = await self._wait_for_history(
                        prompt_id,
                        generation_id=generation_id,
                        initial_history=latest_history,
                    )
                    if reconciled is not None:
                        latest_history = reconciled
                    terminal = _history_terminal(latest_history)
                    await self._finalize(
                        generation_id,
                        history=latest_history or {},
                        outcome=terminal or "interrupted",
                    )
                    return
                if pump.done():
                    # A transient WebSocket outage must not lose the durable result.
                    await asyncio.sleep(0.5)
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)

    async def _process_runtime_event(
        self, generation_id: str, prompt_id: str, event: Mapping[str, Any]
    ) -> bool:
        event_type = event.get("type")
        data = event.get("data", {})
        if not isinstance(data, Mapping):
            data = {}
        node_id = data.get("node")
        if event_type == "executing" and node_id is not None:
            await self._update_stage(generation_id, str(node_id))
        elif event_type == "progress":
            await self._record_progress(generation_id, data)
        elif event_type == "executed" and node_id is not None:
            output = data.get("output", {})
            if isinstance(output, Mapping):
                await self._process_node_output(generation_id, str(node_id), output)
        elif event_type in {
            "execution_success",
            "execution_cached",
            "execution_interrupted",
            "execution_cancelled",
        }:
            return True
        elif event_type == "execution_error":
            await self._record_execution_error(generation_id, data)
            return True
        return False

    async def _update_stage(self, generation_id: str, node_id: str) -> None:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None or generation.status in TERMINAL_STATUSES:
                return
            stage = next(
                (
                    item
                    for item in generation.resolved_contract_json.get("stages", [])
                    if node_id in item.get("resolved_node_ids", [])
                ),
                None,
            )
            if stage is None:
                return
            if generation.current_stage_id == stage.get("id"):
                return
            generation.current_stage_id = str(stage.get("id"))
            generation.current_stage_label = str(stage.get("label"))
            generation.current_stage_sequence = int(stage.get("sequence", 0))
            event = add_generation_event(
                session,
                generation,
                "generation.stage",
                {
                    "stage_id": generation.current_stage_id,
                    "label": generation.current_stage_label,
                    "sequence": generation.current_stage_sequence,
                },
            )
            session.commit()
        await self._publish_event_best_effort(event, generation_id=generation_id)

    async def _record_progress(self, generation_id: str, data: Mapping[str, Any]) -> None:
        value = data.get("value")
        maximum = data.get("max")
        if not isinstance(value, (int, float)) or not isinstance(maximum, (int, float)):
            return
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None or generation.status in TERMINAL_STATUSES:
                return
            event = add_generation_event(
                session,
                generation,
                "generation.progress",
                {"value": value, "maximum": maximum},
            )
            session.commit()
        await self._publish_event_best_effort(event, generation_id=generation_id)

    async def _process_node_output(
        self, generation_id: str, node_id: str, output_payload: Mapping[str, Any]
    ) -> None:
        prepared = await _run_blocking(
            self._normalize_generation_history,
            generation_id,
            {"outputs": {node_id: output_payload}},
            retain_raw_history=False,
            require_nonterminal=False,
        )
        if prepared is None:
            return
        normalized, _ = prepared
        for file_output in normalized.files:
            await self._persist_native_file(generation_id, file_output)

    def _normalize_generation_history(
        self,
        generation_id: str,
        history: Mapping[str, Any],
        *,
        retain_raw_history: bool,
        require_nonterminal: bool,
    ) -> tuple[NormalizedHistory, dict[str, Any] | None] | None:
        """Load normalization context and process large JSON in one worker thread."""

        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None or (
                require_nonterminal and generation.status in TERMINAL_STATUSES
            ):
                return None
            contract = copy.deepcopy(generation.resolved_contract_json)
            warnings = [str(value) for value in generation.result_warnings_json]
        history_snapshot = copy.deepcopy(dict(history))
        normalized = normalize_history(
            history_snapshot,
            contract=contract,
            warnings=warnings,
        )
        return normalized, history_snapshot if retain_raw_history else None

    async def _persist_native_file(self, generation_id: str, file_output: NativeFileOutput) -> None:
        reference = file_output.reference
        with self.session_factory() as session:
            duplicate = session.scalar(
                select(Artifact.id).where(
                    Artifact.generation_id == generation_id,
                    Artifact.output_id == file_output.output_id,
                    Artifact.source_node_id == file_output.node_id,
                    Artifact.batch_index == file_output.batch_index,
                    Artifact.source_filename == reference.get("filename"),
                    Artifact.source_subfolder == (reference.get("subfolder") or None),
                    Artifact.source_type == reference.get("type", "output"),
                )
            )
            if duplicate is not None:
                self._clear_persistence_failure(generation_id, file_output)
                return
        stored: StoredImage | None = None
        retained = False
        try:
            content = await self.comfyui.retrieve_artifact(reference)
            stored = await self.assets.store_artifact_async(
                content,
                generation_id=generation_id,
                kind=file_output.kind,
            )
            declaration: dict[str, Any] = {
                "id": file_output.output_id,
                "role": file_output.role,
                "kind": file_output.kind,
                "resolved_sequence": _artifact_sequence(file_output),
                "canonical_on_success": file_output.role == "final",
                "usable_on_cancel": True,
                "usable_on_failure": True,
                "progression": {},
            }
            event, retained = await self._insert_artifact_async(
                generation_id=generation_id,
                declaration=declaration,
                node_id=file_output.node_id,
                batch_index=file_output.batch_index,
                stored=stored,
                source_filename=reference.get("filename"),
                source_subfolder=reference.get("subfolder") or None,
                source_type=reference.get("type", "output"),
            )
            if not retained:
                await self.assets.delete_stored_async(stored)
                stored = None
            self._clear_persistence_failure(generation_id, file_output)
            if event:
                await self._publish_event_best_effort(event, generation_id=generation_id)
        except Exception as exc:
            if stored is not None and not retained:
                await self.assets.delete_stored_async(stored)
            await self._record_persistence_failure(generation_id, file_output, exc)

    async def _insert_artifact_async(
        self,
        *,
        generation_id: str,
        declaration: Mapping[str, Any],
        node_id: str,
        batch_index: int,
        stored: StoredImage,
        source_filename: str | None,
        source_subfolder: str | None,
        source_type: str | None,
    ) -> tuple[Any | None, bool]:
        task = asyncio.create_task(
            asyncio.to_thread(
                self._insert_artifact,
                generation_id=generation_id,
                declaration=declaration,
                node_id=node_id,
                batch_index=batch_index,
                stored=stored,
                source_filename=source_filename,
                source_subfolder=source_subfolder,
                source_type=source_type,
            )
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            result: tuple[Any | None, bool] | None = None
            with suppress(Exception):
                result = await task
            if result is None or not result[1]:
                await asyncio.shield(self.assets.delete_stored_async(stored))
            raise

    def _insert_artifact(
        self,
        *,
        generation_id: str,
        declaration: Mapping[str, Any],
        node_id: str,
        batch_index: int,
        stored: StoredImage,
        source_filename: str | None,
        source_subfolder: str | None,
        source_type: str | None,
    ) -> tuple[Any | None, bool]:
        output_id = str(declaration.get("id"))
        sequence = int(declaration.get("resolved_sequence", 0))
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return None, False
            duplicate = session.scalar(
                select(Artifact).where(
                    Artifact.generation_id == generation_id,
                    Artifact.output_id == output_id,
                    Artifact.batch_index == batch_index,
                    Artifact.sha256 == stored.sha256,
                )
            )
            if duplicate:
                return None, False
            supersedes = declaration.get("progression", {}).get("supersedes", [])
            parent = None
            if supersedes:
                parent = session.scalar(
                    select(Artifact)
                    .where(
                        Artifact.generation_id == generation_id,
                        Artifact.output_id.in_(list(supersedes)),
                    )
                    .order_by(Artifact.sequence.desc(), Artifact.available_at.desc())
                    .limit(1)
                )
                session.execute(
                    update(Artifact)
                    .where(
                        Artifact.generation_id == generation_id,
                        Artifact.output_id.in_(list(supersedes)),
                        Artifact.state == ArtifactState.PROVISIONAL,
                    )
                    .values(state=ArtifactState.SUPERSEDED)
                )
            artifact = Artifact(
                generation_id=generation_id,
                owner_id=generation.owner_id,
                output_id=output_id,
                role=str(declaration.get("role")),
                kind=str(declaration.get("kind")),
                state=ArtifactState.PROVISIONAL,
                sequence=sequence,
                batch_index=batch_index,
                parent_artifact_id=parent.id if parent else None,
                storage_path=stored.relative_path,
                thumbnail_path=stored.thumbnail_path,
                mime_type=(
                    "text/plain; charset=utf-8"
                    if declaration.get("kind") == "text"
                    else stored.mime_type
                ),
                byte_size=stored.byte_size,
                width=stored.width or None,
                height=stored.height or None,
                sha256=stored.sha256,
                source_node_id=node_id,
                source_filename=source_filename,
                source_subfolder=source_subfolder,
                source_type=source_type,
                usable_on_cancel=bool(declaration.get("usable_on_cancel", False)),
                usable_on_failure=bool(
                    declaration.get("usable_on_failure", declaration.get("usable_on_cancel", False))
                ),
                emitted_at=datetime.now(UTC),
            )
            session.add(artifact)
            session.flush()
            generation.artifact_count = (
                session.scalar(
                    select(func.count())
                    .select_from(Artifact)
                    .where(Artifact.generation_id == generation_id)
                )
                or 0
            )
            if artifact.kind == "image":
                current = (
                    session.get(Artifact, generation.best_available_artifact_id)
                    if generation.best_available_artifact_id
                    else None
                )
                if current is None or _is_better_presentation_candidate(artifact, current):
                    generation.best_available_artifact_id = artifact.id
            event = add_generation_event(
                session,
                generation,
                "artifact.available",
                {
                    "artifact": {
                        "id": artifact.id,
                        "output_id": artifact.output_id,
                        "role": artifact.role,
                        "state": artifact.state.value,
                        "sequence": artifact.sequence,
                        "batch_index": artifact.batch_index,
                        "content_url": f"/api/artifacts/{artifact.id}/content",
                        "thumbnail_url": (
                            f"/api/artifacts/{artifact.id}/thumbnail"
                            if artifact.thumbnail_path
                            else None
                        ),
                    },
                    "status": generation.status.value,
                    "best_available_artifact_id": generation.best_available_artifact_id,
                },
            )
            session.commit()
            return event, True

    async def _record_persistence_failure(
        self, generation_id: str, file_output: NativeFileOutput, exc: Exception
    ) -> None:
        failure_key = _persistence_failure_key(file_output)
        required = _artifact_requires_persistence(file_output)
        diagnostic_key = (
            "artifact_persistence_failures" if required else "artifact_persistence_warnings"
        )
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            diagnostics = dict(generation.internal_diagnostics_json or {})
            failures = [
                value
                for value in diagnostics.get(diagnostic_key, [])
                if not isinstance(value, Mapping)
                or any(value.get(key) != expected for key, expected in failure_key.items())
            ]
            failures.append({**failure_key, "error": type(exc).__name__})
            diagnostics[diagnostic_key] = failures
            generation.internal_diagnostics_json = diagnostics
            event = add_generation_event(
                session,
                generation,
                "artifact.persistence_failed",
                {
                    "output_id": file_output.output_id,
                    "required": required,
                    "message": (
                        "A required output could not be archived."
                        if required
                        else (
                            "An optional output could not be archived; its native reference "
                            "was retained."
                        )
                    ),
                },
            )
            session.commit()
        await self._publish_event_best_effort(event, generation_id=generation_id)

    def _clear_persistence_failure(self, generation_id: str, file_output: NativeFileOutput) -> None:
        failure_key = _persistence_failure_key(file_output)
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            diagnostics = dict(generation.internal_diagnostics_json or {})
            changed = False
            for diagnostic_key in (
                "artifact_persistence_failures",
                "artifact_persistence_warnings",
            ):
                failures = diagnostics.get(diagnostic_key, [])
                if not isinstance(failures, list):
                    continue
                remaining = [
                    value
                    for value in failures
                    if not isinstance(value, Mapping)
                    or any(value.get(key) != expected for key, expected in failure_key.items())
                ]
                if len(remaining) == len(failures):
                    continue
                changed = True
                if remaining:
                    diagnostics[diagnostic_key] = remaining
                else:
                    diagnostics.pop(diagnostic_key, None)
            if not changed:
                return
            generation.internal_diagnostics_json = diagnostics
            session.commit()

    async def _record_execution_error(self, generation_id: str, data: Mapping[str, Any]) -> None:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            diagnostics = dict(generation.internal_diagnostics_json or {})
            diagnostics["comfyui_execution_error"] = {
                key: data.get(key)
                for key in ("node_id", "node_type", "exception_type")
                if data.get(key) is not None
            }
            generation.internal_diagnostics_json = diagnostics
            generation.error_code = "execution_failed"
            generation.error_message = "ComfyUI failed during workflow execution."
            generation.result_errors_json = [
                *(generation.result_errors_json or []),
                {
                    "code": generation.error_code,
                    "message": generation.error_message,
                },
            ]
            event = add_generation_event(
                session,
                generation,
                "generation.error",
                {"code": generation.error_code, "message": generation.error_message},
            )
            session.commit()
        await self._publish_event_best_effort(event, generation_id=generation_id)

    async def _record_reconciliation_error(self, generation_id: str, exc: AppError) -> None:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None or generation.status in TERMINAL_STATUSES:
                return
            diagnostics = dict(generation.internal_diagnostics_json or {})
            diagnostics["history_reconciliation_error"] = {"code": exc.code}
            generation.internal_diagnostics_json = diagnostics
            generation.error_code = "history_reconciliation_failed"
            generation.error_message = (
                "ComfyUI returned execution state that could not be reconciled safely."
            )
            errors = list(generation.result_errors_json or [])
            if not any(
                isinstance(value, Mapping) and value.get("code") == generation.error_code
                for value in errors
            ):
                errors.append(
                    {
                        "code": generation.error_code,
                        "message": generation.error_message,
                    }
                )
                generation.result_errors_json = errors
            event = add_generation_event(
                session,
                generation,
                "generation.error",
                {"code": generation.error_code, "message": generation.error_message},
            )
            session.commit()
        await self._publish_event_best_effort(event, generation_id=generation_id)

    async def _ensure_cancel_sent(self, generation_id: str, prompt_id: str) -> None:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            should_cancel = bool(
                generation and generation.status == GenerationStatus.CANCEL_REQUESTED
            )
            diagnostics = dict(generation.internal_diagnostics_json or {}) if generation else {}
            sent = bool(diagnostics.get("cancel_sent"))
            if should_cancel and not sent and generation:
                diagnostics["cancel_sent"] = True
                generation.internal_diagnostics_json = diagnostics
                session.commit()
            else:
                should_cancel = False
        if should_cancel:
            try:
                await self.comfyui.cancel(prompt_id, running=True)
            except Exception:
                with self.session_factory() as session:
                    generation = session.get(Generation, generation_id)
                    if generation:
                        diagnostics = dict(generation.internal_diagnostics_json or {})
                        diagnostics.pop("cancel_sent", None)
                        generation.internal_diagnostics_json = diagnostics
                        session.commit()

    async def _wait_for_history(
        self,
        prompt_id: str,
        *,
        generation_id: str | None = None,
        initial_history: dict[str, Any] | None = None,
        raise_unreachable: bool = False,
    ) -> dict[str, Any] | None:
        grace = getattr(getattr(self, "settings", None), "reconciliation_grace_seconds", 1.0)
        delay = min(0.1, max(0.01, grace))
        maximum_delay = min(1.0, max(delay, grace))
        latest_history = initial_history
        recorded_error_codes: set[str] = set()
        for attempt in range(12):
            try:
                history = await self.comfyui.history(prompt_id)
                if history is not None:
                    latest_history = history
                    if _history_terminal(history):
                        return history
            except AppError as exc:
                if generation_id is not None and exc.code not in recorded_error_codes:
                    recorded_error_codes.add(exc.code)
                    await self._record_reconciliation_error(generation_id, exc)
            except (httpx.HTTPError, OSError):
                if raise_unreachable:
                    raise
            if attempt < 11:
                await asyncio.sleep(delay)
                delay = min(delay * 1.7, maximum_delay)
        return latest_history

    async def _finalize(
        self, generation_id: str, *, history: Mapping[str, Any], outcome: str
    ) -> None:
        prepared = await _run_blocking(
            self._normalize_generation_history,
            generation_id,
            history,
            retain_raw_history=True,
            require_nonterminal=True,
        )
        if prepared is None:
            return
        normalized, raw_history = prepared
        if raw_history is None:
            raise RuntimeError("final history snapshot was not retained")
        for file_output in normalized.files:
            await self._persist_native_file(generation_id, file_output)
        committed = await _run_blocking(
            self._commit_finalization,
            generation_id,
            raw_history=raw_history,
            normalized=normalized,
            outcome=outcome,
        )
        if committed is None:
            return
        event, pending_delete, owner_id = committed
        await self._publish_event_best_effort(event, generation_id=generation_id)
        if pending_delete:
            await _run_blocking(self._delete_terminal_if_present, generation_id)
            await self._publish_broker_best_effort(
                owner_id,
                {
                    "id": None,
                    "type": "generation.deleted",
                    "generation_id": generation_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "payload": {},
                },
                generation_id=generation_id,
            )

    def _commit_finalization(
        self,
        generation_id: str,
        *,
        raw_history: dict[str, Any],
        normalized: NormalizedHistory,
        outcome: str,
    ) -> tuple[Any, bool, str] | None:
        """Atomically persist the terminal result using a thread-confined session."""

        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None or generation.status in TERMINAL_STATUSES:
                return None
            generation.raw_history_json = raw_history
            generation.declared_outputs_json = normalized.declared_outputs
            generation.unmapped_outputs_json = normalized.unmapped_outputs
            generation.comfyui_status_json = normalized.status
            diagnostics = generation.internal_diagnostics_json or {}
            provisional_error_codes = {"execution_failed", "history_reconciliation_failed"}
            if outcome == "success" and generation.error_code in provisional_error_codes:
                generation.error_code = None
                generation.error_message = None
            existing_errors = [
                value
                for value in (generation.result_errors_json or [])
                if not (
                    outcome == "success"
                    and isinstance(value, Mapping)
                    and value.get("code") in provisional_error_codes
                )
            ]
            existing_errors.extend(list(normalized.errors))
            generation.result_errors_json = existing_errors
            artifacts = list(
                session.scalars(
                    select(Artifact)
                    .where(Artifact.generation_id == generation_id)
                    .order_by(Artifact.sequence.desc(), Artifact.batch_index)
                )
            )
            persistence_failures = diagnostics.get("artifact_persistence_failures", [])
            persistence_warnings = diagnostics.get("artifact_persistence_warnings", [])
            result_warnings: list[Any] = list(normalized.warnings)
            if outcome == "success" and diagnostics.get("comfyui_execution_error"):
                result_warnings.append(
                    {
                        "code": "websocket_outcome_overridden",
                        "message": (
                            "A WebSocket execution-error hint was superseded by authoritative "
                            "successful ComfyUI history."
                        ),
                    }
                )
            if isinstance(persistence_warnings, list):
                for failure in persistence_warnings:
                    if not isinstance(failure, Mapping):
                        continue
                    warning = _optional_persistence_warning(failure)
                    if warning not in result_warnings:
                        result_warnings.append(warning)
            generation.result_warnings_json = result_warnings
            if outcome == "success" and not persistence_failures:
                declared_final = [item for item in artifacts if item.role == "final"]
                declared_final.sort(key=lambda item: (item.sequence, item.batch_index))
                presentation = (
                    declared_final[0] if declared_final else _best_native_image(artifacts)
                )
                for artifact in artifacts:
                    artifact.canonical = artifact in declared_final
                    artifact.best_available = bool(presentation and artifact.id == presentation.id)
                    if artifact in declared_final:
                        artifact.state = ArtifactState.FINAL
                    elif presentation and artifact.id == presentation.id:
                        artifact.state = ArtifactState.BEST_AVAILABLE
                    elif artifact.state == ArtifactState.PROVISIONAL:
                        artifact.state = ArtifactState.SUPERSEDED
                generation.canonical_artifact_id = declared_final[0].id if declared_final else None
                generation.best_available_artifact_id = presentation.id if presentation else None
                generation.final_artifact_count = len(declared_final)
                generation.status = GenerationStatus.SUCCEEDED
            elif outcome == "success" and persistence_failures:
                outcome = "failed"
                generation.error_code = "artifact_persistence_failed"
                generation.error_message = (
                    "ComfyUI completed, but one or more outputs could not be archived."
                )
                generation.result_errors_json = [
                    *(generation.result_errors_json or []),
                    {
                        "code": generation.error_code,
                        "message": generation.error_message,
                    },
                ]

            if outcome in {"cancelled", "failed", "interrupted"}:
                cancelled = outcome == "cancelled" or (
                    generation.status == GenerationStatus.CANCEL_REQUESTED and outcome != "failed"
                )
                eligible = [
                    item
                    for item in artifacts
                    if item.kind == "image"
                    and (item.usable_on_cancel if cancelled else item.usable_on_failure)
                ]
                best = _best_native_image(eligible)
                for artifact in artifacts:
                    artifact.canonical = False
                    artifact.best_available = bool(best and artifact.id == best.id)
                    if best and artifact.id == best.id:
                        artifact.state = ArtifactState.BEST_AVAILABLE
                    elif artifact.state == ArtifactState.PROVISIONAL:
                        artifact.state = ArtifactState.SUPERSEDED
                generation.canonical_artifact_id = None
                generation.best_available_artifact_id = best.id if best else None
                if outcome == "interrupted":
                    generation.status = GenerationStatus.INTERRUPTED
                    generation.error_code = generation.error_code or "execution_interrupted"
                    generation.error_message = generation.error_message or (
                        "Execution outcome could not be reconciled from ComfyUI history."
                    )
                elif cancelled:
                    generation.status = (
                        GenerationStatus.CANCELLED_WITH_ARTIFACTS
                        if best
                        else GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS
                    )
                else:
                    generation.status = (
                        GenerationStatus.FAILED_WITH_ARTIFACTS
                        if best
                        else GenerationStatus.FAILED_WITHOUT_ARTIFACTS
                    )
                    generation.error_code = generation.error_code or "execution_failed"
                    generation.error_message = generation.error_message or (
                        "Workflow execution failed before a final image was archived."
                    )
                if generation.error_code and not any(
                    isinstance(value, Mapping) and value.get("code") == generation.error_code
                    for value in generation.result_errors_json
                ):
                    generation.result_errors_json = [
                        *(generation.result_errors_json or []),
                        {
                            "code": generation.error_code,
                            "message": generation.error_message or "Generation did not complete.",
                        },
                    ]
            generation.completed_at = datetime.now(UTC)
            generation.current_stage_id = None
            generation.current_stage_label = None
            event = add_generation_event(
                session,
                generation,
                "generation.terminal",
                {
                    "status": generation.status.value,
                    "canonical_artifact_id": generation.canonical_artifact_id,
                    "best_available_artifact_id": generation.best_available_artifact_id,
                    "error": generation.error_message,
                },
            )
            pending_delete = generation.pending_delete
            owner_id = generation.owner_id
            session.commit()
            return event, pending_delete, owner_id

    def _delete_terminal_if_present(self, generation_id: str) -> None:
        """Delete a reconciled pending generation and its files in one worker thread."""

        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is not None:
                self.generations.delete_terminal(session, generation)

    async def _finish_without_execution(self, generation_id: str, *, cancelled: bool) -> None:
        await self._finalize(
            generation_id,
            history={},
            outcome="cancelled" if cancelled else "interrupted",
        )

    async def _requeue_after_outage(self, generation_id: str) -> None:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            if generation.status == GenerationStatus.CANCEL_REQUESTED:
                session.commit()
            else:
                generation.status = GenerationStatus.QUEUED
                generation.submitted_graph_json = None
                generation.submitted_graph_sha256 = None
                event = add_generation_event(
                    session,
                    generation,
                    "generation.requeued",
                    {"reason": "ComfyUI is temporarily unavailable."},
                )
                self._set_health(session, "comfyui", False, "ComfyUI is unreachable.")
                session.commit()
                await self._publish_event_best_effort(event, generation_id=generation_id)
                return
        await self._finish_without_execution(generation_id, cancelled=True)

    async def _fail_before_start(self, generation_id: str, exc: Exception) -> None:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            generation.status = GenerationStatus.FAILED_WITHOUT_ARTIFACTS
            generation.error_code = getattr(exc, "code", "comfyui_prompt_rejected")
            generation.error_message = getattr(
                exc, "message", "The workflow could not be dispatched to ComfyUI."
            )
            generation.result_errors_json = [
                {"code": generation.error_code, "message": generation.error_message}
            ]
            generation.completed_at = datetime.now(UTC)
            generation.internal_diagnostics_json = {
                "exception_type": type(exc).__name__,
                "queue_validation": copy.deepcopy(getattr(exc, "details", {})),
            }
            event = add_generation_event(
                session,
                generation,
                "generation.terminal",
                {"status": generation.status.value, "error": generation.error_message},
            )
            pending_delete = generation.pending_delete
            owner_id = generation.owner_id
            session.commit()
        await self._publish_event_best_effort(event, generation_id=generation_id)
        if pending_delete:
            with self.session_factory() as session:
                generation = session.get(Generation, generation_id)
                if generation:
                    self.generations.delete_terminal(session, generation)
            await self._publish_broker_best_effort(
                owner_id,
                {
                    "id": None,
                    "type": "generation.deleted",
                    "generation_id": generation_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "payload": {},
                },
                generation_id=generation_id,
            )

    async def _reconcile_startup(self) -> None:
        requeue_events, prompt_jobs = await _run_blocking(self._prepare_startup_recovery)
        for owner_id, event in requeue_events:
            generation_id = str(event["generation_id"])
            await self._publish_broker_best_effort(
                owner_id,
                event,
                generation_id=generation_id,
            )
        for generation_id, prompt_id in prompt_jobs:
            history = None
            history_reachable = True
            try:
                history = await self.comfyui.history(prompt_id)
            except Exception:
                history_reachable = False
            terminal = _history_terminal(history)
            if terminal:
                await self._finalize(generation_id, history=history or {}, outcome=terminal)
                continue
            queue_reachable = True
            try:
                queue = await self.comfyui.queue()
                queued_ids = _collect_prompt_ids(queue)
            except Exception:
                queue_reachable = False
                queued_ids = set()
            if prompt_id in queued_ids or not (history_reachable and queue_reachable):
                self._start_generation_task(
                    generation_id,
                    self._monitor(generation_id, prompt_id),
                    name=f"generation-recovered-{generation_id}",
                )
                continue
            await asyncio.sleep(self.settings.reconciliation_grace_seconds)
            try:
                history = await self._wait_for_history(
                    prompt_id,
                    generation_id=generation_id,
                    initial_history=history,
                    raise_unreachable=True,
                )
                queue = await self.comfyui.queue()
                queued_ids = _collect_prompt_ids(queue)
            except Exception as exc:
                if isinstance(exc, AppError):
                    await self._record_reconciliation_error(generation_id, exc)
                # The service became unavailable during the grace period. Preserve the
                # in-flight state and let the monitor reconcile after connectivity returns.
                self._start_generation_task(
                    generation_id,
                    self._monitor(generation_id, prompt_id),
                    name=f"generation-recovered-{generation_id}",
                )
                continue
            terminal = _history_terminal(history)
            if terminal:
                await self._finalize(generation_id, history=history or {}, outcome=terminal)
            elif prompt_id in queued_ids:
                self._start_generation_task(
                    generation_id,
                    self._monitor(generation_id, prompt_id),
                    name=f"generation-recovered-{generation_id}",
                )
            else:
                await self._finalize(generation_id, history=history or {}, outcome="interrupted")

    def _prepare_startup_recovery(self) -> RecoveryPlan:
        """Build a primitive recovery plan without loading generation JSON payloads."""

        with self.session_factory() as session:
            in_flight = list(
                session.execute(
                    select(
                        Generation.id,
                        Generation.owner_id,
                        Generation.status,
                        Generation.comfyui_prompt_id,
                    ).where(
                        Generation.status.in_(
                            [
                                GenerationStatus.DISPATCHING,
                                GenerationStatus.RUNNING,
                                GenerationStatus.CANCEL_REQUESTED,
                            ]
                        )
                    )
                )
            )
            requeue_events: list[RecoveryNotification] = []
            prompt_jobs: list[tuple[str, str]] = []
            for generation_id, owner_id, status, prompt_id in in_flight:
                generation_id = str(generation_id)
                owner_id = str(owner_id)
                if not prompt_id:
                    if status == GenerationStatus.CANCEL_REQUESTED:
                        session.execute(
                            update(Generation)
                            .where(Generation.id == generation_id)
                            .values(
                                status=GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS,
                                completed_at=datetime.now(UTC),
                            )
                        )
                    else:
                        session.execute(
                            update(Generation)
                            .where(Generation.id == generation_id)
                            .values(
                                status=GenerationStatus.QUEUED,
                                submitted_graph_json=None,
                                submitted_graph_sha256=None,
                            )
                        )
                        event = GenerationEvent(
                            generation_id=generation_id,
                            owner_id=owner_id,
                            event_type="generation.requeued",
                            payload_json={"reason": "Recovered before ComfyUI submission."},
                            created_at=datetime.now(UTC),
                        )
                        session.add(event)
                        session.flush()
                        requeue_events.append((owner_id, event_payload(event)))
                else:
                    prompt_jobs.append((generation_id, str(prompt_id)))
            session.commit()
            return tuple(requeue_events), tuple(prompt_jobs)

    async def _health_loop(self) -> None:
        while not self._stop.is_set():
            comfy_available, comfy_message = await self.comfyui.health()
            ollama_available, ollama_message = await self.ollama.status()
            await _run_blocking(
                self._persist_service_health,
                "ollama",
                ollama_available,
                ollama_message,
            )
            catalog_loading, should_refresh_catalog = await _run_blocking(
                self._comfy_recovery_state,
                comfy_available,
            )
            catalog_refreshed = False
            if should_refresh_catalog:
                try:
                    await self.generations.registry.refresh()
                    catalog_refreshed = True
                except Exception:
                    logger.exception("workflow_catalog_recovery_refresh_failed")
                    comfy_available = False
                    comfy_message = "ComfyUI source discovery failed during recovery."
            if not catalog_refreshed and not catalog_loading:
                await _run_blocking(
                    self._persist_service_health,
                    "comfyui",
                    comfy_available,
                    comfy_message,
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.external_health_interval_seconds
                )
            except TimeoutError:
                continue

    def _comfy_recovery_state(self, comfy_available: bool) -> tuple[bool, bool]:
        """Read the small cached state needed to decide whether catalog recovery is due."""

        with self.session_factory() as session:
            previous_comfy = session.get(ServiceHealth, "comfyui")
            catalog_state = (
                previous_comfy.capabilities_json.get("catalog_state") if previous_comfy else None
            )
            catalog_loading = catalog_state == "loading"
            should_refresh_catalog = (
                not catalog_loading
                and comfy_available
                and (
                    previous_comfy is None
                    or not previous_comfy.available
                    or catalog_state in {"unavailable", "cached_offline"}
                )
            )
            return catalog_loading, should_refresh_catalog

    def _persist_service_health(
        self,
        service: str,
        available: bool,
        message: str | None,
    ) -> None:
        """Persist one service probe in a short thread-confined transaction."""

        with self.session_factory() as session:
            self._set_health(session, service, available, message)
            session.commit()

    @staticmethod
    def _set_health(session: Session, service: str, available: bool, message: str | None) -> None:
        health = session.get(ServiceHealth, service)
        if health is None:
            health = ServiceHealth(service=service)
            session.add(health)
        health.available = available
        health.message = message
        health.checked_at = datetime.now(UTC)


def _history_terminal(history: Mapping[str, Any] | None) -> str | None:
    if not history:
        return None
    status = history.get("status", {})
    if isinstance(status, str):
        status_text = status.casefold()
        completed = status_text in {"success", "completed", "error", "failed", "cancelled"}
    elif isinstance(status, Mapping):
        status_text = str(status.get("status_str", status.get("status", ""))).casefold()
        completed = bool(status.get("completed", False))
    else:
        return None
    if history_status_indicates_interruption(status):
        return "cancelled"
    if not completed and status_text not in {
        "success",
        "error",
        "failed",
        "cancelled",
        "canceled",
        "interrupted",
    }:
        return None
    if status_text in {"success", "completed"}:
        return "success"
    if status_text in {"cancelled", "canceled", "interrupted"}:
        return "cancelled"
    return "failed"


def _persistence_failure_key(file_output: NativeFileOutput) -> dict[str, Any]:
    return {
        "output_id": file_output.output_id,
        "node_id": file_output.node_id,
        "batch_index": file_output.batch_index,
        "filename": file_output.reference.get("filename"),
        "subfolder": file_output.reference.get("subfolder", ""),
        "type": file_output.reference.get("type", "output"),
    }


def _artifact_requires_persistence(file_output: NativeFileOutput) -> bool:
    """Only a declared final file in durable ComfyUI storage is success-critical."""

    return (
        file_output.declared
        and file_output.role == "final"
        and file_output.reference.get("type", "output") in {"input", "output"}
    )


def _artifact_sequence(file_output: NativeFileOutput) -> int:
    """Rank authored roles while retaining manifest declaration order within each role."""

    role_rank = {
        "unmapped": 0,
        "auxiliary": 1,
        "preview": 2,
        "comparison": 3,
        "final": 4,
    }.get(file_output.role, 0)
    return role_rank * 1_000 + max(0, file_output.sequence)


def _is_better_presentation_candidate(candidate: Artifact, current: Artifact) -> bool:
    """Advance to a later authored stage without replacing its stable batch-zero image."""

    return candidate.sequence > current.sequence or (
        candidate.sequence == current.sequence
        and candidate.batch_index == 0
        and current.batch_index != 0
    )


def _optional_persistence_warning(failure: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "code": "optional_artifact_unavailable",
        "message": (
            "An optional ComfyUI file could not be archived; its native reference remains "
            "available in the result history."
        ),
        "output_id": failure.get("output_id"),
        "node_id": failure.get("node_id"),
        "reference": {
            "filename": failure.get("filename"),
            "subfolder": failure.get("subfolder", ""),
            "type": failure.get("type", "output"),
        },
    }


def _best_native_image(artifacts: list[Artifact]) -> Artifact | None:
    """Prefer authored roles and durable files while keeping batch zero presentation-stable."""

    images = [artifact for artifact in artifacts if artifact.kind == "image"]
    if not images:
        return None

    def emitted_score(artifact: Artifact) -> float:
        return artifact.emitted_at.timestamp() if artifact.emitted_at else 0.0

    role_rank = {"unmapped": 0, "auxiliary": 1, "preview": 2, "comparison": 3, "final": 4}
    storage_rank = {"temp": 0, "input": 1, "output": 2}
    return max(
        images,
        key=lambda artifact: (
            role_rank.get(artifact.role, 0),
            storage_rank.get(artifact.source_type or "", 0),
            artifact.sequence,
            artifact.batch_index == 0,
            emitted_score(artifact),
        ),
    )


def _collect_prompt_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"prompt_id", "id"} and isinstance(item, str):
                result.add(item)
            result.update(_collect_prompt_ids(item))
    elif isinstance(value, list):
        # ComfyUI queue entries are commonly positional arrays where element 1
        # is the prompt_id: [queue_number, prompt_id, graph, ...].
        if len(value) > 1 and isinstance(value[1], str):
            result.add(value[1])
        for item in value:
            result.update(_collect_prompt_ids(item))
    return result
