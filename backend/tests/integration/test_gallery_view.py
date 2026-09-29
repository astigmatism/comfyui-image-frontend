from io import BytesIO
from zipfile import ZipFile

from app.main import create_app
from app.models import Favorite, Generation
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.conftest import csrf
from tests.helpers import (
    create_generation,
    login_ready_admin,
    provision_user,
    restore_cookie,
    wait_for_status,
)
from tests.integration.test_gallery_selection import folder, transfer
from tests.integration.test_prompt_groups import seed_runs


def test_whole_view_snapshot_exclusions_and_bulk_actions_beyond_500(app_client):
    client = app_client
    user, _ = provision_user(client)
    ids = seed_runs(client, ["collection snapshot"] * 526)
    with client.app.state.container.db.session_factory() as session:
        for item in session.scalars(select(Generation).where(Generation.owner_id == user["id"])):
            item.status = "succeeded"
        session.commit()
    destination = folder(client, "Destination")
    nested = folder(client, "Nested", destination["id"])
    inventory = client.get("/api/gallery/items").json()
    assert len(inventory["generations"]) == 526
    assert inventory["collection_ids"] == [destination["id"]]
    assert nested["id"] not in inventory["collection_ids"]
    assert set(inventory["generations"][0]) == {
        "id",
        "status",
        "collection_id",
        "is_favorite",
        "image_count",
    }
    # Explicit IDs freeze membership, independent of arrival time or pagination.
    late = create_generation(client, "arrived after select all")
    selected = [item for item in ids if item != ids[0]]
    payload = {"scope": {"collection_id": None}, "generation_ids": selected}
    headers = {"X-CSRF-Token": csrf(client)}
    assert (
        client.post(
            "/api/gallery/favorite", headers=headers, json={"generation_ids": selected}
        ).status_code
        == 422
    )
    favorites = client.post("/api/gallery/favorite", headers=headers, json=payload)
    assert favorites.status_code == 200, favorites.text
    assert set(favorites.json()["generation_ids"]) == set(selected)
    with client.app.state.container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Favorite)) == 525
    assert not client.get(f"/api/generations/{late['id']}").json()["is_favorite"]
    assert not client.get(f"/api/generations/{ids[0]}").json()["is_favorite"]
    copied = client.post(
        "/api/gallery/transfer",
        headers=headers,
        json={
            **payload,
            "operation": "copy",
            "collection_id": destination["id"],
        },
    )
    assert copied.status_code == 200, copied.text
    assert len(copied.json()["generation_ids"]) == 525
    moved = client.post(
        "/api/gallery/transfer",
        headers=headers,
        json={
            **payload,
            "operation": "move",
            "collection_id": destination["id"],
        },
    )
    assert moved.status_code == 200, moved.text
    assert len(moved.json()["generation_ids"]) == 525
    # Stale scope cannot accidentally act on moved or filtered-out cards.
    assert client.post("/api/gallery/favorite", headers=headers, json=payload).status_code == 409
    deleted = client.post(
        "/api/gallery/delete",
        headers=headers,
        json={
            **payload,
            "scope": {"collection_id": destination["id"]},
        },
    )
    assert deleted.status_code == 200, deleted.text
    assert len(deleted.json()["items"]) == 525
    assert all(item["status"] == "deleted" for item in deleted.json()["items"])
    assert client.get(f"/api/generations/{ids[0]}").status_code == 200
    assert client.get(f"/api/generations/{late['id']}").status_code == 200


