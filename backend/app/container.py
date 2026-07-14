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
        )

    async def close(self) -> None:
        started_at = time.monotonic()
        logger.info("application_shutdown_started")
        await self.worker.stop()
        logger.info("worker_cancellation_complete")
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
