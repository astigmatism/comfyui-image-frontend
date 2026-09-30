from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
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
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

COMFYUI_INSTANCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


def _string_list(value: Any) -> Any:
    """Accept a JSON array or a comma/whitespace-delimited environment string.

    Appending one more ComfyUI image worker must stay a trivial edit, so the
    delimited form is first class. ``NoDecode`` keeps pydantic-settings from
    rejecting a non-JSON value before this validator runs.
    """

    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except ValueError as exc:
                raise ValueError("must be a JSON array or a comma-separated list") from exc
            if not isinstance(decoded, list):
                raise ValueError("must be a JSON array or a comma-separated list")
            return [str(item) for item in decoded]
        return [part for part in re.split(r"[,\s]+", text) if part]
    return value


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


def _derived_worker_identity(base_url: str) -> tuple[str, str]:
    """Return a stable routing ID and safe presentation label for a URL-only worker.

    The ID is derived from the host and port so it stays stable across restarts
    and across additions or removals elsewhere in the list; execution history
    keeps referring to the same worker identity.
    """

    netloc = urlparse(base_url).netloc
    sanitized = re.sub(r"[^A-Za-z0-9]+", "-", netloc).strip("-").lower()
    candidate = f"w-{sanitized}"
    if not sanitized or len(candidate) > 64:
        candidate = "w-" + hashlib.sha256(base_url.encode()).hexdigest()[:20]
    return _validate_comfyui_instance_id(candidate), f"ComfyUI {netloc}"[:120]


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


