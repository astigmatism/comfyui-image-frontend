from __future__ import annotations

import json

import pytest
from app.config import Settings
from app.errors import AppError
from app.schemas import GenerationCreate
from app.services.comfyui_instances import ComfyUIInstances
from pydantic import ValidationError


def test_legacy_single_instance_configuration_is_preserved() -> None:
    settings = Settings(
        _env_file=None,
        test_mode=True,
        comfyui_base_url="http://comfy.test:8188/",
        comfyui_ws_url="ws://comfy.test:8188/ws",
        comfyui_instance_id="stable-home",
        comfyui_user="local-user",
        comfyui_concurrency=3,
    )

    assert settings.comfyui_default_instance_id == "stable-home"
    assert [item.model_dump() for item in settings.configured_comfyui_instances] == [
        {
            "id": "stable-home",
            "label": "stable-home",
            "description": None,
            "base_url": "http://comfy.test:8188",
            "ws_url": "ws://comfy.test:8188/ws",
            "user": "local-user",
            "concurrency": 3,
        }
    ]


def test_json_instance_configuration_selects_an_explicit_default(monkeypatch) -> None:
    monkeypatch.setenv(
        "CIF_COMFYUI_INSTANCES",
        json.dumps(
            [
                {
                    "id": "primary",
                    "label": "Primary ComfyUI — RTX 3090",
                    "description": "24 GB VRAM",
                    "base_url": "http://local-ai-comfyui:8188",
                },
                {
                    "id": "worker-2",
                    "label": "ComfyUI Worker 2 — RTX 3080",
                    "description": "10 GB VRAM",
                    "base_url": "http://local-ai-comfyui-worker-2:8188",
                    "concurrency": 2,
                },
            ]
        ),
    )
    monkeypatch.setenv("CIF_COMFYUI_DEFAULT_INSTANCE_ID", "primary")

    settings = Settings(_env_file=None, test_mode=True)

    assert settings.default_comfyui_instance.label == "Primary ComfyUI — RTX 3090"
    assert [item.id for item in settings.configured_comfyui_instances] == [
        "primary",
        "worker-2",
    ]
    assert settings.configured_comfyui_instances[0].concurrency == 1
    assert settings.configured_comfyui_instances[1].concurrency == 2


@pytest.mark.parametrize(
    ("instances", "default_instance_id", "message"),
    [
        (
            [
                {"id": "duplicate", "label": "One", "base_url": "http://one.test"},
                {"id": "duplicate", "label": "Two", "base_url": "http://two.test"},
            ],
            "duplicate",
            "unique instance IDs",
        ),
        (
            [{"id": "primary", "label": "Primary", "base_url": "http://one.test"}],
            "missing",
            "must match a configured instance",
        ),
    ],
)
def test_instance_configuration_rejects_ambiguous_routing(
    instances: list[dict[str, object]],
    default_instance_id: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(
            _env_file=None,
            test_mode=True,
            comfyui_instances=instances,
            comfyui_default_instance_id=default_instance_id,
        )


@pytest.mark.parametrize("instance_id", ["-worker", "_worker", "wörker", "worker 2"])
def test_config_and_generation_request_share_safe_instance_id_rules(instance_id: str) -> None:
    with pytest.raises(ValidationError, match="instance ID"):
        Settings(
            _env_file=None,
            test_mode=True,
            comfyui_instances=[
                {"id": instance_id, "label": "Worker", "base_url": "http://worker.test"}
            ],
        )

    with pytest.raises(ValidationError):
        GenerationCreate(source_key="source", comfyui_instance_id=instance_id)


def test_global_concurrency_fallback_has_the_per_instance_safety_cap() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 32"):
        Settings(_env_file=None, test_mode=True, comfyui_concurrency=33)


@pytest.mark.asyncio
async def test_instance_registry_never_falls_back_for_an_unknown_pin() -> None:
    settings = Settings(
        _env_file=None,
        test_mode=True,
        comfyui_instances=[
            {"id": "primary", "label": "Primary", "base_url": "http://primary.test"}
        ],
        comfyui_default_instance_id="primary",
    )
    instances = ComfyUIInstances(settings)
    try:
        with pytest.raises(AppError) as error:
            instances.get("removed-worker")
        assert error.value.code == "comfyui_instance_unconfigured"
    finally:
        await instances.close()
