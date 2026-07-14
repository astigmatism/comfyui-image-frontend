from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import change_password, create_user, csrf, login

ADMIN_TEMP = "AdminTemporary123!"
ADMIN_PASSWORD = "AdminPermanent123!"
USER_TEMP = "UserTemporary123!"
USER_PASSWORD = "UserPermanent123!"


def ready_admin(client: TestClient) -> dict[str, Any]:
    session = login(client, "admin", ADMIN_TEMP)
    if session["user"]["must_change_password"]:
        change_password(client, ADMIN_PASSWORD)
    return client.get("/api/auth/session").json()["user"]


def provision_user(
    client: TestClient,
    *,
    username: str = "artist.one",
    temporary_password: str = USER_TEMP,
    permanent_password: str = USER_PASSWORD,
) -> tuple[dict[str, Any], str]:
    ready_admin(client)
    user = create_user(client, username, temporary_password)
    client.cookies.clear()
    login(client, username, temporary_password)
    change_password(client, permanent_password)
    cookie = client.cookies.get("cif_session")
    assert cookie
    return user, cookie


def login_ready_admin(client: TestClient) -> str:
    client.cookies.clear()
    login(client, "admin", ADMIN_PASSWORD)
    cookie = client.cookies.get("cif_session")
    assert cookie
    return cookie


def restore_cookie(client: TestClient, cookie: str, *, name: str = "cif_session") -> None:
    client.cookies.clear()
    client.cookies.set(name, cookie)


def first_profile(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/workflows")
    assert response.status_code == 200, response.text
    profiles = response.json()
    assert profiles
    return next(item for item in profiles if item["display_name"] == "Krea 2 NSFW V4")


def generation_payload(
    client: TestClient,
    prompt: str,
    *,
    seed: int | str = "random",
    source_upload_id: str | None = None,
    preset_id: str | None = None,
) -> dict[str, Any]:
    profile = first_profile(client)
    controls: dict[str, Any] = {
        "prompt": prompt,
        "seed": seed,
        "width": 512,
        "height": 512,
        "enable_seedvr2_upscale": False,
        "knpv4_1_strength": 1.0,
    }
    # Publication v1 deliberately exposes only manifest-declared scalar parameters. Upload
    # fixtures remain useful for authorization/storage tests but are not injected into this source.
    del source_upload_id
    payload: dict[str, Any] = {
        "source_key": profile["source_key"],
        "revision": profile["revision"],
        "parameters": controls,
    }
    if preset_id is not None:
        payload["preset_id"] = preset_id
    return payload


def create_generation(client: TestClient, prompt: str, **kwargs: Any) -> dict[str, Any]:
    payload = generation_payload(client, prompt, **kwargs)
    response = client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(client)},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def wait_for_generation(
    client: TestClient,
    generation_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/generations/{generation_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if predicate(last):
            return last
        time.sleep(0.03)
    raise AssertionError(f"generation did not reach expected state; last={last}")


def wait_for_status(
    client: TestClient,
    generation_id: str,
    *statuses: str,
    timeout: float = 8.0,
) -> dict[str, Any]:
    expected = set(statuses)
    return wait_for_generation(
        client,
        generation_id,
        lambda value: value["status"] in expected,
        timeout=timeout,
    )
