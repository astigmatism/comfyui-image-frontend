from __future__ import annotations

import importlib.util
import io
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from app.config import Settings
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]


def image_environment() -> dict[str, str]:
    dockerfile = (ROOT / "Dockerfile").read_text().replace("\\\n", " ")
    result = {}
    for line in dockerfile.splitlines():
        if line.startswith("ENV "):
            for assignment in shlex.split(line[4:]):
                key, value = assignment.split("=", 1)
                result[key] = value
    return result


@pytest.fixture
def image_settings_environment(monkeypatch):
    for key in os.environ:
        if key.startswith("CIF_"):
            monkeypatch.delenv(key)
    for key, value in image_environment().items():
        monkeypatch.setenv(key, value)


def test_installed_portal_runner_smoke_environment_boots_new_image_settings(
    monkeypatch, image_settings_environment
):
    fixture = ROOT / "scripts/tests/fixtures/pinned_portal_smoke.py"
    spec = importlib.util.spec_from_file_location("pinned_portal_smoke", fixture)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    supplied = {}

    def docker(command, **kwargs):
        if command[:2] == ["docker", "run"]:
            assert command[command.index("--network") + 1] == "none"
            assert command[command.index("--user") + 1] == "1000:1000"
            assert "--read-only" in command
            env_file = Path(command[command.index("--env-file") + 1])
            supplied.update(line.split("=", 1) for line in env_file.read_text().splitlines())
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(runner.subprocess, "run", docker)
    monkeypatch.setattr(runner, "inspect", lambda _: {"State": {"Running": True}})
    runner.smoke_image("candidate", "1000:1000", io.StringIO())
    assert "CIF_COMFYUI_TEXT_INSTANCE_ID" not in supplied
    for key, value in supplied.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert [instance.id for instance in settings.configured_comfyui_instances] == ["smoke"]
    assert settings.comfyui_default_instance_id == "smoke"
    assert settings.comfyui_text_instance_id is None


def test_production_stage_assignments_remain_explicit_and_strict(
    monkeypatch, image_settings_environment
):
    for line in (ROOT / ".env.example").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {
            "CIF_COMFYUI_INSTANCES",
            "CIF_COMFYUI_DEFAULT_INSTANCE_ID",
            "CIF_COMFYUI_TEXT_INSTANCE_ID",
        }:
            monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None, test_mode=True)
    assert settings.comfyui_default_instance_id == "primary"
    assert settings.comfyui_text_instance_id == "promptgen"
    assert {item.id: item.base_url for item in settings.configured_comfyui_instances} == {
        "primary": "http://comfyui:8188",
        "promptgen": "http://comfyui-promptgen:8188",
    }
    monkeypatch.setenv("CIF_COMFYUI_TEXT_INSTANCE_ID", "missing")
    with pytest.raises(ValidationError, match="must match a configured instance"):
        Settings(_env_file=None, test_mode=True)
