from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server-only configuration. No value in this object is serialized to the browser."""

    model_config = SettingsConfigDict(
        env_prefix="CIF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

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
    comfyui_user: str | None = None
    comfyui_workflow_directory: str = "workflows"
    comfyui_concurrency: int = 1
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
    prompt_template_version: str = "v4"

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
        normalized = value.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("ComfyUI instance ID must contain 1 to 64 characters")
        if not all(character.isalnum() or character in {"-", "_"} for character in normalized):
            raise ValueError("ComfyUI instance ID may contain only letters, digits, '-' and '_'")
        return normalized

    @field_validator("comfyui_user")
    @classmethod
    def normalize_comfyui_user(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        return normalized or None

    @model_validator(mode="after")
    def derive_paths_and_validate(self) -> Settings:
        self.data_dir = self.data_dir.resolve()
        self.database_path = (self.database_path or self.data_dir / "app.db").resolve()
        self.frontend_dist = self.frontend_dist.resolve()
        if self.comfyui_concurrency < 1:
            raise ValueError("comfyui_concurrency must be at least one")
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
