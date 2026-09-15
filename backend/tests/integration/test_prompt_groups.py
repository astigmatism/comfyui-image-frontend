from datetime import timedelta
from hashlib import sha256

from app.models import Generation, uuid_str
from app.services.prompt_groups import lookup_groups, prompt_changes
from sqlalchemy import event, func, select
from tests.conftest import csrf
from tests.helpers import create_generation, login_ready_admin, provision_user, restore_cookie


def seed_runs(client, prompts):
    seed = create_generation(client, prompts[0])
    with client.app.state.container.db.session_factory() as session:
        original = session.get(Generation, seed["id"])
        original.accepted_at = session.scalar(select(func.max(Generation.accepted_at))) + timedelta(
            seconds=1
        )
        values = {
            column.key: getattr(original, column.key) for column in Generation.__table__.columns
        }
        ids = [original.id]
        for index, prompt in enumerate(prompts[1:], 1):
            item = Generation(
                **{
                    **values,
                    "id": uuid_str(),
                    "correlation_id": uuid_str(),
                    "comfyui_client_id": uuid_str(),
                    "queue_seq": original.queue_seq + index,
                    "accepted_at": original.accepted_at + timedelta(seconds=index),
                    "final_prompt": prompt,
                    "prompt_fingerprint": sha256(prompt.encode()).hexdigest(),
                }
            )
            session.add(item)
            ids.append(item.id)
        # The fixture workflow may wrap the authored prompt; use exact test text throughout.
        original.final_prompt = prompts[0]
        original.prompt_fingerprint = sha256(prompts[0].encode()).hexdigest()
        session.commit()
        return ids


def lookup(client, ids, collection_id=None):
    response = client.post(
        "/api/gallery/prompt-groups/lookup",
        headers={"X-CSRF-Token": csrf(client)},
        json={"generation_ids": ids, "collection_id": collection_id},
    )
    assert response.status_code == 200, response.text
    return {item["generation_id"]: item["group"] for item in response.json()}


def test_groups_cross_pages_and_reused_prompt_remains_a_separate_run(app_client):
    client = app_client
    user, _ = provision_user(client)
    ids = seed_runs(
        client,
        ["quiet lake at sunrise"] * 70
        + ["quiet lake at sunset"] * 3
        + ["quiet lake at sunrise"] * 4,
    )
    page = client.get("/api/generations?collection_id=&limit=24").json()
    assert all(
        "final_prompt" not in item and len(item["prompt_fingerprint"]) == 64
        for item in page["items"]
    )
    groups = lookup(client, [item["id"] for item in page["items"]])
    assert groups[ids[-1]]["id"] == ids[73]
    assert groups[ids[-1]]["generation_count"] == 4
    assert groups[ids[70]]["generation_count"] == 3
    assert groups[ids[69]]["generation_count"] == 70
    assert groups[ids[69]]["id"] == ids[0]
    next_page = client.get(
        "/api/generations", params={"collection_id": "", "cursor": page["next_cursor"]}
    ).json()
    assert set(
        item["group"]["id"]
        for item in client.post(
            "/api/gallery/prompt-groups/lookup",
            headers={"X-CSRF-Token": csrf(client)},
            json={"generation_ids": [item["id"] for item in next_page["items"]]},
        ).json()
    ) == {ids[0]}
    skipped = client.get(
        "/api/generations", params={"collection_id": "", "cursor": groups[ids[-1]]["after_cursor"]}
    ).json()
    assert skipped["items"][0]["id"] == ids[72]
    members_url = f"/api/gallery/prompt-groups/{ids[0]}/members"
    first = client.get(members_url).json()
    second = client.get(members_url, params={"cursor": first["next_cursor"]}).json()
    assert len(first["items"]) == 60 and len(second["items"]) == 10
    selected = client.get(members_url, params={"selection": "true"}).json()
    assert {item["id"] for item in selected["items"]} == set(ids[:70])
    changes = client.get(f"/api/gallery/prompt-groups/{ids[-1]}/changes").json()
    assert changes["edit_count"] == 1
    assert any(
        part == {"kind": "removed", "text": "sunset"}
        for snippet in changes["snippets"]
        for part in snippet
    )
    assert client.get(f"/api/gallery/prompt-groups/{ids[0]}/changes").json()["first_prompt"]
    statements = []
    engine = client.app.state.container.db.engine

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with client.app.state.container.db.session_factory() as session:
            lookup_groups(session, str(user["id"]), None, ids[:1])
            one = len(statements)
            statements.clear()
            lookup_groups(session, str(user["id"]), None, ids)
        assert one == len(statements) == 1
        assert "final_prompt" not in " ".join(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_group_selection_limit_ownership_and_recalculation_after_move(app_client):
    client = app_client
    _, cookie = provision_user(client)
    ids = seed_runs(client, ["A"] * 2 + ["B"] + ["A"] * 2)
    before = lookup(client, ids)
    assert before[ids[0]]["generation_count"] == 2
    folder = client.post(
        "/api/collections", headers={"X-CSRF-Token": csrf(client)}, json={"name": "Moved"}
    ).json()
    response = client.post(
        "/api/gallery/transfer",
        headers={"X-CSRF-Token": csrf(client)},
        json={
            "operation": "move",
            "generation_ids": [ids[2]],
            "collection_id": folder["id"],
        },
    )
    assert response.status_code == 200, response.text
    assert lookup(client, ids)[ids[0]]["generation_count"] == 4
    assert lookup(client, [ids[2]], folder["id"])[ids[2]]["generation_count"] == 1
    assert client.get(f"/api/gallery/prompt-groups/{ids[2]}/members").status_code == 404
    login_ready_admin(client)
    assert lookup(client, ids) == {}
    for suffix in ("members", "changes"):
        assert client.get(f"/api/gallery/prompt-groups/{ids[0]}/{suffix}").status_code == 404
    restore_cookie(client, cookie)
    many = seed_runs(client, ["huge group"] * 501)
    response = client.get(f"/api/gallery/prompt-groups/{many[0]}/members?selection=true")
    assert response.status_code == 422
    assert "500" in response.text


def test_prompt_diffs_bound_large_replacements_and_preserve_formatting():
    change = prompt_changes("sunrise", "sunset")
    assert change.edit_count == 1
    assert [part.kind for part in change.snippets[0]] == ["removed", "added"]
    assert prompt_changes("a b", "a\nb").edit_count == 1
    assert prompt_changes("same", "same").edit_count == 0
    long = prompt_changes("before " * 20000, "after " * 20000)
    assert len(long.model_dump_json()) < 3000
    shared = "x" * 20000
    assert len(prompt_changes(shared + " red", shared + " blue").model_dump_json()) < 3000
