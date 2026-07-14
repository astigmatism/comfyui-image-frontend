from __future__ import annotations

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
        await self.worker.stop()
        await self.comfyui.close()
        await self.ollama.close()
        await self.speech_to_text.close()
        self.db.close()