def _image_pool_membership(
    configured_instances: Sequence[ComfyUIInstanceConfig],
    default_instance_id: str,
    text_instance_id: str | None,
    worker_urls: Sequence[str],
    worker_ids: Sequence[str],
) -> tuple[str, ...]:
    """Ordered opt-in image pool: the primary, then named workers."""

    by_base_url = {instance.base_url: instance.id for instance in configured_instances}
    known = {instance.id for instance in configured_instances}
    pool = [default_instance_id]
    candidates = [by_base_url.get(value.strip().rstrip("/")) for value in worker_urls] + list(
        worker_ids
    )
    for candidate in candidates:
        if (
            candidate
            and candidate in known
            and candidate not in pool
            and candidate != text_instance_id
        ):
            pool.append(candidate)
    return tuple(pool)


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

    app_title: str = "ImageGen"
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
    comfyui_label: str = Field(default="Primary", min_length=1, max_length=120)
    comfyui_description: str | None = Field(default=None, max_length=240)
    comfyui_user: str | None = None
    comfyui_instances: list[ComfyUIInstanceConfig] | None = None
    comfyui_additional_instances: list[ComfyUIInstanceConfig] = Field(default_factory=list)
    # Appendable image-worker pool. Each entry is a credential-free base URL of a
    # ComfyUI container that duplicates the primary's publications and models.
    # Membership is opt-in: an instance configured above never executes image
    # work unless it appears here or in comfyui_image_worker_ids.
    comfyui_image_workers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    comfyui_image_worker_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    comfyui_default_instance_id: str | None = None
    comfyui_text_instance_id: str | None = None
    comfyui_workflow_directory: str = "workflows"
    comfyui_concurrency: int = Field(default=1, ge=1, le=32)
    # A matching secret must be configured on ComfyUI before model administration is exposed.
    lora_management_secret: SecretStr | None = None
    lora_upload_max_bytes: int = Field(default=4 * 1024 * 1024 * 1024, gt=0)
    comfyui_listing_max_bytes: int = 4 * 1024 * 1024
    comfyui_object_info_max_bytes: int = 64 * 1024 * 1024
    comfyui_manifest_max_bytes: int = 1024 * 1024
    comfyui_workflow_max_bytes: int = 32 * 1024 * 1024
    comfyui_api_max_bytes: int = 32 * 1024 * 1024
    comfyui_history_max_bytes: int = 32 * 1024 * 1024
    comfyui_output_max_bytes: int = 128 * 1024 * 1024
    # Budget for the cheap liveness probe and the companion cleanup call. A worker
    # that is busy generating still has to answer within this window, so it is
    # deliberately generous: a starved runtime answering slowly is not an outage,
    # and treating it as one withholds work from a GPU that is merely loaded.
    comfyui_health_timeout_seconds: float = 15.0
    external_health_interval_seconds: float = 10.0
    dispatch_poll_seconds: float = 0.4
    dispatcher_heartbeat_stale_seconds: float = 30.0
    reconciliation_grace_seconds: float = 5.0

    ollama_base_url: str | None = None
    ollama_model: str | None = "nighttime"
    ollama_api_key: SecretStr | None = None
    prompt_template_version: str = "v5"

    speech_to_text_url: str | None = None
    speech_to_text_api_key: SecretStr | None = None
    speech_to_text_model: str = "whisper-1"
    speech_to_text_max_bytes: int = 25 * 1024 * 1024
    speech_to_text_timeout_seconds: float = 120.0

    upload_max_bytes: int = 20 * 1024 * 1024
    upload_max_pixels: int = 50_000_000
    thumbnail_max_edge: int = 640

    # Staging area for request-scoped temporary files that can be far larger than
    # the container's /tmp. Deployments run with a read-only root filesystem and a
    # small tmpfs, so this defaults inside the writable data volume.
    temp_dir: Path | None = None
    download_max_bytes: int = Field(default=8 * 1024 * 1024 * 1024, gt=0)
    download_free_space_margin_bytes: int = Field(default=256 * 1024 * 1024, ge=0)

    login_max_attempts: int = 6
    login_window_seconds: int = 300
    login_block_seconds: int = 300

    log_level: str = "INFO"
    frontend_dist: Path = Path("./frontend/dist")
    enable_background_worker: bool = True
    test_mode: bool = False

    @field_validator("comfyui_base_url", "speech_to_text_url")
    @classmethod
    def strip_trailing_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value

    @field_validator("ollama_base_url")
    @classmethod
    def normalize_ollama_base_url(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        normalized = _validate_service_url(
            value, schemes={"http", "https"}, context="Ollama router base URL"
        )
        # Accept the router's advertised OpenAI base as well as its native API root.
        return normalized.removesuffix("/v1")

    @field_validator("ollama_model")
    @classmethod
    def normalize_ollama_model(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

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

    @field_validator("comfyui_text_instance_id", mode="before")
    @classmethod
    def normalize_text_assignment(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("comfyui_image_workers", "comfyui_image_worker_ids", mode="before")
    @classmethod
    def normalize_delimited_list(cls, value: object) -> object:
        return _string_list(value)

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

    def _validate_image_pool(
        self,
        configured_instances: list[ComfyUIInstanceConfig],
        default_instance_id: str,
    ) -> None:
        """Validate opt-in image-worker membership, synthesizing URL-only workers.

        The primary is always worker one. A configured instance joins the pool
        only when the operator names it, so an unrelated or anticipatory entry
        never silently receives image work.
        """

        by_id = {instance.id: instance for instance in configured_instances}
        by_base_url = {instance.base_url: instance for instance in configured_instances}
        primary = by_id[default_instance_id]
        for raw_url in self.comfyui_image_workers:
            base_url = _validate_service_url(
                raw_url, schemes={"http", "https"}, context="ComfyUI image worker URL"
            )
            if base_url in by_base_url:
                continue
            worker_id, label = _derived_worker_identity(base_url)
            conflict = by_id.get(worker_id)
            if conflict is not None:
                raise ValueError(
                    f"ComfyUI image worker {base_url} derives instance ID '{worker_id}', "
                    f"which already belongs to {conflict.base_url}"
                )
            worker = ComfyUIInstanceConfig(
                id=worker_id,
                label=label,
                base_url=base_url,
                user=primary.user,
                concurrency=primary.concurrency or self.comfyui_concurrency,
            )
            configured_instances.append(worker)
            by_id[worker.id] = worker
            by_base_url[base_url] = worker
        requested = [
            by_base_url[
                _validate_service_url(raw_url, schemes={"http", "https"}, context="worker")
            ].id
            for raw_url in self.comfyui_image_workers
        ]
        for raw_id in self.comfyui_image_worker_ids:
            worker_id = _validate_comfyui_instance_id(raw_id)
            if worker_id not in by_id:
                raise ValueError("comfyui_image_worker_ids must name configured instances")
            requested.append(worker_id)
        if self.comfyui_text_instance_id is not None and self.comfyui_text_instance_id in requested:
            raise ValueError("The assigned prompt instance cannot also be an image worker")

    @model_validator(mode="after")
    def derive_paths_and_validate(self) -> Settings:
        self.data_dir = self.data_dir.resolve()
        self.database_path = (self.database_path or self.data_dir / "app.db").resolve()
        self.temp_dir = (self.temp_dir or self.data_dir / "tmp").resolve()
        self.frontend_dist = self.frontend_dist.resolve()
        if self.comfyui_concurrency < 1:
            raise ValueError("comfyui_concurrency must be at least one")
        configured_instances = self.comfyui_instances
        self._comfyui_instances_explicitly_configured = (
            configured_instances is not None
            or "comfyui_additional_instances" in self.model_fields_set
            or bool(self.comfyui_image_workers)
            or bool(self.comfyui_image_worker_ids)
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
        configured_instances = list(configured_instances)
        if not configured_instances:
            raise ValueError("comfyui_instances must configure at least one instance")
        instance_ids = [instance.id for instance in configured_instances]
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("comfyui_instances must use unique instance IDs")
        default_instance_id = self.comfyui_default_instance_id or instance_ids[0]
        if default_instance_id not in instance_ids:
            raise ValueError("comfyui_default_instance_id must match a configured instance")
        self.comfyui_default_instance_id = default_instance_id
        if self.comfyui_text_instance_id is not None:
            self.comfyui_text_instance_id = _validate_comfyui_instance_id(
                self.comfyui_text_instance_id
            )
            if self.comfyui_text_instance_id not in instance_ids:
                raise ValueError("comfyui_text_instance_id must match a configured instance")
            if self.comfyui_text_instance_id == default_instance_id:
                raise ValueError("Image and prompt generation must use distinct ComfyUI instances")
        self._validate_image_pool(configured_instances, default_instance_id)
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
        if self.comfyui_health_timeout_seconds <= 0:
            raise ValueError("comfyui_health_timeout_seconds must be positive")
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
    def staging_dir(self) -> Path:
        """Writable location for request-scoped temporary files, never the container /tmp."""

        assert self.temp_dir is not None
        return self.temp_dir

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

    @property
    def image_pool_instance_ids(self) -> tuple[str, ...]:
        """Ordered image-worker identities; the primary is always the first member.

        Derived on access so a settings copy that changes stage assignments
        reports the pool that copy actually describes.
        """

        assert self.comfyui_default_instance_id is not None
        return _image_pool_membership(
            self.configured_comfyui_instances,
            self.comfyui_default_instance_id,
            self.comfyui_text_instance_id,
            self.comfyui_image_workers,
            self.comfyui_image_worker_ids,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
