from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Generation, GenerationStatus
from tests.conftest import change_password, create_user, login
from tests.helpers import (
    ADMIN_PASSWORD,
    ADMIN_TEMP,
    USER_PASSWORD,
    USER_TEMP,
    create_generation,
    generation_payload,
    provision_user,
    restore_cookie,
    wait_for_generation,
    wait_for_status,
)


def test_scheduler_is_fifo_per_user_and_round_robin_across_users(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        login(client, "admin", ADMIN_TEMP)
        change_password(client, ADMIN_PASSWORD)
        create_user(client, "queue.alice", USER_TEMP)
        create_user(client, "queue.bob", "BobTemporary123!")

        client.cookies.clear()
        login(client, "queue.alice", USER_TEMP)
        change_password(client, USER_PASSWORD)
        alice_cookie = client.cookies.get(settings.session_cookie_name)
        assert alice_cookie
        a1 = create_generation(client, "alice one", seed=1)
        a2 = create_generation(client, "alice two", seed=2)

        client.cookies.clear()
        login(client, "queue.bob", "BobTemporary123!")
        change_password(client, "BobPermanent123!")
        b1 = create_generation(client, "bob one", seed=3)
        b2 = create_generation(client, "bob two", seed=4)

        worker = client.app.state.container.worker
        claimed = [worker._claim_next() for _ in range(4)]
        assert all(item is not None for item in claimed)
        claimed_ids = [item[0] for item in claimed if item]
        assert claimed_ids == [a1["id"], b1["id"], a2["id"], b2["id"]]


def test_queued_generation_survives_restart_and_dispatches(settings_factory, fake_state) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="restart.queued")
        queued = create_generation(first, "queued across restart", seed=11)
        assert queued["status"] == "queued"

    settings.enable_background_worker = True
    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie, name=settings.session_cookie_name)
        completed = wait_for_status(second, queued["id"], "succeeded")
        assert completed["canonical_artifact_id"] is not None
        assert [item["prompt"] for item in fake_state.submitted] == ["queued across restart"]


def test_running_generation_reconciles_after_application_restart(settings_factory, fake_state) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="restart.running")
        running = create_generation(first, "slow restart reconciliation", seed=12)
        before_shutdown = wait_for_generation(
            first,
            running["id"],
            lambda item: item["status"] == "running" and item["artifact_count"] >= 1,
        )
        assert before_shutdown["canonical_artifact_id"] is None

    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie, name=settings.session_cookie_name)
        reconciled = wait_for_status(second, running["id"], "succeeded")
        assert reconciled["artifact_count"] == 2
        assert len(fake_state.submitted) == 1


def test_unknown_restart_outcome_becomes_interrupted(settings_factory, fake_state) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="restart.unknown")
        generation = create_generation(first, "unknown restart", seed=13)
        container = first.app.state.container
        with container.db.session_factory() as session:
            stored = session.get(Generation, generation["id"])
            assert stored
            stored.status = GenerationStatus.RUNNING
            stored.comfyui_prompt_id = "missing-prompt-id"
            session.commit()

    settings.enable_background_worker = True
    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie, name=settings.session_cookie_name)
        detail = second.get(f"/api/generations/{generation['id']}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "interrupted"
        assert detail.json()["error_code"] == "execution_interrupted"
        assert second.get(f"/api/generations/{generation['id']}/recall").json()["available"] is True


def test_comfyui_outage_preserves_history_and_pauses_dispatch(settings_factory, fake_state) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="outage.user")
        queued = create_generation(first, "resume after outage", seed=14)

    fake_state.service_available = False
    settings.enable_background_worker = True
    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie, name=settings.session_cookie_name)
        current = second.get(f"/api/generations/{queued['id']}")
        assert current.status_code == 200
        assert current.json()["status"] == "queued"
        rejected = second.post(
            "/api/generations",
            headers={"X-CSRF-Token": second.get("/api/auth/session").json()["csrf_token"]},
            json=generation_payload(second, "blocked while down", seed=15),
        )
        assert rejected.status_code == 503

        fake_state.service_available = True
        completed = wait_for_status(second, queued["id"], "succeeded", timeout=10)
        assert completed["canonical_artifact_id"] is not None


def test_restart_during_comfyui_outage_defers_unknown_interruption_until_reconnect(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="restart.outage")
        generation = create_generation(first, "unknown while service is down", seed=16)
        container = first.app.state.container
        with container.db.session_factory() as session:
            stored = session.get(Generation, generation["id"])
            assert stored
            stored.status = GenerationStatus.RUNNING
            stored.comfyui_prompt_id = "missing-during-outage"
            session.commit()

    fake_state.service_available = False
    settings.enable_background_worker = True
    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie, name=settings.session_cookie_name)
        current = second.get(f"/api/generations/{generation['id']}")
        assert current.status_code == 200
        assert current.json()["status"] == "running"

        fake_state.service_available = True
        interrupted = wait_for_status(second, generation["id"], "interrupted", timeout=5)
        assert interrupted["error_code"] == "execution_interrupted"
        assert second.get(f"/api/generations/{generation['id']}/recall").json()["available"] is True
