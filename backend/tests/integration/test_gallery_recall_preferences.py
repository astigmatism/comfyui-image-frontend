from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import csrf
from tests.helpers import (
    create_generation,
    provision_user,
    restore_cookie,
    wait_for_status,
)


def test_cursor_pagination_is_newest_first_and_preference_persists(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="gallery.user")
        created = [create_generation(first, f"gallery {index}", seed=index) for index in range(7)]
        expected = [item["id"] for item in reversed(created)]
        first_page = first.get("/api/generations?limit=3").json()
        second_page = first.get(
            "/api/generations", params={"limit": 3, "cursor": first_page["next_cursor"]}
        ).json()
        third_page = first.get(
            "/api/generations", params={"limit": 3, "cursor": second_page["next_cursor"]}
        ).json()
        actual = [
            item["id"]
            for page in (first_page, second_page, third_page)
            for item in page["items"]
        ]
        assert actual == expected
        assert len(set(actual)) == 7
        assert third_page["next_cursor"] is None

        saved = first.put(
            "/api/preferences",
            headers={"X-CSRF-Token": csrf(first)},
            json={"gallery_scale": 93},
        )
        assert saved.status_code == 200
        assert saved.json()["gallery_scale"] == 93
        assert first.put(
            "/api/preferences",
            headers={"X-CSRF-Token": csrf(first)},
            json={"gallery_scale": 101},
        ).status_code == 422

    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie, name=settings.session_cookie_name)
        assert second.get("/api/preferences").json()["gallery_scale"] == 93


def test_recall_is_disabled_when_exact_workflow_disappears_but_history_remains(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    original_files = copy.deepcopy(fake_state.workflow_files)
    with TestClient(create_app(settings)) as client:
        user, user_cookie = provision_user(client, username="recall.user")
        generation = create_generation(client, "historical exact request", seed=551)
        complete = wait_for_status(client, generation["id"], "succeeded")
        assert complete["recall_available"] is True

        client.cookies.clear()
        # The bootstrap administrator already has a permanent password after provisioning.
        from tests.conftest import login
        from tests.helpers import ADMIN_PASSWORD

        login(client, "admin", ADMIN_PASSWORD)
        fake_state.workflow_files = {
            key: value
            for key, value in fake_state.workflow_files.items()
            if not key.startswith("profiles/progressive.")
        }
        refreshed = client.post(
            "/api/admin/workflows/refresh",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert refreshed.status_code == 200

        restore_cookie(client, user_cookie, name=settings.session_cookie_name)
        gallery = client.get("/api/generations").json()["items"]
        item = next(value for value in gallery if value["id"] == generation["id"])
        assert item["recall_available"] is False
        assert "Original workflow version" in item["recall_unavailable_reason"]
        recall = client.get(f"/api/generations/{generation['id']}/recall")
        assert recall.status_code == 200
        assert recall.json()["available"] is False
        assert client.get(f"/api/generations/{generation['id']}").status_code == 200
        assert client.get(complete["artifacts"][-1]["content_url"]).status_code == 200
    fake_state.workflow_files = original_files
