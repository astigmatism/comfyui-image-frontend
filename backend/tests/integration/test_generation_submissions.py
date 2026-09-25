import base64
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from app.models import Generation, GenerationEvent, GenerationRun, GenerationSubmission
from sqlalchemy import func, select
from tests.conftest import csrf
from tests.helpers import generation_payload, login_ready_admin, provision_user, restore_cookie


def submit(client, payload, key, *, batch=False):
    return client.post(
        "/api/generations/batch" if batch else "/api/generations",
        headers={"X-CSRF-Token": csrf(client), "Idempotency-Key": key},
        json=payload,
    )


def test_four_item_batch_keeps_utc_acceptance_times_across_reads_and_cursors(app_client):
    provision_user(app_client)
    valid = generation_payload(app_client, "four cards in one prompt group")
    payload = {"items": [valid] * 4}
    key = str(uuid4())
    response = submit(app_client, payload, key, batch=True)
    assert response.status_code == 201, response.text
    accepted = [item["generation"] for item in response.json()["items"]]
    timestamps = {item["id"]: item["accepted_at"] for item in accepted}
    assert len(timestamps) == 4
    assert all(value.endswith("Z") for value in timestamps.values())

    def assert_timestamps(items):
        assert {item["id"]: item["accepted_at"] for item in items} == timestamps

    history = app_client.get("/api/generations?collection_id=").json()["items"]
    assert_timestamps(history)
    assert_timestamps(
        [app_client.get(f"/api/generations/{item['id']}").json() for item in accepted]
    )
    receipt = app_client.get(f"/api/generation-submissions/{key}").json()["result"]
    assert_timestamps([item["generation"] for item in receipt["items"]])
    replay = submit(app_client, payload, key, batch=True).json()
    assert_timestamps([item["generation"] for item in replay["items"]])
    members_url = f"/api/gallery/prompt-groups/{history[-1]['id']}/members"
    assert_timestamps(app_client.get(members_url).json()["items"])

    # Both server-issued cursors and cursors built from normalized summaries
    # must continue to page through SQLite's timezone-less stored timestamps.
    first = app_client.get("/api/generations?collection_id=&limit=2").json()
    last = first["items"][-1]
    cursors = [first["next_cursor"]]
    for timestamp in (last["accepted_at"], last["accepted_at"].removesuffix("Z")):
        cursors.append(
            base64.urlsafe_b64encode(
                json.dumps({"accepted_at": timestamp, "id": last["id"]}).encode()
            )
            .decode()
            .rstrip("=")
        )
    for cursor in cursors:
        for url in ("/api/generations", members_url):
            page = app_client.get(url, params={"collection_id": "", "cursor": cursor})
            assert page.status_code == 200, page.text
            assert [item["id"] for item in page.json()["items"]] == [
                item["id"] for item in history[2:]
            ]


def test_identical_race_and_payload_conflict(app_client):
    user, _ = provision_user(app_client)
    payload = generation_payload(app_client, "one acceptance")
    key = str(uuid4())
    token = csrf(app_client)
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(
            executor.map(
                lambda _: app_client.post(
                    "/api/generations",
                    json=payload,
                    headers={"X-CSRF-Token": token, "Idempotency-Key": key},
                ),
                range(8),
            )
        )
    assert [response.status_code for response in responses] == [201] * 8
    assert len({response.json()["id"] for response in responses}) == 1
    lookup = app_client.get(f"/api/generation-submissions/{key}")
    assert lookup.status_code == 200
    assert lookup.json()["result"]["id"] == responses[0].json()["id"]
    changed = {**payload, "parameters": {**payload["parameters"], "prompt": "different"}}
    assert submit(app_client, changed, key).status_code == 409
    with app_client.app.state.container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Generation)) == 1
        assert session.scalar(select(func.count()).select_from(GenerationSubmission)) == 1
        assert session.scalar(select(func.count()).select_from(GenerationRun)) == 1
        assert session.scalar(select(func.count()).select_from(GenerationEvent)) == 1
        assert session.get(GenerationSubmission, (user["id"], key)) is not None


def test_batch_replay_preserves_order_failures_and_deleted_identity(app_client):
    provision_user(app_client)
    valid = generation_payload(app_client, "batch receipt")
    invalid = {**valid, "source_key": "missing-source"}
    payload = {"items": [valid, invalid, valid]}
    key = str(uuid4())
    first = submit(app_client, payload, key, batch=True)
    assert first.status_code == 201, first.text
    assert first.json()["items"][1]["error"] is not None
    replay = submit(app_client, payload, key, batch=True).json()
    assert replay["items"][1]["error"] == first.json()["items"][1]["error"]
    assert [
        item["generation"]["id"] if item["generation"] else None for item in replay["items"]
    ] == [
        item["generation"]["id"] if item["generation"] else None for item in first.json()["items"]
    ]
    generation_id = first.json()["items"][0]["generation"]["id"]
    deleted = app_client.delete(
        f"/api/generations/{generation_id}", headers={"X-CSRF-Token": csrf(app_client)}
    )
    assert deleted.status_code == 204
    assert submit(app_client, payload, key, batch=True).status_code == 410
    assert app_client.get(f"/api/generation-submissions/{key}").status_code == 410
    with app_client.app.state.container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Generation)) == 1
        assert session.scalar(select(func.count()).select_from(GenerationSubmission)) == 1


