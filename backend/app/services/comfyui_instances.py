from __future__ import annotations

import asyncio
from typing import Literal

import httpx

from ..config import ComfyUIInstanceConfig, Settings
from ..errors import AppError
from .comfyui import ComfyUIAdapter


class ComfyUIInstances:
    """Configured ComfyUI adapters keyed by stable, non-secret execution identity."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.configs = settings.configured_comfyui_instances
        self.default_id = settings.default_comfyui_instance.id
        self.configuration_mode = settings.comfyui_instance_configuration_mode
        self.configured_ids = frozenset(instance.id for instance in self.configs)
        self._configs = {instance.id: instance for instance in self.configs}
        self._adapters = {
            instance.id: ComfyUIAdapter(
                settings.model_copy(
                    update={
                        "comfyui_base_url": instance.base_url,
                        "comfyui_ws_url": instance.ws_url,
                        "comfyui_instance_id": instance.id,
                        "comfyui_user": instance.user,
                        "comfyui_concurrency": instance.concurrency,
                    }
                ),
                transport=transport,
            )
            for instance in self.configs
        }
        self.default_adapter = self._adapters[self.default_id]

    def config(self, instance_id: str) -> ComfyUIInstanceConfig | None:
        return self._configs.get(instance_id)

    def for_stage(
        self, kind: Literal["image", "text"], supplied_id: str | None = None
    ) -> ComfyUIInstanceConfig:
        """Resolve an environment assignment; old request fields are assertions only."""
        instance_id = self.default_id if kind == "image" else self.settings.comfyui_text_instance_id
        if instance_id is None:
            raise AppError(
                "prompt_runtime_not_configured",
                "Configure the CPU prompt service in the server environment.",
                status_code=503,
            )
        if supplied_id is not None and supplied_id != instance_id:
            raise AppError(
                "runtime_assignment_conflict",
                "Generation services are server-assigned. Reload the app for current settings.",
                status_code=409,
                details={"stage": kind, "instance_id": instance_id},
            )
        config = self.config(instance_id)
        if config is None:
            raise AppError(
                "comfyui_instance_unconfigured",
                "The assigned service is not configured.",
                status_code=503,
            )
        return config

    def get(self, instance_id: str) -> ComfyUIAdapter:
        adapter = self._adapters.get(instance_id)
        if adapter is None:
            raise AppError(
                "comfyui_instance_unconfigured",
                "The selected ComfyUI instance is no longer configured.",
                status_code=409,
                details={"instance_id": instance_id},
            )
        return adapter

    async def close(self) -> None:
        await asyncio.gather(*(adapter.close() for adapter in self._adapters.values()))
