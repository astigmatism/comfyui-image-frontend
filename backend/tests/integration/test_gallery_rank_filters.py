from app.models import Favorite, Generation, UserPreference
from tests.conftest import csrf
from tests.helpers import login_ready_admin, provision_user, restore_cookie
from tests.integration.test_gallery_selection import folder
from tests.integration.test_prompt_groups import seed_runs

GRADES = ("A", "B", "C", "D", "F")


def checkpoint(index):
    return f"cp1_{index:064x}"


def ranked_fixture(client, prompts=None):
    user, cookie = provision_user(client)
    ids = seed_runs(client, prompts or ["same prompt"] * 14)
    tiers = {grade: [checkpoint(index)] for index, grade in enumerate(GRADES)}
    with client.app.state.container.db.session_factory() as session:
        preference = session.get(UserPreference, user["id"])
        if preference is None:
            preference = UserPreference(user_id=user["id"])
            session.add(preference)
        preference.checkpoint_tiers_json = tiers
        for index, generation_id in enumerate(ids):
            item = session.get(Generation, generation_id)
            item.checkpoint_id = checkpoint(index // 2) if index < 12 else None
            item.status = "succeeded"
            if index % 2 == 0:
                session.add(Favorite(owner_id=user["id"], generation_id=generation_id))
        session.commit()
    return user, cookie, ids


def test_all_rank_and_favorites_combinations_share_membership_before_pagination(app_client):
    client = app_client
    _, _, ids = ranked_fixture(client)
    child = folder(client, "Folder")
    headers = {"X-CSRF-Token": csrf(client)}
    for mask in range(32):
        excluded = [grade for bit, grade in enumerate(GRADES) if mask & (1 << bit)]
        for mode in ("all", "favorites", "unfavorited"):
            scope = {
                "favorites_only": mode == "favorites",
                "unfavorited_only": mode == "unfavorited",
                "excluded_checkpoint_ranks": excluded,
            }
            params = [
                ("collection_id", ""),
                ("favorites_only", str(scope["favorites_only"]).lower()),
                ("unfavorited_only", str(scope["unfavorited_only"]).lower()),
                *[("excluded_checkpoint_ranks", grade) for grade in excluded],
            ]
            expected = [
                item
                for index, item in enumerate(ids)
                if (GRADES[index // 2] if index < 10 else "C") not in excluded
                and (mode == "all" or (index % 2 == 0) == (mode == "favorites"))
            ]
            inventory = client.get("/api/gallery/items", params=params)
            assert inventory.status_code == 200, inventory.text
            assert {item["id"] for item in inventory.json()["generations"]} == set(expected)
            assert inventory.json()["collection_ids"] == (
                [child["id"]] if not excluded and mode == "all" else []
            )
            seen = []
            cursor = []
            while True:
                response = client.get("/api/generations", params=[*params, ("limit", "3"), *cursor])
                assert response.status_code == 200, response.text
                page = response.json()
                seen.extend(item["id"] for item in page["items"])
                if not page["next_cursor"]:
                    break
                assert len(page["items"]) == 3
                cursor = [("cursor", page["next_cursor"])]
            assert seen == list(reversed(expected))
            groups = client.post(
                "/api/gallery/prompt-groups/lookup",
                headers=headers,
                json={**scope, "generation_ids": ids},
            )
            assert groups.status_code == 200, groups.text
            assert {row["generation_id"] for row in groups.json()} == set(expected)
            assert all(row["group"]["id"] == ids[0] for row in groups.json())
            assert all(row["group"]["generation_count"] == len(expected) for row in groups.json())
            members = client.get(
                f"/api/gallery/prompt-groups/{ids[0]}/members",
                params=[*params, ("selection", "true")],
            )
            assert members.status_code == 200, members.text
            assert [item["id"] for item in members.json()["items"]] == list(reversed(expected))


def test_rank_scope_revalidates_bulk_actions_and_is_owner_specific(app_client):
    client = app_client
    user, cookie, ids = ranked_fixture(client)
    scope = {"excluded_checkpoint_ranks": ["B", "C", "D", "F"]}
    headers = {"X-CSRF-Token": csrf(client)}
    # Rank-only filtering includes both favorites and unfavorited cards.
    response = client.post(
        "/api/gallery/favorite",
        headers=headers,
        json={"scope": scope, "generation_ids": ids[:2]},
    )
    assert response.status_code == 200, response.text
    with client.app.state.container.db.session_factory() as session:
        preference = session.get(UserPreference, user["id"])
        preference.checkpoint_tiers_json = {"F": [checkpoint(0)]}
        session.commit()
    response = client.post(
        "/api/gallery/delete",
        headers=headers,
        json={"scope": scope, "generation_ids": ids[:2]},
    )
    assert response.status_code == 409, response.text
    assert client.get(f"/api/generations/{ids[0]}").status_code == 200
    child = folder(client, "Folder")
    assert (
        client.post(
            "/api/gallery/favorite",
            headers=headers,
            json={"scope": scope, "collection_ids": [child["id"]]},
        ).status_code
        == 409
    )
    login_ready_admin(client)
    assert client.get("/api/gallery/items", params={"excluded_checkpoint_ranks": "F"}).json() == {
        "generations": [],
        "collection_ids": [],
    }
    restore_cookie(client, cookie)
    assert ids[0] not in {
        item["id"]
        for item in client.get(
            "/api/gallery/items", params={"excluded_checkpoint_ranks": "F"}
        ).json()["generations"]
    }


def test_filter_preserves_prompt_runs_and_reduces_group_selection_limit(app_client):
    client = app_client
    user, _, ids = ranked_fixture(client, ["repeat", "hidden", "repeat"])
    with client.app.state.container.db.session_factory() as session:
        session.get(Generation, ids[1]).checkpoint_id = checkpoint(4)
        session.commit()
    response = client.post(
        "/api/gallery/prompt-groups/lookup",
        headers={"X-CSRF-Token": csrf(client)},
        json={"generation_ids": ids, "excluded_checkpoint_ranks": ["F"]},
    )
    assert response.status_code == 200, response.text
    groups = {row["generation_id"]: row["group"] for row in response.json()}
    assert groups[ids[0]]["id"] != groups[ids[2]]["id"]
    assert groups[ids[2]]["previous_generation_id"] == ids[1]
    assert groups[ids[2]]["generation_count"] == 1
    many = seed_runs(client, ["big group"] * 526)
    with client.app.state.container.db.session_factory() as session:
        for index, generation_id in enumerate(many):
            session.get(Generation, generation_id).checkpoint_id = checkpoint(index % 2)
        # Exercise a large rank map without exceeding SQLite's bind limit.
        session.get(UserPreference, user["id"]).checkpoint_tiers_json = {
            "A": [checkpoint(0)],
            "F": [checkpoint(index) for index in range(1, 25000)],
        }
        session.commit()
    params = {"excluded_checkpoint_ranks": "F", "selection": "true"}
    response = client.get(f"/api/gallery/prompt-groups/{many[0]}/members", params=params)
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 263
    assert (
        client.get(f"/api/gallery/prompt-groups/{many[0]}/members?selection=true").status_code
        == 422
    )


def test_invalid_rank_and_incompatible_favorites_filters_are_rejected(app_client):
    client = app_client
    _, _, ids = ranked_fixture(client)
    for endpoint in (
        "/api/generations",
        "/api/gallery/items",
        f"/api/gallery/prompt-groups/{ids[0]}/members",
    ):
        assert client.get(endpoint, params={"excluded_checkpoint_ranks": "E"}).status_code == 422
        assert (
            client.get(
                endpoint,
                params={
                    "favorites_only": "true",
                    "unfavorited_only": "true",
                },
            ).status_code
            == 422
        )
    headers = {"X-CSRF-Token": csrf(client)}
    assert (
        client.post(
            "/api/gallery/prompt-groups/lookup",
            headers=headers,
            json={
                "generation_ids": ids,
                "excluded_checkpoint_ranks": ["E"],
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/gallery/favorite",
            headers=headers,
            json={
                "generation_ids": ids[:1],
                "scope": {"excluded_checkpoint_ranks": ["E"]},
            },
        ).status_code
        == 422
    )


def lora(index):
    return f"lr1_{index:064x}"


def test_lora_rank_filter_hides_images_that_used_any_excluded_lora(app_client):
    from app.models import GenerationLora

    client = app_client
    user, _cookie = provision_user(client)
    ids = seed_runs(client, ["lora prompt"] * 5)
    usage = {
        0: [lora(1)],  # A
        1: [lora(1), lora(5)],  # A and F
        2: [lora(9)],  # unranked, so C
        3: [],  # no LoRA
        4: [lora(4)],  # D
    }
    with client.app.state.container.db.session_factory() as session:
        preference = session.get(UserPreference, user["id"])
        if preference is None:
            preference = UserPreference(user_id=user["id"])
            session.add(preference)
        preference.lora_tiers_json = {"A": [lora(1)], "D": [lora(4)], "F": [lora(5)]}
        for index, generation_id in enumerate(ids):
            session.get(Generation, generation_id).status = "succeeded"
            for position, identity in enumerate(usage[index]):
                session.add(
                    GenerationLora(
                        generation_id=generation_id,
                        position=position,
                        lora_identity=identity,
                        label=f"LoRA {position}",
                        strength=1.0,
                    )
                )
        session.commit()

    def visible(*excluded):
        params = [("collection_id", ""), *[("excluded_lora_ranks", grade) for grade in excluded]]
        response = client.get("/api/gallery/items", params=params)
        assert response.status_code == 200, response.text
        listed = client.get("/api/generations", params=params).json()["items"]
        assert {item["id"] for item in listed} == {
            item["id"] for item in response.json()["generations"]
        }
        return {ids.index(item["id"]) for item in response.json()["generations"]}

    assert visible() == {0, 1, 2, 3, 4}
    assert visible("F") == {0, 2, 3, 4}
    assert visible("C") == {0, 1, 3, 4}
    assert visible("A", "B", "C", "D", "F") == {3}
    assert visible("D", "F") == {0, 2, 3}
    page = client.get("/api/generations", params=[("collection_id", "")]).json()["items"]
    by_id = {item["id"]: item for item in page}
    assert [entry["lora_identity"] for entry in by_id[ids[1]]["loras"]] == [lora(1), lora(5)]
    items = client.get("/api/gallery/items", params=[("collection_id", "")]).json()
    selection = {item["id"]: item for item in items["generations"]}
    assert selection[ids[1]]["lora_identities"] == [lora(1), lora(5)]


def test_lora_tiers_are_validated_and_revision_checked(app_client):
    client = app_client
    provision_user(client)
    headers = {"X-CSRF-Token": csrf(client)}
    current = client.get("/api/preferences").json()
    saved = client.put(
        "/api/preferences",
        headers=headers,
        json={"expected_revision": current["revision"], "lora_tiers": {"A": [lora(1)]}},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["lora_tiers"] == {"A": [lora(1)]}
    for invalid in (
        {"A": [checkpoint(1)]},
        {"Z": [lora(1)]},
        {"A": [lora(1)], "B": [lora(1)]},
    ):
        rejected = client.put(
            "/api/preferences",
            headers=headers,
            json={"expected_revision": saved.json()["revision"], "lora_tiers": invalid},
        )
        assert rejected.status_code == 422, rejected.text
    missing_revision = client.put("/api/preferences", headers=headers, json={"lora_tiers": {}})
    assert missing_revision.status_code == 409
