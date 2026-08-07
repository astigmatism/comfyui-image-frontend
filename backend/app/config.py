from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

COMFYUI_INSTANCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


def _validate_comfyui_instance_id(value: str) -> str:
    normalized = value.strip()
    if re.fullmatch(COMFYUI_INSTANCE_ID_PATTERN, normalized) is None:
        raise ValueError(
            "ComfyUI instance ID must start with an ASCII letter or digit and contain "
            "only 1 to 64 ASCII letters, digits, '-' and '_'"
        )
    return normalized


def _validate_service_url(value: str, *, schemes: set[str], context: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in schemes
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        allowed = " or ".join(sorted(schemes))
        raise ValueError(f"{context} must be a credential-free {allowed} URL")
    return normalized


class ComfyUIInstanceConfig(BaseModel):
    """One private execution target from server/deployment configuration."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=240)
    base_url: str
    ws_url: str | None = None
    user: str | None = Field(default=None, max_length=255)
    concurrency: int | None = Field(default=None, ge=1, le=32)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_comfyui_instance_id(value)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("ComfyUI instance label must not be empty")
        return normalized

    @field_validator("description", "user")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        return normalized or None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_service_url(
            value,
            schemes={"http", "https"},
            context="ComfyUI base_url",
        )

    @field_validator("ws_url")
    @classmethod
    def validate_ws_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_service_url(
            value,
            schemes={"ws", "wss"},
            context="ComfyUI ws_url",
        )


class Settings(BaseSettings):
    """Server-only configuration. No value in this object is serialized to the browser."""

    model_config = SettingsConfigDict(
        env_prefix="CIF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    _comfyui_instances_explicitly_configured: bool = PrivateAttr(default=False)

    app_title: str = "ImageGen V2"
    listen_host: str = "0.0.0.0"  # noqa: S104 - configurable application listener default
    listen_port: int = 8000
    graceful_shutdown_timeout_seconds: int = Field(default=10, gt=0)
    data_dir: Path = Path("./backend/data")
    database_path: Path | None = None
    session_secret: SecretStr = Field(default=SecretStr(""))
    session_cookie_name: str = "cif_session"
    session_ttl_hours: int = 168
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    bootstrap_admin_username: str | None = None
    bootstrap_admin_temporary_password: SecretStr | None = None

    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_ws_url: str | None = None
    comfyui_instance_id: str = "default"
    comfyui_label: str = Field(default="Original", min_length=1, max_length=120)
    comfyui_description: str | None = Field(default=None, max_length=240)
    comfyui_user: str | None = None
    comfyui_instances: list[ComfyUIInstanceConfig] | None = None
    comfyui_additional_instances: list[ComfyUIInstanceConfig] = Field(default_factory=list)
    comfyui_default_instance_id: str | None = None
    comfyui_workflow_directory: str = "workflows"
    comfyui_concurrency: int = Field(default=1, ge=1, le=32)
    comfyui_listing_max_bytes: int = 4 * 1024 * 1024
    comfyui_object_info_max_bytes: int = 64 * 1024 * 1024
    comfyui_manifest_max_bytes: int = 1024 * 1024
    comfyui_workflow_max_bytes: int = 32 * 1024 * 1024
    comfyui_api_max_bytes: int = 32 * 1024 * 1024
    comfyui_history_max_bytes: int = 32 * 1024 * 1024
    comfyui_output_max_bytes: int = 128 * 1024 * 1024
    external_health_interval_seconds: float = 10.0
    dispatch_poll_seconds: float = 0.4
    dispatcher_heartbeat_stale_seconds: float = 30.0
    reconciliation_grace_seconds: float = 5.0

    ollama_base_url: str | None = None
    prompt_template_version: str = "v5"

    speech_to_text_url: str | None = None
    speech_to_text_api_key: SecretStr | None = None
    speech_to_text_model: str = "whisper-1"
    speech_to_text_max_bytes: int = 25 * 1024 * 1024
    speech_to_text_timeout_seconds: float = 120.0

    upload_max_bytes: int = 20 * 1024 * 1024
    upload_max_pixels: int = 50_000_000
    thumbnail_max_edge: int = 640

    login_max_attempts: int = 6
    login_window_seconds: int = 300
    login_block_seconds: int = 300

    log_level: str = "INFO"
    frontend_dist: Path = Path("./frontend/dist")
    enable_background_worker: bool = True
    test_mode: bool = False

    @field_validator("comfyui_base_url", "ollama_base_url", "speech_to_text_url")
    @classmethod
    def strip_trailing_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value

    @field_validator("speech_to_text_model")
    @classmethod
    def validate_speech_to_text_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("speech-to-text model must not be empty")
        return normalized

    @field_validator("comfyui_workflow_directory")
    @classmethod
    def validate_workflow_directory(cls, value: str) -> str:
        normalized = value.strip().strip("/")
        if not normalized or ".." in Path(normalized).parts:
            raise ValueError("workflow directory must be a safe relative namespace")
        return normalized

    @field_validator("comfyui_instance_id")
    @classmethod
    def validate_comfyui_instance_id(cls, value: str) -> str:
        return _validate_comfyui_instance_id(value)

    @field_validator("comfyui_default_instance_id")
    @classmethod
    def validate_comfyui_default_instance_id(cls, value: str | None) -> str | None:
        return _validate_comfyui_instance_id(value) if value is not None else None

    @field_validator("comfyui_label")
    @classmethod
    def normalize_comfyui_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("comfyui_label must not be empty")
        return normalized

    @field_validator("comfyui_description", "comfyui_user")
    @classmethod
    def normalize_optional_comfyui_text(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        return normalized or None

    @model_validator(mode="after")
    def derive_paths_and_validate(self) -> Settings:
        self.data_dir = self.data_dir.resolve()
        self.database_path = (self.database_path or self.data_dir / "app.db").resolve()
        self.frontend_dist = self.frontend_dist.resolve()
        if self.comfyui_concurrency < 1:
            raise ValueError("comfyui_concurrency must be at least one")
        configured_instances = self.comfyui_instances
        self._comfyui_instances_explicitly_configured = configured_instances is not None or bool(
            self.comfyui_additional_instances
        )
        if configured_instances is None:
            configured_instances = [
                ComfyUIInstanceConfig(
                    id=self.comfyui_instance_id,
                    label=self.comfyui_label,
                    description=self.comfyui_description,
                    base_url=self.comfyui_base_url,
                    ws_url=self.comfyui_ws_url,
                    user=self.comfyui_user,
                    concurrency=self.comfyui_concurrency,
                ),
                *self.comfyui_additional_instances,
            ]
        if not configured_instances:
            raise ValueError("comfyui_instances must configure at least one instance")
        instance_ids = [instance.id for instance in configured_instances]
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("comfyui_instances must use unique instance IDs")
        default_instance_id = self.comfyui_default_instance_id or instance_ids[0]
        if default_instance_id not in instance_ids:
            raise ValueError("comfyui_default_instance_id must match a configured instance")
        self.comfyui_default_instance_id = default_instance_id
        self.comfyui_instances = [
            instance.model_copy(
                update={
                    "concurrency": instance.concurrency or self.comfyui_concurrency,
                }
            )
            for instance in configured_instances
        ]
        for field_name in (
            "comfyui_listing_max_bytes",
            "comfyui_object_info_max_bytes",
            "comfyui_manifest_max_bytes",
            "comfyui_workflow_max_bytes",
            "comfyui_api_max_bytes",
            "comfyui_history_max_bytes",
            "comfyui_output_max_bytes",
            "speech_to_text_max_bytes",
        ):
            if getattr(self, field_name) < 1024:
                raise ValueError(f"{field_name} must be at least 1024 bytes")
        if self.session_ttl_hours < 1:
            raise ValueError("session_ttl_hours must be positive")
        if self.speech_to_text_timeout_seconds <= 0:
            raise ValueError("speech_to_text_timeout_seconds must be positive")
        if self.dispatch_poll_seconds <= 0:
            raise ValueError("dispatch_poll_seconds must be positive")
        minimum_heartbeat_window = max(15.0, self.dispatch_poll_seconds * 2)
        if self.dispatcher_heartbeat_stale_seconds <= minimum_heartbeat_window:
            raise ValueError(
                "dispatcher_heartbeat_stale_seconds must exceed the SQLite busy timeout "
                "and two dispatcher poll intervals"
            )
        secret = self.session_secret.get_secret_value()
        if not self.test_mode and len(secret) < 32:
            raise ValueError("CIF_SESSION_SECRET must contain at least 32 random characters")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("cookie_samesite=none requires cookie_secure=true")
        return self

    @property
    def database_url(self) -> str:
        assert self.database_path is not None
        return f"sqlite:///{self.database_path}"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def configured_comfyui_instances(self) -> tuple[ComfyUIInstanceConfig, ...]:
        assert self.comfyui_instances is not None
        return tuple(self.comfyui_instances)

    @property
    def comfyui_instance_configuration_mode(self) -> Literal["explicit", "legacy"]:
        return "explicit" if self._comfyui_instances_explicitly_configured else "legacy"

    @property
    def default_comfyui_instance(self) -> ComfyUIInstanceConfig:
        assert self.comfyui_default_instance_id is not None
        return next(
            instance
            for instance in self.configured_comfyui_instances
            if instance.id == self.comfyui_default_instance_id
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
