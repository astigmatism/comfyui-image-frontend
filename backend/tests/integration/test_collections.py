from __future__ import annotations

import time

from app.main import create_app
from app.models import AuditLog, Collection, Generation
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.conftest import change_password, create_user, csrf, login
from tests.helpers import (
    ADMIN_PASSWORD,
    USER_TEMP,
    create_generation,
    generation_payload,
    provision_user,
    restore_cookie,
    wait_for_status,
)


def _create_collection(
    client: TestClient,
    name: str,
    parent_id: str | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/collections",
        headers={"X-CSRF-Token": csrf(client)},
        json={"name": name, "parent_id": parent_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_in_collection(
    client: TestClient,
    prompt: str,
    collection_id: str | None,
) -> dict[str, object]:
    payload = generation_payload(client, prompt)
    payload["collection_id"] = collection_id
    response = client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(client)},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_collection_crud_name_validation_depth_and_explicit_root_move(
    settings_factory, fake_state
) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=False))) as client:
        provision_user(client, username="collections.crud")
        for invalid_name in ("", "   "):
            response = client.post(
                "/api/collections",
                headers={"X-CSRF-Token": csrf(client)},
                json={"name": invalid_name},
            )
            assert response.status_code == 422
        assert client.get("/api/collections").json() == []

        parent = _create_collection(client, " Alpha ")
        child = _create_collection(client, "Beta", str(parent["id"]))
        assert parent["name"] == "Alpha"
        renamed = client.patch(
            f"/api/collections/{child['id']}",
            headers={"X-CSRF-Token": csrf(client)},
            json={"name": " Beta Prime "},
        )
        assert renamed.status_code == 200
        assert renamed.json()["parent_id"] == parent["id"]
        moved = client.patch(
            f"/api/collections/{child['id']}",
            headers={"X-CSRF-Token": csrf(client)},
            json={"parent_id": None},
        )
        assert moved.status_code == 200
        assert moved.json()["parent_id"] is None

        parent_id = str(parent["id"])
        for level in range(2, 6):
            parent_id = str(_create_collection(client, f"Level {level}", parent_id)["id"])
        too_deep = client.post(
            "/api/collections",
            headers={"X-CSRF-Token": csrf(client)},
            json={"name": "Too deep", "parent_id": parent_id},
        )
        assert too_deep.status_code == 409
        assert too_deep.json()["error"]["code"] == "collection_depth"


def test_collection_previews_toggle_persists_independently_per_collection(
    settings_factory, fake_state
) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=False))) as client:
        provision_user(client, username="collections.previews.toggle")
        first = _create_collection(client, "Toggle first")
        second = _create_collection(client, "Toggle second")
        assert first["previews_enabled"] is True
        assert second["previews_enabled"] is True

        hidden = client.patch(
            f"/api/collections/{first['id']}",
            headers={"X-CSRF-Token": csrf(client)},
            json={"previews_enabled": False},
        )
        assert hidden.status_code == 200
        assert hidden.json()["previews_enabled"] is False
        assert hidden.json()["name"] == "Toggle first"
        listed = {item["id"]: item for item in client.get("/api/collections").json()}
        assert listed[first["id"]]["previews_enabled"] is False
        assert listed[second["id"]]["previews_enabled"] is True
        assert listed[first["id"]]["previews"] == []

        renamed = client.patch(
            f"/api/collections/{first['id']}",
            headers={"X-CSRF-Token": csrf(client)},
            json={"name": "Toggle first renamed"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["previews_enabled"] is False

        shown = client.patch(
            f"/api/collections/{first['id']}",
            headers={"X-CSRF-Token": csrf(client)},
            json={"previews_enabled": True},
        )
        assert shown.status_code == 200
        assert shown.json()["previews_enabled"] is True
        assert (
            client.patch(
                f"/api/collections/{first['id']}",
                headers={"X-CSRF-Token": csrf(client)},
                json={},
            ).status_code
            == 422
        )


def test_generation_scopes_moves_and_cross_owner_targets_are_not_found(
    settings_factory, fake_state
) -> None:
    del fake_state
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        _, owner_cookie = provision_user(client, username="collections.owner")
        first = _create_collection(client, "First")
        second = _create_collection(client, "Second")
        root_generation = create_generation(client, "root generation")
        filed = _create_in_collection(client, "filed generation", str(first["id"]))

        unscoped = client.get("/api/generations?limit=60").json()["items"]
        root = client.get("/api/generations", params={"collection_id": ""}).json()["items"]
        first_page = client.get("/api/generations", params={"collection_id": first["id"]}).json()[
            "items"
        ]
        assert {item["id"] for item in unscoped} == {root_generation["id"], filed["id"]}
        assert [item["id"] for item in root] == [root_generation["id"]]
        assert [item["id"] for item in first_page] == [filed["id"]]
        assert filed["collection_id"] == first["id"]

        moved = client.post(
            f"/api/generations/{filed['id']}/move",
            headers={"X-CSRF-Token": csrf(client)},
            json={"collection_id": second["id"]},
        )
        assert moved.status_code == 200
        assert moved.json()["collection_id"] == second["id"]
        unfiled = client.post(
            f"/api/generations/{filed['id']}/move",
            headers={"X-CSRF-Token": csrf(client)},
            json={"collection_id": None},
        )
        assert unfiled.status_code == 200
        assert unfiled.json()["collection_id"] is None

        client.cookies.clear()
        login(client, "admin", ADMIN_PASSWORD)
        create_user(client, "collections.other", USER_TEMP)
        client.cookies.clear()
        login(client, "collections.other", USER_TEMP)
        change_password(client, "OtherCollectionsPermanent123!")
        for response in (
            client.post(
                "/api/collections",
                headers={"X-CSRF-Token": csrf(client)},
                json={"name": "Foreign child", "parent_id": first["id"]},
            ),
            client.patch(
                f"/api/collections/{first['id']}",
                headers={"X-CSRF-Token": csrf(client)},
                json={"name": "Foreign rename"},
            ),
            client.delete(
                f"/api/collections/{first['id']}",
                headers={"X-CSRF-Token": csrf(client)},
            ),
            client.post(
                f"/api/generations/{filed['id']}/move",
                headers={"X-CSRF-Token": csrf(client)},
                json={"collection_id": first["id"]},
            ),
        ):
            assert response.status_code == 404, response.text

        invalid_accept = generation_payload(client, "foreign filing")
        invalid_accept["collection_id"] = first["id"]
        response = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=invalid_accept,
        )
        assert response.status_code == 404
        restore_cookie(client, owner_cookie, name=settings.session_cookie_name)


def test_collection_previews_are_direct_image_only_newest_four_and_owner_scoped(
    settings_factory, fake_state
) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        user, _ = provision_user(client, username="collections.previews")
        collection = _create_collection(client, "Previews")
        child = _create_collection(client, "Nested", str(collection["id"]))
        created = [
            _create_in_collection(client, f"preview {index}", str(collection["id"]))
            for index in range(5)
        ]
        nested = _create_in_collection(client, "nested preview", str(child["id"]))
        for generation in [*created, nested]:
            wait_for_status(client, str(generation["id"]), "succeeded")

        listed = {item["id"]: item for item in client.get("/api/collections").json()}
        preview_ids = [item["generation_id"] for item in listed[collection["id"]]["previews"]]
        assert listed[collection["id"]]["generation_count"] == 5
        assert preview_ids == [item["id"] for item in reversed(created[-4:])]
        assert nested["id"] not in preview_ids

        container = client.app.state.container
        with container.db.session_factory() as session:
            pending = session.get(Generation, created[-1]["id"])
            assert pending is not None
            pending.pending_delete = True
            session.commit()
        listed = {item["id"]: item for item in client.get("/api/collections").json()}
        assert listed[collection["id"]]["generation_count"] == 4
        assert created[-1]["id"] not in {
            item["generation_id"] for item in listed[collection["id"]]["previews"]
        }

        with container.db.session_factory() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Collection)
                    .where(Collection.owner_id == user["id"])
                )
                == 2
            )


