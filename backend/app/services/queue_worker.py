from __future__ import annotations

import asyncio
import copy
import logging
import time
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..domain.contract import sha256_json
from ..models import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    Artifact,
    ArtifactState,
    Generation,
    GenerationStatus,
    GenerationUpload,
    SchedulerState,
    ServiceHealth,
    Upload,
)
from .assets import AssetStore
from .comfyui import ComfyUIAdapter
from .event_broker import EventBroker
from .events import add_generation_event, publish_event
from .generations import GenerationService
from .ollama import OllamaAdapter

logger = logging.getLogger(__name__)


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
        self._health_task: asyncio.Task[None] | None = None
        self._active: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        if self._main_task is not None:
            return
        self._stop.clear()
        await self._reconcile_startup()
        self._main_task = asyncio.create_task(self._run(), name="generation-queue-worker")
        self._health_task = asyncio.create_task(self._health_loop(), name="external-health-monitor")

    async def stop(self) -> None:
        self._stop.set()
        tasks = [task for task in (self._main_task, self._health_task) if task]
        for task in tasks:
            task.cancel()
        for task in list(self._active.values()):
            task.cancel()
        await asyncio.gather(*tasks, *self._active.values(), return_exceptions=True)
        self._active.clear()
        self._main_task = None
        self._health_task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            for generation_id, task in list(self._active.items()):
                if task.done():
                    self._active.pop(generation_id, None)
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.exception("generation_task_failed", extra={"generation_id": generation_id})
            available_slots = self.settings.comfyui_concurrency - len(self._active)
            if available_slots > 0 and self._comfyui_available():
                for _ in range(available_slots):
                    claim = self._claim_next()
                    if claim is None:
                        break
                    generation_id, event = claim
                    await publish_event(self.broker, event)
                    self._active[generation_id] = asyncio.create_task(
                        self._execute(generation_id), name=f"generation-{generation_id}"
                    )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.dispatch_poll_seconds)
            except TimeoutError:
                continue

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
            prompt_id = await self.comfyui.submit_prompt(materialized, generation.comfyui_client_id)
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException, OSError):
            await self._requeue_after_outage(generation_id)
            return
        except Exception as exc:
            await self._fail_before_start(generation_id, exc)
            return

        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            if generation.status == GenerationStatus.CANCEL_REQUESTED:
                # Submission raced with cancellation; retain prompt ID and interrupt immediately.
                generation.comfyui_prompt_id = prompt_id
                session.commit()
                try:
                    await self.comfyui.cancel(prompt_id, running=True)
                except Exception:
                    pass
            else:
                generation.comfyui_prompt_id = prompt_id
                generation.status = GenerationStatus.RUNNING
                generation.dispatched_at = datetime.now(UTC)
                generation.started_at = datetime.now(UTC)
                event = add_generation_event(
                    session,
                    generation,
                    "generation.running",
                    {"status": GenerationStatus.RUNNING.value},
                )
                session.commit()
                await publish_event(self.broker, event)
        await self._monitor(generation_id, prompt_id)

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
                    content = self.assets.read(upload.storage_path)
                    cache[upload_id] = await self.comfyui.upload_image(
                        content,
                        f"{upload.id}.png",
                        kind=upload.kind.value,
                    )
                return cache[upload_id]
            if isinstance(value, dict):
                return {key: await replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [await replace(item) for item in value]
            return value

        return await replace(graph)

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
                logger.info("comfyui_websocket_disconnected", extra={"generation_id": generation_id})

        pump = asyncio.create_task(websocket_pump(), name=f"comfy-ws-{generation_id}")
        terminal_hint: str | None = None
        unknown_reachable_since: float | None = None
        try:
            while not self._stop.is_set():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.75)
                    terminal_hint = await self._process_runtime_event(
                        generation_id, prompt_id, event
                    ) or terminal_hint
                except TimeoutError:
                    pass
                await self._ensure_cancel_sent(generation_id, prompt_id)
                history = None
                try:
                    history = await self.comfyui.history(prompt_id)
                except (httpx.HTTPError, OSError):
                    history = None
                terminal = _history_terminal(history)
                if terminal or terminal_hint:
                    if history is None:
                        history = await self._wait_for_history(prompt_id)
                    await self._finalize(
                        generation_id,
                        history=history or {},
                        outcome=terminal or terminal_hint or "interrupted",
                    )
                    return
                if history is None:
                    # A missing history entry is ambiguous. Keep waiting while ComfyUI is
                    # unreachable or still reports the prompt in its queue. Once the service is
                    # reachable and the prompt is absent from both queue and history for the
                    # configured grace period, make the uncertainty explicit as `interrupted`.
                    try:
                        queue_state = await self.comfyui.queue()
                        present = prompt_id in _collect_prompt_ids(queue_state)
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
                        await self._finalize(generation_id, history={}, outcome="interrupted")
                        return
                    if pump.done():
                        # Keep polling: a transient WebSocket outage must not lose the durable result.
                        await asyncio.sleep(0.5)
                else:
                    unknown_reachable_since = None
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)

    async def _process_runtime_event(
        self, generation_id: str, prompt_id: str, event: Mapping[str, Any]
    ) -> str | None:
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
        elif event_type in {"execution_success", "execution_cached"}:
            return "success"
        elif event_type in {"execution_interrupted", "execution_cancelled"}:
            return "cancelled"
        elif event_type == "execution_error":
            await self._record_execution_error(generation_id, data)
            return "failed"
        return None

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
        await publish_event(self.broker, event)

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
                {"value": value, "maximum": maximum, "node": data.get("node")},
            )
            session.commit()
        await publish_event(self.broker, event)

    async def _process_node_output(
        self, generation_id: str, node_id: str, output_payload: Mapping[str, Any]
    ) -> None:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            declarations = [
                item
                for item in generation.resolved_contract_json.get("outputs", [])
                if str(item.get("resolved_node_id")) == node_id
            ]
        for declaration in declarations:
            await self._persist_declared_output(generation_id, declaration, output_payload, node_id)

    async def _persist_declared_output(
        self,
        generation_id: str,
        declaration: Mapping[str, Any],
        output_payload: Mapping[str, Any],
        node_id: str,
    ) -> None:
        kind = str(declaration.get("kind"))
        field = declaration.get("history_field") or ("images" if kind == "image" else "text")
        values = output_payload.get(field)
        if values is None and isinstance(output_payload.get("frontend_artifacts"), list):
            values = [
                item
                for item in output_payload["frontend_artifacts"]
                if isinstance(item, Mapping) and item.get("artifact_id") == declaration.get("id")
            ]
        if values is None:
            return
        if not isinstance(values, list):
            values = [values]
        for batch_index, value in enumerate(values):
            try:
                if kind == "image":
                    if not isinstance(value, Mapping):
                        continue
                    content = await self.comfyui.retrieve_artifact(value)
                    stored = self.assets.store_artifact(
                        content, generation_id=generation_id, kind="image"
                    )
                    source_filename = str(value.get("filename", "")) or None
                    source_subfolder = str(value.get("subfolder", "")) or None
                    source_type = str(value.get("type", "output"))
                else:
                    if isinstance(value, Mapping) and "text" in value:
                        text = str(value["text"])
                    else:
                        text = str(value)
                    stored = self.assets.store_artifact(
                        text.encode("utf-8"), generation_id=generation_id, kind="text"
                    )
                    source_filename = source_subfolder = source_type = None
                event = self._insert_artifact(
                    generation_id=generation_id,
                    declaration=declaration,
                    node_id=node_id,
                    batch_index=batch_index,
                    stored=stored,
                    source_filename=source_filename,
                    source_subfolder=source_subfolder,
                    source_type=source_type,
                )
                if event:
                    await publish_event(self.broker, event)
            except Exception as exc:
                await self._record_persistence_failure(
                    generation_id, str(declaration.get("id")), exc
                )

    def _insert_artifact(
        self,
        *,
        generation_id: str,
        declaration: Mapping[str, Any],
        node_id: str,
        batch_index: int,
        stored: Any,
        source_filename: str | None,
        source_subfolder: str | None,
        source_type: str | None,
    ) -> Any | None:
        output_id = str(declaration.get("id"))
        sequence = int(declaration.get("resolved_sequence", 0))
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                self.assets.delete_paths(
                    [path for path in (stored.relative_path, stored.thumbnail_path) if path]
                )
                return None
            duplicate = session.scalar(
                select(Artifact).where(
                    Artifact.generation_id == generation_id,
                    Artifact.output_id == output_id,
                    Artifact.batch_index == batch_index,
                    Artifact.sha256 == stored.sha256,
                )
            )
            if duplicate:
                self.assets.delete_paths(
                    [path for path in (stored.relative_path, stored.thumbnail_path) if path]
                )
                return None
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
                mime_type=("text/plain; charset=utf-8" if declaration.get("kind") == "text" else stored.mime_type),
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
                    declaration.get(
                        "usable_on_failure", declaration.get("usable_on_cancel", False)
                    )
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
                if current is None or artifact.sequence >= current.sequence:
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
            return event

    async def _record_persistence_failure(
        self, generation_id: str, output_id: str, exc: Exception
    ) -> None:
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None:
                return
            diagnostics = dict(generation.internal_diagnostics_json or {})
            failures = list(diagnostics.get("artifact_persistence_failures", []))
            failures.append({"output_id": output_id, "error": type(exc).__name__})
            diagnostics["artifact_persistence_failures"] = failures
            generation.internal_diagnostics_json = diagnostics
            event = add_generation_event(
                session,
                generation,
                "artifact.persistence_failed",
                {"output_id": output_id, "message": "An output could not be archived."},
            )
            session.commit()
        await publish_event(self.broker, event)

    async def _record_execution_error(
        self, generation_id: str, data: Mapping[str, Any]
    ) -> None:
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
            event = add_generation_event(
                session,
                generation,
                "generation.error",
                {"code": generation.error_code, "message": generation.error_message},
            )
            session.commit()
        await publish_event(self.broker, event)

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

    async def _wait_for_history(self, prompt_id: str) -> dict[str, Any] | None:
        for _ in range(20):
            try:
                history = await self.comfyui.history(prompt_id)
                if history is not None:
                    return history
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.25)
        return None

    async def _finalize(
        self, generation_id: str, *, history: Mapping[str, Any], outcome: str
    ) -> None:
        outputs = history.get("outputs", {}) if isinstance(history, Mapping) else {}
        if isinstance(outputs, Mapping):
            for node_id, output in outputs.items():
                if isinstance(output, Mapping):
                    await self._process_node_output(generation_id, str(node_id), output)
        with self.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation is None or generation.status in TERMINAL_STATUSES:
                return
            artifacts = list(
                session.scalars(
                    select(Artifact)
                    .where(Artifact.generation_id == generation_id)
                    .order_by(Artifact.sequence.desc(), Artifact.batch_index)
                )
            )
            persistence_failures = generation.internal_diagnostics_json.get(
                "artifact_persistence_failures", []
            )
            if outcome == "success" and not persistence_failures:
                canonical_ids = {
                    str(item.get("id"))
                    for item in generation.resolved_contract_json.get("outputs", [])
                    if item.get("canonical_on_success")
                }
                canonical = [item for item in artifacts if item.output_id in canonical_ids]
                if not canonical:
                    outcome = "failed"
                    generation.error_code = "output_missing"
                    generation.error_message = "Workflow completed without its declared final output."
                else:
                    for artifact in artifacts:
                        if artifact in canonical:
                            artifact.state = ArtifactState.FINAL
                            artifact.canonical = True
                        elif artifact.state == ArtifactState.PROVISIONAL:
                            artifact.state = ArtifactState.SUPERSEDED
                        artifact.best_available = False
                    canonical.sort(key=lambda item: item.batch_index)
                    generation.canonical_artifact_id = canonical[0].id
                    generation.best_available_artifact_id = canonical[0].id
                    generation.final_artifact_count = len(canonical)
                    generation.status = GenerationStatus.SUCCEEDED
            elif outcome == "success" and persistence_failures:
                outcome = "failed"
                generation.error_code = "artifact_persistence_failed"
                generation.error_message = "ComfyUI completed, but one or more outputs could not be archived."

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
                best = eligible[0] if eligible else None
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
                        "Execution outcome could not be reconciled after restart."
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
        await publish_event(self.broker, event)
        if pending_delete:
            with self.session_factory() as session:
                generation = session.get(Generation, generation_id)
                if generation:
                    self.generations.delete_terminal(session, generation)
            await self.broker.publish(
                owner_id,
                {
                    "id": None,
                    "type": "generation.deleted",
                    "generation_id": generation_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "payload": {},
                },
            )

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
                await publish_event(self.broker, event)
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
            generation.completed_at = datetime.now(UTC)
            generation.internal_diagnostics_json = {"exception_type": type(exc).__name__}
            event = add_generation_event(
                session,
                generation,
                "generation.terminal",
                {"status": generation.status.value, "error": generation.error_message},
            )
            pending_delete = generation.pending_delete
            owner_id = generation.owner_id
            session.commit()
        await publish_event(self.broker, event)
        if pending_delete:
            with self.session_factory() as session:
                generation = session.get(Generation, generation_id)
                if generation:
                    self.generations.delete_terminal(session, generation)
            await self.broker.publish(
                owner_id,
                {
                    "id": None,
                    "type": "generation.deleted",
                    "generation_id": generation_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "payload": {},
                },
            )

    async def _reconcile_startup(self) -> None:
        with self.session_factory() as session:
            in_flight = list(
                session.scalars(
                    select(Generation).where(
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
            requeue_events = []
            prompt_jobs: list[tuple[str, str]] = []
            for generation in in_flight:
                if not generation.comfyui_prompt_id:
                    if generation.status == GenerationStatus.CANCEL_REQUESTED:
                        generation.status = GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS
                        generation.completed_at = datetime.now(UTC)
                    else:
                        generation.status = GenerationStatus.QUEUED
                        requeue_events.append(
                            add_generation_event(
                                session,
                                generation,
                                "generation.requeued",
                                {"reason": "Recovered before ComfyUI submission."},
                            )
                        )
                else:
                    prompt_jobs.append((generation.id, generation.comfyui_prompt_id))
            session.commit()
        for event in requeue_events:
            await publish_event(self.broker, event)
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
                self._active[generation_id] = asyncio.create_task(
                    self._monitor(generation_id, prompt_id), name=f"generation-recovered-{generation_id}"
                )
                continue
            await asyncio.sleep(self.settings.reconciliation_grace_seconds)
            try:
                history = await self.comfyui.history(prompt_id)
            except Exception:
                # The service became unavailable during the grace period. Preserve the
                # in-flight state and let the monitor reconcile after connectivity returns.
                self._active[generation_id] = asyncio.create_task(
                    self._monitor(generation_id, prompt_id), name=f"generation-recovered-{generation_id}"
                )
                continue
            terminal = _history_terminal(history)
            if terminal:
                await self._finalize(generation_id, history=history or {}, outcome=terminal)
            else:
                await self._finalize(generation_id, history=history or {}, outcome="interrupted")

    async def _health_loop(self) -> None:
        while not self._stop.is_set():
            comfy_available, comfy_message = await self.comfyui.health()
            ollama_available, ollama_message = await self.ollama.status()
            with self.session_factory() as session:
                self._set_health(session, "comfyui", comfy_available, comfy_message)
                self._set_health(session, "ollama", ollama_available, ollama_message)
                session.commit()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.external_health_interval_seconds
                )
            except TimeoutError:
                continue

    @staticmethod
    def _set_health(
        session: Session, service: str, available: bool, message: str | None
    ) -> None:
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
    if not completed and status_text not in {"success", "error", "failed", "cancelled", "interrupted"}:
        return None
    if status_text in {"success", "completed"}:
        return "success"
    if status_text in {"cancelled", "canceled", "interrupted"}:
        return "cancelled"
    return "failed"


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
