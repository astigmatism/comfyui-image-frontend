from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config import Settings, get_settings
from app.errors import AppError
from app.schemas import GenerationCreate
from app.services.comfyui_instances import ComfyUIInstances
from pydantic import ValidationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_legacy_single_instance_configuration_keeps_identity_with_friendly_label() -> None:
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
    assert settings.comfyui_instance_configuration_mode == "legacy"
    assert [item.model_dump() for item in settings.configured_comfyui_instances] == [
        {
            "id": "stable-home",
            "label": "Original",
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

    assert settings.comfyui_instance_configuration_mode == "explicit"
    assert settings.default_comfyui_instance.label == "Primary ComfyUI — RTX 3090"
    assert [item.id for item in settings.configured_comfyui_instances] == [
        "primary",
        "worker-2",
    ]
    assert settings.configured_comfyui_instances[0].concurrency == 1
    assert settings.configured_comfyui_instances[1].concurrency == 2


def test_standard_compose_defaults_extend_the_existing_household_primary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CIF_COMFYUI_INSTANCES", raising=False)
    monkeypatch.delenv("CIF_COMFYUI_ADDITIONAL_INSTANCES", raising=False)
    monkeypatch.delenv("CIF_COMFYUI_DEFAULT_INSTANCE_ID", raising=False)
    defaults_file = REPOSITORY_ROOT / "deployment" / "comfyui-instances.env"
    private_file = tmp_path / "legacy.env"
    private_file.write_text(
        "CIF_COMFYUI_INSTANCE_ID=home\n"
        "CIF_COMFYUI_BASE_URL=http://local-ai-comfyui:8188\n"
        "CIF_COMFYUI_WS_URL=ws://local-ai-comfyui:8188/ws\n"
        "CIF_COMFYUI_USER=household-user\n"
        "CIF_COMFYUI_CONCURRENCY=3\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=(defaults_file, private_file), test_mode=True)

    assert settings.comfyui_instance_configuration_mode == "explicit"
    assert settings.comfyui_default_instance_id == "home"
    assert [
        (item.id, item.label, item.description, item.base_url)
        for item in settings.configured_comfyui_instances
    ] == [
        (
            "home",
            "Original · RTX 3090",
            "24 GB VRAM",
            "http://local-ai-comfyui:8188",
        ),
        (
            "worker-2",
            "Worker 1 · RTX 3080",
            "10 GB VRAM",
            "http://192.168.1.21:8189",
        ),
    ]
    primary = settings.configured_comfyui_instances[0]
    assert (primary.ws_url, primary.user, primary.concurrency) == (
        "ws://local-ai-comfyui:8188/ws",
        "household-user",
        3,
    )

    compose = (REPOSITORY_ROOT / "compose.example.yml").read_text(encoding="utf-8")
    defaults_index = compose.index("- deployment/comfyui-instances.env")
    private_index = compose.index("- .env")
    assert defaults_index < private_index
    assert "\n.env\n" in (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
    defaults = defaults_file.read_text(encoding="utf-8")
    assert "CIF_COMFYUI_ADDITIONAL_INSTANCES=" in defaults
    assert "\nCIF_COMFYUI_INSTANCES=" not in defaults


def test_production_image_bundles_defaults_for_launches_that_bypass_compose(
    monkeypatch,
    tmp_path: Path,
) -> None:
    for name in (
        "CIF_COMFYUI_INSTANCES",
        "CIF_COMFYUI_ADDITIONAL_INSTANCES",
        "CIF_COMFYUI_DEFAULT_INSTANCE_ID",
        "CIF_COMFYUI_LABEL",
        "CIF_COMFYUI_DESCRIPTION",
    ):
        monkeypatch.delenv(name, raising=False)
    defaults_file = REPOSITORY_ROOT / "deployment" / "comfyui-instances.env"
    image_defaults: dict[str, str] = {}
    for raw_line in defaults_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, value = line.split("=", 1)
        image_defaults[name] = value
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CIF_COMFYUI_INSTANCE_ID", "persisted-home")
    monkeypatch.setenv("CIF_COMFYUI_BASE_URL", "http://persisted-primary.test:8188")
    monkeypatch.setenv("CIF_COMFYUI_WS_URL", "ws://persisted-primary.test:8188/ws")
    monkeypatch.setenv("CIF_COMFYUI_USER", "persisted-user")
    monkeypatch.setenv("CIF_COMFYUI_CONCURRENCY", "4")
    monkeypatch.setenv("CIF_TEST_MODE", "true")

    get_settings.cache_clear()
    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.comfyui_instance_configuration_mode == "explicit"
    assert settings.comfyui_default_instance_id == "persisted-home"
    assert [item.id for item in settings.configured_comfyui_instances] == [
        "persisted-home",
        "worker-2",
    ]
    primary, worker = settings.configured_comfyui_instances
    assert primary.model_dump() == {
        "id": "persisted-home",
        "label": "Original · RTX 3090",
        "description": "24 GB VRAM",
        "base_url": "http://persisted-primary.test:8188",
        "ws_url": "ws://persisted-primary.test:8188/ws",
        "user": "persisted-user",
        "concurrency": 4,
    }
    assert (worker.id, worker.label, worker.base_url) == (
        "worker-2",
        "Worker 1 · RTX 3080",
        "http://192.168.1.21:8189",
    )
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f'ENV CIF_COMFYUI_LABEL="{image_defaults["CIF_COMFYUI_LABEL"]}"' in dockerfile
    assert (
        f'ENV CIF_COMFYUI_DESCRIPTION="{image_defaults["CIF_COMFYUI_DESCRIPTION"]}"' in dockerfile
    )
    escaped_workers = image_defaults["CIF_COMFYUI_ADDITIONAL_INSTANCES"].replace('"', '\\"')
    assert f'ENV CIF_COMFYUI_ADDITIONAL_INSTANCES="{escaped_workers}"' in dockerfile


def test_explicit_empty_additional_instances_is_a_single_runtime_opt_out(monkeypatch) -> None:
    monkeypatch.delenv("CIF_COMFYUI_INSTANCES", raising=False)
    monkeypatch.delenv("CIF_COMFYUI_DEFAULT_INSTANCE_ID", raising=False)
    monkeypatch.setenv("CIF_COMFYUI_ADDITIONAL_INSTANCES", "[]")

    settings = Settings(
        _env_file=None,
        test_mode=True,
        comfyui_instance_id="intentional-single",
    )

    assert settings.comfyui_instance_configuration_mode == "explicit"
    assert [item.id for item in settings.configured_comfyui_instances] == ["intentional-single"]


def test_updater_verifies_the_running_container_is_not_in_legacy_mode() -> None:
    updater = (REPOSITORY_ROOT / "update_and_restart").read_text(encoding="utf-8")

    verification_index = updater.index("comfyui_instance_configuration_mode")
    success_index = updater.index('echo "$SERVICE is updated and healthy."')
    assert '"${compose[@]}" exec -T "$SERVICE" python -c' in updater
    assert verification_index < success_index


def test_private_env_can_override_standard_compose_instance_defaults(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CIF_COMFYUI_INSTANCES", raising=False)
    monkeypatch.delenv("CIF_COMFYUI_ADDITIONAL_INSTANCES", raising=False)
    monkeypatch.delenv("CIF_COMFYUI_DEFAULT_INSTANCE_ID", raising=False)
    override_file = tmp_path / "override.env"
    override_file.write_text(
        'CIF_COMFYUI_INSTANCES=[{"id":"custom","label":"Custom",'
        '"base_url":"http://custom.test:8188"}]\n',
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=(
            REPOSITORY_ROOT / "deployment" / "comfyui-instances.env",
            override_file,
        ),
        test_mode=True,
    )

    assert settings.comfyui_default_instance_id == "custom"
    assert [(item.id, item.label) for item in settings.configured_comfyui_instances] == [
        ("custom", "Custom")
    ]


def test_additional_instance_configuration_rejects_a_legacy_primary_id_collision() -> None:
    with pytest.raises(ValidationError, match="unique instance IDs"):
        Settings(
            _env_file=None,
            test_mode=True,
            comfyui_instance_id="primary",
            comfyui_additional_instances=[
                {
                    "id": "primary",
                    "label": "Duplicate",
                    "base_url": "http://worker.test:8188",
                }
            ],
        )


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
