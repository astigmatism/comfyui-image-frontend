from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import auth_session, change_password, create_user, csrf, login


ADMIN_TEMP = "AdminTemporary123!"
ADMIN_PASSWORD = "AdminPermanent123!"
USER_TEMP = "UserTemporary123!"
USER_PASSWORD = "UserPermanent123!"


def test_bootstrap_admin_forced_change_and_csrf(app_client: TestClient) -> None:
    anonymous = auth_session(app_client)
    assert anonymous["authenticated"] is False
    assert app_client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_TEMP}
    ).status_code == 403

    signed_in = login(app_client, "admin", ADMIN_TEMP)
    assert signed_in["user"]["role"] == "admin"
    assert signed_in["user"]["must_change_password"] is True
    blocked = app_client.get("/api/workflows")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "password_change_required"

    no_csrf = app_client.post(
        "/api/auth/password", json={"current_password": None, "new_password": ADMIN_PASSWORD}
    )
    assert no_csrf.status_code == 403
    change_password(app_client, ADMIN_PASSWORD)
    ready = auth_session(app_client)
    assert ready["user"]["must_change_password"] is False
    workflows = app_client.get("/api/workflows")
    assert workflows.status_code == 200
    assert [item["workflow_id"] for item in workflows.json()] == ["fake-progressive-v1"]


def test_admin_creates_user_and_temporary_password_is_forced(app_client: TestClient) -> None:
    login(app_client, "admin", ADMIN_TEMP)
    change_password(app_client, ADMIN_PASSWORD)
    created = create_user(app_client, "artist.one", USER_TEMP)
    assert created["role"] == "user"
    assert created["must_change_password"] is True

    duplicate = app_client.post(
        "/api/admin/users",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"username": "ARTIST.ONE", "temporary_password": "AnotherPassword123!"},
    )
    assert duplicate.status_code == 409

    app_client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf(app_client)})
    signed_in = login(app_client, "artist.one", USER_TEMP)
    assert signed_in["user"]["must_change_password"] is True
    assert app_client.get("/api/generations").status_code == 403
    change_password(app_client, USER_PASSWORD)
    assert app_client.get("/api/generations").status_code == 200


def test_password_reset_revokes_existing_sessions(settings_factory) -> None:
    settings = settings_factory()
    app = create_app(settings)
    with TestClient(app) as client:
        login(client, "admin", ADMIN_TEMP)
        change_password(client, ADMIN_PASSWORD)
        user = create_user(client, "reset.user", USER_TEMP)

        client.cookies.clear()
        login(client, "reset.user", USER_TEMP)
        change_password(client, USER_PASSWORD)
        assert auth_session(client)["authenticated"] is True
        user_cookie = client.cookies.get(settings.session_cookie_name)
        assert user_cookie

        # Keep the user's server-side session alive while authenticating as the
        # administrator in the same test process. This avoids starting two app
        # lifespans against the same SQLite file.
        client.cookies.clear()
        login(client, "admin", ADMIN_PASSWORD)
        reset = client.post(
            f"/api/admin/users/{user['id']}/reset-password",
            headers={"X-CSRF-Token": csrf(client)},
            json={"temporary_password": "ResetTemporary123!"},
        )
        assert reset.status_code == 204

        client.cookies.clear()
        client.cookies.set(settings.session_cookie_name, user_cookie)
        assert auth_session(client)["authenticated"] is False
        client.cookies.clear()
        signed_in = login(client, "reset.user", "ResetTemporary123!")
        assert signed_in["user"]["must_change_password"] is True


def test_restart_does_not_reapply_bootstrap_password(settings_factory) -> None:
    settings = settings_factory()
    with TestClient(create_app(settings)) as client:
        login(client, "admin", ADMIN_TEMP)
        change_password(client, ADMIN_PASSWORD)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/auth/session")
        token = response.json()["csrf_token"]
        old = client.post(
            "/api/auth/login",
            headers={"X-CSRF-Token": token},
            json={"username": "admin", "password": ADMIN_TEMP},
        )
        assert old.status_code == 401
        signed_in = login(client, "admin", ADMIN_PASSWORD)
        assert signed_in["user"]["must_change_password"] is False


def test_login_throttling_blocks_even_a_correct_password_until_backoff_expires(
    settings_factory,
) -> None:
    import time

    settings = settings_factory(
        login_max_attempts=2,
        login_window_seconds=60,
        login_block_seconds=1,
    )
    with TestClient(create_app(settings)) as client:
        anonymous = auth_session(client)
        token = anonymous["csrf_token"]
        for _ in range(2):
            failed = client.post(
                "/api/auth/login",
                headers={"X-CSRF-Token": str(token)},
                json={"username": "admin", "password": "wrong-password"},
            )
            assert failed.status_code == 401
        blocked = client.post(
            "/api/auth/login",
            headers={"X-CSRF-Token": str(token)},
            json={"username": "admin", "password": ADMIN_TEMP},
        )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "login_throttled"
        time.sleep(1.05)
        recovered = client.post(
            "/api/auth/login",
            headers={"X-CSRF-Token": str(token)},
            json={"username": "admin", "password": ADMIN_TEMP},
        )
        assert recovered.status_code == 200


def test_empty_database_without_bootstrap_configuration_fails_startup(settings_factory) -> None:
    import pytest

    settings = settings_factory(
        bootstrap_admin_username=None,
        bootstrap_admin_temporary_password=None,
    )
    with pytest.raises(RuntimeError, match="Empty database requires"):
        with TestClient(create_app(settings)):
            pass
