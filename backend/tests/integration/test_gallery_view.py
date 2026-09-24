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
    assert favorites["collection_ids"] == [parent["id"]]
    inner = client.get("/api/gallery/items", params={"collection_id": parent["id"]}).json()
    assert [item["id"] for item in inner["generations"]] == [hidden["id"]]
    assert inner["collection_ids"] == [child["id"]]
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
                "scope": {"collection_id": None, "favorites_only": True},
                "collection_ids": [parent["id"]],
            },
        )
        assert response.status_code == 200, response.text
        with ZipFile(BytesIO(response.content)) as archive:
            names = archive.namelist()
            assert len(names) == inside_detail["image_count"]
            assert all(inside["id"] in name and outside["id"] not in name for name in names)
