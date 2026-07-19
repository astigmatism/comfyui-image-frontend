from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .config import Settings
from .db import Database
from .domain.compiler import WorkflowCompiler
from .services.assets import AssetStore
from .services.auth import AuthService
from .services.comfyui import ComfyUIAdapter
from .services.event_broker import EventBroker
from .services.generation_eta import GenerationEtaEstimator
from .services.generations import GenerationService
from .services.ollama import OllamaAdapter
from .services.queue_worker import QueueWorker
from .services.speech_to_text import SpeechToTextAdapter
from .services.user_deletion import UserDeletionService
from .services.workflow_registry import WorkflowRegistry

logger = logging.getLogger(__name__)


class AppContainer:
    def __init__(
        self,
        settings: Settings,
        *,
        comfy_transport: httpx.AsyncBaseTransport | None = None,
        ollama_transport: httpx.AsyncBaseTransport | None = None,
        speech_to_text_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.db = Database(settings)
        self.auth = AuthService(settings)
        self.assets = AssetStore(settings)
        self.broker = EventBroker()
        self.comfyui = ComfyUIAdapter(settings, transport=comfy_transport)
        self.ollama = OllamaAdapter(settings, transport=ollama_transport)
        self.speech_to_text = SpeechToTextAdapter(
            settings,
            transport=speech_to_text_transport,
        )
        self.registry = WorkflowRegistry(self.db.session_factory, self.comfyui)
        self.compiler = WorkflowCompiler()
        self.generation_eta = GenerationEtaEstimator(self.db.session_factory)
        self.generations = GenerationService(
            session_factory=self.db.session_factory,
            registry=self.registry,
            compiler=self.compiler,
            assets=self.assets,
            comfyui=self.comfyui,
            broker=self.broker,
        )
        self.user_deletion = UserDeletionService(
            session_factory=self.db.session_factory,
            auth=self.auth,
            comfyui=self.comfyui,
            assets=self.assets,
        )
        self.worker = QueueWorker(
            settings=settings,
            session_factory=self.db.session_factory,
            comfyui=self.comfyui,
            ollama=self.ollama,
            assets=self.assets,
            broker=self.broker,
            generations=self.generations,
            generation_eta=self.generation_eta,
        )
        self._startup_discovery_task: asyncio.Task[None] | None = None
        self._observed_startup_discovery_tasks: set[asyncio.Future[None]] = set()

    def start_workflow_discovery(self) -> None:
        if self._startup_discovery_task is not None:
            if not self._startup_discovery_task.done():
                return
            self._observe_startup_discovery_task(self._startup_discovery_task)
        self.registry.mark_startup_loading()
        task = asyncio.create_task(
            self._run_startup_discovery(),
            name="startup-workflow-discovery",
        )
        self._startup_discovery_task = task
        task.add_done_callback(self._observe_startup_discovery_task)

    def _observe_startup_discovery_task(self, task: asyncio.Future[None]) -> None:
        """Retrieve and report a background task exception exactly once."""

        if task in self._observed_startup_discovery_tasks or not task.done():
            return
        self._observed_startup_discovery_tasks.add(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is None:
            return
        logger.error(
            "startup_workflow_discovery_task_failed",
            extra={"exception_class": type(exception).__name__},
            exc_info=(type(exception), exception, exception.__traceback__),
        )

    async def _run_startup_discovery(self) -> None:
        started_at = time.monotonic()
        logger.info("startup_workflow_discovery_started")
        try:
            await self.registry.refresh()
        except asyncio.CancelledError:
            logger.info("startup_workflow_discovery_cancelled")
            raise
        except Exception as exc:
            logger.exception(
                "startup_workflow_discovery_failed",
                extra={"exception_class": type(exc).__name__},
            )
            failure_record = asyncio.create_task(
                asyncio.to_thread(self.registry.record_background_refresh_failure)
            )
            try:
                await asyncio.shield(failure_record)
            except asyncio.CancelledError:
                await asyncio.gather(failure_record, return_exceptions=True)
                raise
            except Exception as record_exc:
                logger.exception(
                    "startup_workflow_discovery_failure_record_failed",
                    extra={"exception_class": type(record_exc).__name__},
                )
        else:
            logger.info(
                "startup_workflow_discovery_complete",
                extra={"duration_ms": round((time.monotonic() - started_at) * 1000, 3)},
            )

    async def _stop_startup_discovery(self) -> None:
        task = self._startup_discovery_task
        self._startup_discovery_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._observe_startup_discovery_task(task)

    async def close(self) -> None:
        started_at = time.monotonic()
        logger.info("application_shutdown_started")
        await self.worker.stop()
        logger.info("worker_cancellation_complete")
        await self.generation_eta.stop()
        logger.info("generation_eta_maintenance_stopped")
        await self._stop_startup_discovery()
        logger.info("startup_discovery_cancellation_complete")
        await asyncio.gather(
            self.comfyui.close(),
            self.ollama.close(),
            self.speech_to_text.close(),
        )
        logger.info("external_clients_closed")
        self.db.close()
        logger.info("database_closed")
        logger.info(
            "application_shutdown_complete",
            extra={"shutdown_duration_seconds": round(time.monotonic() - started_at, 3)},
        )