def test_view_inventory_scope_favorites_and_ownership(app_client):
    client = app_client
    _, cookie = provision_user(client)
    home = create_generation(client, "home")
    hidden = create_generation(client, "hidden")
    parent = folder(client, "Parent")
    child = folder(client, "Child", parent["id"])
    assert transfer(client, "move", [hidden["id"]], destination=parent["id"]).status_code == 200
    headers = {"X-CSRF-Token": csrf(client)}
    assert (
        client.post(
            "/api/gallery/favorite",
            headers=headers,
            json={
                "generation_ids": [home["id"]],
                "collection_ids": [parent["id"]],
            },
        ).status_code
        == 200
    )
    favorites = client.get("/api/gallery/items?favorites_only=true").json()
    assert [item["id"] for item in favorites["generations"]] == [home["id"]]
    # A filtered view lists generation cards only: a folder would carry contents
    # that ignore the filter, so no whole-view selection can include one.
    assert favorites["collection_ids"] == []
    unfavorited = client.get("/api/gallery/items?unfavorited_only=true").json()
    assert [item["id"] for item in unfavorited["generations"]] == []
    assert unfavorited["collection_ids"] == []
    inner = client.get("/api/gallery/items", params={"collection_id": parent["id"]}).json()
    assert [item["id"] for item in inner["generations"]] == [hidden["id"]]
    assert inner["collection_ids"] == [child["id"]]
    inner_unfavorited = client.get(
        "/api/gallery/items",
        params={"collection_id": parent["id"], "unfavorited_only": True},
    ).json()
    assert [item["id"] for item in inner_unfavorited["generations"]] == [hidden["id"]]
    assert inner_unfavorited["collection_ids"] == []
    assert (
        client.get(
            "/api/gallery/items",
            params={"favorites_only": True, "unfavorited_only": True},
        ).status_code
        == 422
    )
    assert client.get(
        "/api/gallery/items",
        params={
            "collection_id": parent["id"],
            "favorites_only": True,
        },
    ).json() == {"generations": [], "collection_ids": []}
    assert (
        client.post(
            "/api/gallery/favorite",
            headers=headers,
            json={
                "scope": {"collection_id": parent["id"], "favorites_only": True},
                "generation_ids": [hidden["id"]],
            },
        ).status_code
        == 409
    )
    # An unfavorited-view selection must still be unfavorited, and a filtered view
    # never holds a folder, so either drift is rejected before any mutation.
    assert (
        client.post(
            "/api/gallery/delete",
            headers=headers,
            json={
                "scope": {"collection_id": None, "unfavorited_only": True},
                "generation_ids": [home["id"]],
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/gallery/transfer",
            headers=headers,
            json={
                "scope": {"collection_id": None, "unfavorited_only": True},
                "collection_ids": [parent["id"]],
                "operation": "move",
                "collection_id": child["id"],
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/gallery/favorite",
            headers=headers,
            json={
                "scope": {
                    "collection_id": None,
                    "favorites_only": True,
                    "unfavorited_only": True,
                },
                "generation_ids": [home["id"]],
            },
        ).status_code
        == 422
    )
    # Favoriting an unfavorited-view selection is the one bulk action that ends the
    # view's own membership: it succeeds once, then the same snapshot is stale.
    unfavorited_scope = {
        "scope": {"collection_id": parent["id"], "unfavorited_only": True},
        "generation_ids": [hidden["id"]],
    }
    assert (
        client.post("/api/gallery/favorite", headers=headers, json=unfavorited_scope).status_code
        == 200
    )
    assert (
        client.post("/api/gallery/favorite", headers=headers, json=unfavorited_scope).status_code
        == 409
    )
    login_ready_admin(client)
    assert client.get("/api/gallery/items").json() == {"generations": [], "collection_ids": []}
    assert (
        client.get("/api/gallery/items", params={"collection_id": parent["id"]}).status_code == 404
    )
    assert (
        client.post(
            "/api/gallery/favorite",
            headers={"X-CSRF-Token": csrf(client)},
            json={
                "scope": {},
                "generation_ids": [home["id"]],
            },
        ).status_code
        == 404
    )
    restore_cookie(client, cookie)
    assert client.get(f"/api/generations/{home['id']}").json()["is_favorite"]


def test_gallery_layout_preference_default_validation_and_persistence(app_client):
    client = app_client
    provision_user(client)
    prefs = client.get("/api/preferences").json()
    assert prefs["settings"]["gallery_layout"] == "grouped"
    saved = client.put(
        "/api/preferences",
        headers={"X-CSRF-Token": csrf(client)},
        json={
            "settings": {**prefs["settings"], "gallery_layout": "classic"},
            "expected_revision": prefs["revision"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert client.get("/api/preferences").json()["settings"]["gallery_layout"] == "classic"
    assert (
        client.put(
            "/api/preferences",
            headers={"X-CSRF-Token": csrf(client)},
            json={
                "settings": {"gallery_layout": "masonry"},
                "expected_revision": saved.json()["revision"],
            },
        ).status_code
        == 422
    )


def test_scoped_download_respects_exclusions_and_selected_folder_contents(
    settings_factory,
    fake_state,
):
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client)
        parent = folder(client, "Selected folder")
        inside = create_generation(client, "download inside")
        outside = create_generation(client, "download excluded")
        inside_detail = wait_for_status(client, inside["id"], "succeeded")
        wait_for_status(client, outside["id"], "succeeded")
        assert transfer(client, "move", [inside["id"]], destination=parent["id"]).status_code == 200
        headers = {"X-CSRF-Token": csrf(client)}
        assert (
            client.post(
                "/api/gallery/favorite",
                headers=headers,
                json={
                    "collection_ids": [parent["id"]],
                },
            ).status_code
            == 200
        )
        response = client.post(
            "/api/gallery/download",
            headers=headers,
            json={
                "scope": {"collection_id": None},
                "collection_ids": [parent["id"]],
            },
        )
        assert response.status_code == 200, response.text
        with ZipFile(BytesIO(response.content)) as archive:
            names = archive.namelist()
            assert len(names) == inside_detail["image_count"]
            assert all(inside["id"] in name and outside["id"] not in name for name in names)
        # Folder tiles exist in the unfiltered view only, so a folder can never be
        # part of a filtered view's snapshot, favorited or not.
        for scope in ({"favorites_only": True}, {"unfavorited_only": True}):
            assert (
                client.post(
                    "/api/gallery/download",
                    headers=headers,
                    json={
                        "scope": {"collection_id": None, **scope},
                        "collection_ids": [parent["id"]],
                    },
                ).status_code
                == 409
            )


def test_whole_favorites_view_download_reports_capacity_without_a_server_error(
    settings_factory,
    fake_state,
):
    """Filtering to favorites, selecting the whole view, then downloading it.

    This reproduces the reported failure: the archive is staged in the container's
    temporary directory, which deployments cap at a small tmpfs, so an oversized
    selection used to surface as HTTP 500 from a bare OSError.
    """

    del fake_state
    app = create_app(settings_factory(enable_background_worker=True))
    with TestClient(app) as client:
        settings = app.state.container.settings
        provision_user(client)
        inside = folder(client, "Keepers")
        images = [create_generation(client, f"favorite keeper {index}") for index in range(3)]
        details = [wait_for_status(client, image["id"], "succeeded") for image in images]
        assert (
            transfer(client, "move", [images[0]["id"]], destination=inside["id"]).status_code == 200
        )
        headers = {"X-CSRF-Token": csrf(client)}
        assert (
            client.post(
                "/api/gallery/favorite",
                headers=headers,
                json={"generation_ids": [image["id"] for image in images[1:]]},
            ).status_code
            == 200
        )
        inventory = client.get("/api/gallery/items?favorites_only=true").json()
        selected = [item["id"] for item in inventory["generations"]]
        assert sorted(selected) == sorted(image["id"] for image in images[1:])
        payload = {
            "scope": {"collection_id": None, "favorites_only": True},
            "generation_ids": selected,
        }

        response = client.post("/api/gallery/download", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        expected = sum(detail["image_count"] for detail in details[1:])
        with ZipFile(BytesIO(response.content)) as archive:
            assert len(archive.namelist()) == expected

        # The staging ceiling is reported as an actionable error, never HTTP 500.
        settings.download_max_bytes = 1
        refused = client.post("/api/gallery/download", headers=headers, json=payload)
        assert refused.status_code == 507, refused.text
        assert refused.json()["error"]["code"] == "download_too_large"
        assert not list(settings.staging_dir.iterdir())