def test_receipt_is_account_scoped_and_deleted_with_account(app_client):
    user, cookie = provision_user(app_client)
    payload = generation_payload(app_client, "private receipt")
    key = str(uuid4())
    assert submit(app_client, payload, key).status_code == 201
    login_ready_admin(app_client)
    assert app_client.get(f"/api/generation-submissions/{key}").status_code == 404
    assert submit(app_client, payload, key).status_code == 201
    deleted = app_client.delete(
        f"/api/admin/users/{user['id']}", headers={"X-CSRF-Token": csrf(app_client)}
    )
    assert deleted.status_code == 204, deleted.text
    with app_client.app.state.container.db.session_factory() as session:
        assert session.get(GenerationSubmission, (user["id"], key)) is None
    restore_cookie(app_client, cookie)
    assert app_client.get(f"/api/generation-submissions/{key}").status_code == 401


def test_failure_before_commit_rolls_back_acceptance(app_client, monkeypatch):
    provision_user(app_client)
    payload = generation_payload(app_client, "atomic failure")
    from app.services import submissions

    original = submissions.project_receipt

    def fail(*args):
        raise RuntimeError("injected before commit")

    monkeypatch.setattr(submissions, "project_receipt", fail)
    key = str(uuid4())
    with pytest.raises(RuntimeError, match="injected"):
        submit(app_client, payload, key)
    with app_client.app.state.container.db.session_factory() as session:
        for model in (Generation, GenerationSubmission, GenerationEvent, GenerationRun):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    monkeypatch.setattr(submissions, "project_receipt", original)
    assert submit(app_client, payload, key).status_code == 201


def test_notification_failure_does_not_lose_committed_receipt(app_client, monkeypatch):
    provision_user(app_client)
    payload = generation_payload(app_client, "lost notification")

    async def fail(*args):
        raise ConnectionError("injected after commit")

    monkeypatch.setattr(app_client.app.state.container.broker, "publish", fail)
    key = str(uuid4())
    first = submit(app_client, payload, key)
    assert first.status_code == 201
    assert submit(app_client, payload, key).json()["id"] == first.json()["id"]


def test_protocol_and_uuid_are_required_before_acceptance(app_client):
    provision_user(app_client)
    payload = generation_payload(app_client, "protocol")
    token = csrf(app_client)
    for key in ("", "invalid"):
        assert submit(app_client, payload, key).status_code == 400
    assert (
        app_client.post(
            "/api/generations",
            json=payload,
            headers={
                "X-CSRF-Token": token,
                "X-CIF-Generation-Protocol": "2",
            },
        ).status_code
        == 409
    )


def test_receipt_resolves_after_application_restart(settings_factory, fake_state):
    from app.main import create_app
    from fastapi.testclient import TestClient

    settings = settings_factory()
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first)
        payload = generation_payload(first, "restart receipt")
        key = str(uuid4())
        accepted = submit(first, payload, key).json()
        original = first.get(f"/api/generations/{accepted['id']}").json()
    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie)
        assert (
            second.get(f"/api/generation-submissions/{key}").json()["result"]["id"]
            == accepted["id"]
        )
        assert submit(second, payload, key).json()["id"] == accepted["id"]
        replayed = second.get(f"/api/generations/{accepted['id']}").json()
        assert replayed["resolved_seeds"] == original["resolved_seeds"]


def test_replay_does_not_consume_another_prompt_composition(app_client, fake_state):
    from app.models import PromptAssistantRun, ServiceHealth

    provision_user(app_client)
    with app_client.app.state.container.db.session_factory() as session:
        session.merge(ServiceHealth(service="ollama", available=True))
        session.commit()
    composed = app_client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"mode": "refine", "prompt": "portrait", "creative_direction": "soft light"},
    )
    assert composed.status_code == 200, composed.text
    payload = generation_payload(app_client, composed.json()["prompt"])
    payload["prompt_assistant_run_id"] = composed.json()["composition_id"]
    key = str(uuid4())
    accepted = submit(app_client, payload, key)
    assert accepted.status_code == 201, accepted.text
    assert submit(app_client, payload, key).json()["id"] == accepted.json()["id"]
    with app_client.app.state.container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PromptAssistantRun)) == 1
        assert session.scalar(select(func.count()).select_from(Generation)) == 1
    assert len(fake_state.ollama_calls) == 1
