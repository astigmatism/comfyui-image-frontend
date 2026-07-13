from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import change_password, create_user, csrf, login
from tests.helpers import (
    ADMIN_PASSWORD,
    USER_TEMP,
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


def test_favorites_are_idempotent_owner_scoped_and_preserve_generation_history(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        _, owner_cookie = provision_user(client, username="favorite.owner")
        generation = create_generation(client, "favorite lighthouse", seed=411)

        created = client.put(
            f"/api/generations/{generation['id']}/favorite",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert created.status_code == 200
        favorite = created.json()
        assert favorite["generation"]["id"] == generation["id"]
        assert favorite["generation"]["is_favorite"] is True
        assert favorite["final_prompt"] == "favorite lighthouse"

        duplicate = client.put(
            f"/api/generations/{generation['id']}/favorite",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == favorite["id"]

        page = client.get("/api/favorites").json()
        assert [item["generation"]["id"] for item in page["items"]] == [generation["id"]]
        gallery_item = next(
            item
            for item in client.get("/api/generations").json()["items"]
            if item["id"] == generation["id"]
        )
        assert gallery_item["is_favorite"] is True

        client.cookies.clear()
        login(client, "admin", ADMIN_PASSWORD)
        create_user(client, "favorite.other", USER_TEMP)
        client.cookies.clear()
        login(client, "favorite.other", USER_TEMP)
        change_password(client, "OtherPermanent123!")

        assert client.get("/api/favorites").json()["items"] == []
        assert (
            client.put(
                f"/api/generations/{generation['id']}/favorite",
                headers={"X-CSRF-Token": csrf(client)},
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/api/generations/{generation['id']}/favorite",
                headers={"X-CSRF-Token": csrf(client)},
            ).status_code
            == 404
        )

        restore_cookie(client, owner_cookie, name=settings.session_cookie_name)
        removed = client.delete(
            f"/api/generations/{generation['id']}/favorite",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert removed.status_code == 204
        assert client.get("/api/favorites").json()["items"] == []
        assert client.get(f"/api/generations/{generation['id']}").status_code == 200
        updated = client.get("/api/generations").json()["items"]
        assert (
            next(item for item in updated if item["id"] == generation["id"])["is_favorite"] is False
        )


def test_favorites_cursor_pagination_is_newest_first(settings_factory, fake_state) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="favorite.pages")
        generations = [
            create_generation(client, f"favorite page {index}", seed=index)
            for index in range(5)
        ]
        for generation in generations:
            response = client.put(
                f"/api/generations/{generation['id']}/favorite",
                headers={"X-CSRF-Token": csrf(client)},
            )
            assert response.status_code == 200

        first = client.get("/api/favorites?limit=2").json()
        second = client.get(
            "/api/favorites", params={"limit": 2, "cursor": first["next_cursor"]}
        ).json()
        third = client.get(
            "/api/favorites", params={"limit": 2, "cursor": second["next_cursor"]}
        ).json()
        actual = [
            item["generation"]["id"]
            for page in (first, second, third)
            for item in page["items"]
        ]
        assert actual == [generation["id"] for generation in reversed(generations)]
        assert third["next_cursor"] is None