def test_recursive_delete_removes_terminal_contents_files_and_writes_audits(
    settings_factory, fake_state
) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        user, _ = provision_user(client, username="collections.delete")
        parent = _create_collection(client, "Delete parent")
        child = _create_collection(client, "Delete child", str(parent["id"]))
        generation = _create_in_collection(client, "delete collection result", str(child["id"]))
        complete = wait_for_status(client, str(generation["id"]), "succeeded")
        artifact_url = complete["artifacts"][-1]["content_url"]
        assert client.get(artifact_url).status_code == 200

        deleted = client.delete(
            f"/api/collections/{parent['id']}",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert deleted.status_code == 204
        assert client.get(artifact_url).status_code == 404
        assert client.get(f"/api/generations/{generation['id']}").status_code == 404
        assert client.get("/api/collections").json() == []

        with client.app.state.container.db.session_factory() as session:
            audits = list(
                session.scalars(
                    select(AuditLog).where(
                        AuditLog.actor_user_id == user["id"],
                        AuditLog.action == "collection_deleted",
                    )
                )
            )
            assert {audit.target_id for audit in audits} == {parent["id"], child["id"]}


def test_scoped_collection_cursor_pagination_is_stable(settings_factory, fake_state) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=False))) as client:
        provision_user(client, username="collections.pages")
        collection = _create_collection(client, "Paged")
        created = [
            _create_in_collection(client, f"paged {index}", str(collection["id"]))
            for index in range(7)
        ]
        pages = []
        cursor = None
        while True:
            parameters = {"collection_id": collection["id"], "limit": 3}
            if cursor is not None:
                parameters["cursor"] = cursor
            page = client.get("/api/generations", params=parameters).json()
            pages.append(page)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        actual = [item["id"] for page in pages for item in page["items"]]
        assert actual == [item["id"] for item in reversed(created)]
        assert len(actual) == len(set(actual)) == 7


def test_recursive_delete_with_active_generation_returns_202_and_reconciles(
    settings_factory, fake_state
) -> None:
    fake_state.slow_stage_delay = 0.35
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client, username="collections.active.delete")
        collection = _create_collection(client, "Active deletion")
        generation = _create_in_collection(
            client,
            "slow active collection deletion",
            str(collection["id"]),
        )
        wait_for_status(client, str(generation["id"]), "running")

        deleted = client.delete(
            f"/api/collections/{collection['id']}",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert deleted.status_code == 202
        assert client.get("/api/collections").json() == []
        assert client.get("/api/generations", params={"collection_id": ""}).json()["items"] == []

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if client.get(f"/api/generations/{generation['id']}").status_code == 404:
                break
            time.sleep(0.03)
        else:
            raise AssertionError("pending collection generation was not reconciled")
