from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from app import security
from app.config import Settings
from app.main import create_app
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from tests.fake_services import FakeServiceState, LiveFakeServer


@pytest.fixture(scope="session", autouse=True)
def fast_test_password_hasher() -> Iterator[None]:
    """Keep integration tests deterministic without weakening production Argon2id settings."""

    original = security._password_hasher
    security._password_hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    try:
        yield
    finally:
        security._password_hasher = original


@pytest.fixture(scope="session")
def fake_services() -> Iterator[LiveFakeServer]:
    server = LiveFakeServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def fake_state(fake_services: LiveFakeServer) -> FakeServiceState:
    fake_services.state.reset_runtime()
    return fake_services.state


@pytest.fixture
def settings_factory(tmp_path: Path, fake_services: LiveFakeServer) -> Callable[..., Settings]:
    counter = 0

    def factory(**overrides: object) -> Settings:
        nonlocal counter
        counter += 1
        data_dir = tmp_path / f"app-data-{counter}"
        values: dict[str, object] = {
            "app_title": "Test Image Appliance",
            "data_dir": data_dir,
            "database_path": data_dir / "app.db",
            "session_secret": "test-session-secret-material-0123456789",
            "bootstrap_admin_username": "admin",
            "bootstrap_admin_temporary_password": "AdminTemporary123!",
            "comfyui_base_url": fake_services.base_url,
            "comfyui_ws_url": fake_services.ws_url,
            "comfyui_instance_id": "test-instance",
            "comfyui_user": "fixture-user",
            "comfyui_workflow_directory": "workflows",
            "ollama_base_url": fake_services.base_url,
            "frontend_dist": Path(__file__).resolve().parents[2] / "frontend" / "dist",
            "dispatch_poll_seconds": 0.02,
            "external_health_interval_seconds": 0.05,
            "reconciliation_grace_seconds": 0.05,
            "enable_background_worker": False,
            "test_mode": True,
            "login_block_seconds": 1,
            "log_level": "WARNING",
        }
        values.update(overrides)
        return Settings(**values)

    return factory


@pytest.fixture
def app_client(
    settings_factory: Callable[..., Settings], fake_state: FakeServiceState
) -> Iterator[TestClient]:
    del fake_state  # The dependency guarantees reset before application startup discovery.
    app = create_app(settings_factory())
    with TestClient(app) as client:
        yield client


def auth_session(client: TestClient) -> dict[str, object]:
    response = client.get("/api/auth/session")
    assert response.status_code == 200, response.text
    return response.json()


def login(client: TestClient, username: str, password: str) -> dict[str, object]:
    anonymous = auth_session(client)
    token = anonymous["csrf_token"]
    response = client.post(
        "/api/auth/login",
        headers={"X-CSRF-Token": str(token)},
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def csrf(client: TestClient) -> str:
    response = client.get("/api/auth/session")
    assert response.status_code == 200, response.text
    token = response.json().get("csrf_token")
    assert token
    return str(token)


def change_password(
    client: TestClient,
    new_password: str,
    current_password: str | None = None,
) -> None:
    response = client.post(
        "/api/auth/password",
        headers={"X-CSRF-Token": csrf(client)},
        json={"current_password": current_password, "new_password": new_password},
    )
    assert response.status_code == 204, response.text


def create_user(
    client: TestClient,
    username: str,
    temporary_password: str,
) -> dict[str, object]:
    response = client.post(
        "/api/admin/users",
        headers={"X-CSRF-Token": csrf(client)},
        json={"username": username, "temporary_password": temporary_password},
    )
    assert response.status_code == 201, response.text
    return response.json()
