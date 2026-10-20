from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.main import create_app
from app.models import LoraGroup, LoraGroupMembership
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import csrf
from tests.helpers import login_ready_admin, provision_user, ready_admin, restore_cookie
from tests.publication_fixtures import moody_lora_library_files

PATH = "/api/admin/lora-library/krea2/groups"
READ = "/api/lora-library/krea2/groups"


def test_shared_groups_crud_membership_fallback_and_permissions(fake_state, settings_factory):
    fake_state.workflow_files = moody_lora_library_files()
    with TestClient(create_app(settings_factory())) as client:
        assert client.get(READ).status_code == 401
        _, user_cookie = provision_user(client, username="group.artist")
        original = client.get(READ).json()
        assert original["revision"] == 0
        assert original["groups"] == [
            {"id": "uncategorized", "name": "Uncategorized", "system": True}
        ]
        assert set(original["assignments"].values()) == {"uncategorized"}
        identities = list(original["assignments"])
        assert len(identities) == 2
        body = {"revision": 0, "name": "Portraits"}
        assert (
            client.post(PATH, json=body, headers={"X-CSRF-Token": csrf(client)}).status_code == 403
        )
        login_ready_admin(client)
        assert client.post(PATH, json=body).status_code == 403
        headers = {"X-CSRF-Token": csrf(client)}
        response = client.post(PATH, json=body, headers=headers)
        assert response.status_code == 201, response.text
        created = response.json()
        group_id = created["groups"][0]["id"]
        assert created["revision"] == 1
        assert set(created["assignments"].values()) == {"uncategorized"}  # empty groups persist
        assert client.post(PATH, json=body, headers=headers).status_code == 409
        assert (
            client.post(
                PATH, json={"revision": 1, "name": " portraits "}, headers=headers
            ).status_code
            == 409
        )
        assert (
            client.post(
                PATH, json={"revision": 1, "name": "Uncategorized"}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(PATH, json={"revision": 1, "name": "  "}, headers=headers).status_code
            == 422
        )
        membership = f"/api/admin/lora-library/krea2/group-memberships/{identities[0]}"
        assigned = client.put(
            membership, json={"revision": 1, "group_id": group_id}, headers=headers
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["assignments"][identities[0]] == group_id
        # The same identity has one group in every workflow in this family.
        sources = client.get("/api/workflows").json()
        seen = 0
        for source in sources:
            detail = client.get(f"/api/workflows/{source['source_key']}").json()
            for control in detail["interface"]["inputs"]:
                if control["type"] == "lora_stack":
                    view = client.get(
                        f"/api/workflows/{source['source_key']}/lora-groups/{control['id']}"
                    ).json()
                    assert view["assignments"][identities[0]] == group_id
                    seen += 1
        assert seen == 2
        renamed = client.patch(
            f"{PATH}/{group_id}", json={"revision": 2, "name": " Characters "}, headers=headers
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["groups"][0]["name"] == "Characters"
        assert (
            client.request(
                "DELETE", f"{PATH}/uncategorized", json={"revision": 3}, headers=headers
            ).status_code
            == 404
        )
        removed = client.request(
            "DELETE", f"{PATH}/{group_id}", json={"revision": 3}, headers=headers
        )
        assert removed.status_code == 200, removed.text
        assert set(removed.json()["assignments"].values()) == {"uncategorized"}
        assert len(removed.json()["groups"]) == 1
        restore_cookie(client, user_cookie)
        assert client.get(READ).json() == removed.json()
        assert len(client.get("/api/workflows").json()) == len(sources)


def test_group_membership_rejects_other_families_and_unknown_loras(fake_state, settings_factory):
    fake_state.workflow_files = moody_lora_library_files()
    with TestClient(create_app(settings_factory())) as client:
        ready_admin(client)
        headers = {"X-CSRF-Token": csrf(client)}
        identity = next(iter(client.get(READ).json()["assignments"]))
        with client.app.state.container.db.session_factory() as session:
            group = LoraGroup(library_key="other", name="Characters", name_normalized="characters")
            session.add(group)
            session.commit()
            other_id = group.id
        membership = f"/api/admin/lora-library/krea2/group-memberships/{identity}"
        assert (
            client.put(
                membership, json={"revision": 0, "group_id": other_id}, headers=headers
            ).status_code
            == 404
        )
        created = client.post(PATH, json={"revision": 0, "name": "Styles"}, headers=headers).json()
        group_id = created["groups"][0]["id"]
        assert (
            client.put(
                "/api/admin/lora-library/krea2/group-memberships/lr1_" + "f" * 64,
                json={"revision": 1, "group_id": group_id},
                headers=headers,
            ).status_code
            == 404
        )
        assert (
            client.put(
                membership, json={"revision": 1, "group_id": group_id}, headers=headers
            ).status_code
            == 200
        )
        second = client.post(
            PATH, json={"revision": 2, "name": "Characters"}, headers=headers
        ).json()
        second_id = next(group["id"] for group in second["groups"] if group["name"] == "Characters")
        reassigned = client.put(
            membership, json={"revision": 3, "group_id": second_id}, headers=headers
        )
        assert reassigned.status_code == 200, reassigned.text
        with client.app.state.container.db.session_factory() as session:
            memberships = list(session.scalars(select(LoraGroupMembership)))
            assert len(memberships) == 1
            assert memberships[0].group_id == second_id
        fallback = client.put(
            membership, json={"revision": 4, "group_id": "uncategorized"}, headers=headers
        )
        assert fallback.status_code == 200, fallback.text
        with client.app.state.container.db.session_factory() as session:
            assert list(session.scalars(select(LoraGroupMembership))) == []
        assert client.get(READ).json()["revision"] == 5
        assert client.get("/api/lora-library/missing/groups").status_code == 404


def test_concurrent_group_edits_require_review(fake_state, settings_factory):
    fake_state.workflow_files = moody_lora_library_files()
    with TestClient(create_app(settings_factory())) as client:
        ready_admin(client)
        headers = {"X-CSRF-Token": csrf(client)}

        def create(name):
            return client.post(
                PATH, json={"revision": 0, "name": name}, headers=headers
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ["Characters", "Styles"]))
        assert sorted(results) == [201, 409]
        assert len(client.get(READ).json()["groups"]) == 2


def test_shared_group_order_is_complete_conditional_and_survives_other_edits(
    fake_state, settings_factory
):
    fake_state.workflow_files = moody_lora_library_files()
    order_path = "/api/admin/lora-library/krea2/group-order"
    with TestClient(create_app(settings_factory())) as client:
        _, user_cookie = provision_user(client, username="group.order")
        assert (
            client.put(
                order_path,
                json={"revision": 0, "group_ids": ["uncategorized"]},
                headers={"X-CSRF-Token": csrf(client)},
            ).status_code
            == 403
        )
        login_ready_admin(client)
        assert (
            client.put(order_path, json={"revision": 0, "group_ids": ["uncategorized"]}).status_code
            == 403
        )
        headers = {"X-CSRF-Token": csrf(client)}
        beta = client.post(PATH, json={"revision": 0, "name": "Beta"}, headers=headers).json()[
            "groups"
        ][0]["id"]
        created = client.post(PATH, json={"revision": 1, "name": "Alpha"}, headers=headers).json()
        alpha = created["groups"][0]["id"]
        assert [group["name"] for group in created["groups"]] == ["Alpha", "Beta", "Uncategorized"]
        with client.app.state.container.db.session_factory() as session:
            other = LoraGroup(library_key="other", name="Other", name_normalized="other")
            session.add(other)
            session.commit()
            other_id = other.id
        for invalid in [
            [alpha, beta],
            [alpha, alpha, "uncategorized"],
            [other_id, beta, "uncategorized"],
        ]:
            response = client.put(
                order_path, json={"revision": 2, "group_ids": invalid}, headers=headers
            )
            assert response.status_code == 422, response.text
            assert client.get(READ).json()["revision"] == 2
        wanted = ["uncategorized", beta, alpha]
        ordered = client.put(order_path, json={"revision": 2, "group_ids": wanted}, headers=headers)
        assert ordered.status_code == 200, ordered.text
        assert [group["id"] for group in ordered.json()["groups"]] == wanted
        assert ordered.json()["assignments"] == created["assignments"]
        assert (
            client.put(
                order_path, json={"revision": 2, "group_ids": wanted[::-1]}, headers=headers
            ).status_code
            == 409
        )
        renamed = client.patch(
            f"{PATH}/{beta}", json={"revision": 3, "name": "Zeta"}, headers=headers
        )
        assert [group["id"] for group in renamed.json()["groups"]] == wanted
        added = client.post(PATH, json={"revision": 4, "name": "New group"}, headers=headers).json()
        new_id = next(group["id"] for group in added["groups"] if group["name"] == "New group")
        assert [group["id"] for group in added["groups"]] == [new_id, *wanted]
        removed = client.request("DELETE", f"{PATH}/{beta}", json={"revision": 5}, headers=headers)
        assert [group["id"] for group in removed.json()["groups"]] == [
            new_id,
            "uncategorized",
            alpha,
        ]
        restore_cookie(client, user_cookie)
        assert client.get(READ).json() == removed.json()
        for source in client.get("/api/workflows").json():
            detail = client.get(f"/api/workflows/{source['source_key']}").json()
            for control in detail["interface"]["inputs"]:
                if control["type"] == "lora_stack":
                    view = client.get(
                        f"/api/workflows/{source['source_key']}/lora-groups/{control['id']}"
                    ).json()
                    assert view["groups"] == removed.json()["groups"]


def test_completed_lora_removal_prunes_group_membership(fake_state, settings_factory):
    from app.domain.lora_identity import lora_identity_v1

    fake_state.workflow_files = moody_lora_library_files()
    with TestClient(create_app(settings_factory())) as client:
        ready_admin(client)
        headers = {"X-CSRF-Token": csrf(client)}
        created = client.post(
            PATH, json={"revision": 0, "name": "Characters"}, headers=headers
        ).json()
        identity = lora_identity_v1("private/a.safetensors")
        assert identity in created["assignments"]
        assigned = client.put(
            f"/api/admin/lora-library/krea2/group-memberships/{identity}",
            json={"revision": 1, "group_id": created["groups"][0]["id"]},
            headers=headers,
        )
        assert assigned.status_code == 200, assigned.text
        client.app.state.container.lora_operations._reconcile_after_success(
            [], {"action": "remove", "id": "a"}, "private/a.safetensors"
        )
        after = client.get(READ).json()
        assert after["assignments"][identity] == "uncategorized"
        assert after["revision"] == 3
        assert after["groups"][0]["name"] == "Characters"
