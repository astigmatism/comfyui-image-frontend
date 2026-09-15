from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.main import create_app
from app.models import Generation, GenerationRun, GenerationRunMember, GenerationStatus
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.conftest import csrf
from tests.helpers import (
    create_generation,
    generation_payload,
    login_ready_admin,
    provision_user,
    restore_cookie,
    wait_for_status,
)


def _post(client, path, payload):
    response = client.post(path, headers={"X-CSRF-Token": csrf(client)}, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _finish(client, generation_id, status=GenerationStatus.SUCCEEDED):
    with client.app.state.container.db.session_factory() as session:
        generation = session.get(Generation, generation_id)
        generation.status = status
        generation.completed_at = datetime.now(UTC)
        session.commit()


def test_overlapping_batches_preserve_totals_after_cancellation_deletion_and_reload(app_client):
    client = app_client
    _, cookie = provision_user(client)
    payload = generation_payload(client, "progress")
    batch = _post(client, "/api/generations/batch", {"items": [payload] * 3})
    ids = [item["generation"]["id"] for item in batch["items"]]
    _finish(client, ids[0])
    first = client.get("/api/generation-activity").json()["run"]
    assert (first["resolved_count"], first["total_count"]) == (1, 3)
    _post(client, "/api/generations/batch", {"items": [payload] * 2})
    extended = client.get("/api/generation-activity").json()["run"]
    assert extended["id"] == first["id"]
    assert (extended["resolved_count"], extended["total_count"]) == (1, 5)

    # Queued cancellation physically removes the gallery record. It must still
    # resolve one original job rather than shrinking the denominator.
    response = client.post(
        f"/api/generations/{ids[1]}/cancel", headers={"X-CSRF-Token": csrf(client)}
    )
    assert response.status_code == 204
    response = client.delete(f"/api/generations/{ids[0]}", headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 204
    restore_cookie(client, cookie)
    run = client.get("/api/generation-activity").json()["run"]
    assert (run["total_count"], run["resolved_count"], run["remaining_count"]) == (5, 2, 3)
    assert (run["succeeded_count"], run["cancelled_count"], run["failed_count"]) == (1, 1, 0)

    with client.app.state.container.db.session_factory() as session:
        remaining_ids = list(session.scalars(select(Generation.id)))
    for generation_id in remaining_ids:
        _finish(client, generation_id, GenerationStatus.FAILED_WITHOUT_ARTIFACTS)
    finished = client.get("/api/generation-activity").json()["run"]
    assert finished["resolved_count"] == 5
    assert finished["failed_count"] == 3
    assert finished["completed_at"]
    create_generation(client, "new run")
    fresh = client.get("/api/generation-activity").json()["run"]
    assert fresh["id"] != first["id"]
    assert fresh["total_count"] == 1


def test_activity_counts_nested_and_unfiled_jobs_without_loading_gallery_and_is_owner_scoped(
    app_client,
):
    client = app_client
    _, cookie = provision_user(client)
    parent = _post(client, "/api/collections", {"name": "Parent"})["id"]
    child = _post(client, "/api/collections", {"name": "Child", "parent_id": parent})["id"]
    target = _post(client, "/api/collections", {"name": "Destination"})["id"]
    payload = generation_payload(client, "nested")
    batch = _post(
        client,
        "/api/generations/batch",
        {"items": [{**payload, "collection_id": child} for _ in range(26)]},
    )
    root = create_generation(client, "unfiled")
    activity = client.get("/api/generation-activity").json()
    assert activity["remaining_count"] == 27
    assert activity["collection_remaining_counts"] == {parent: 26, child: 26, target: 0}
    assert activity["collection_generation_counts"] == {child: 26}
    moved = client.post(
        f"/api/generations/{batch['items'][0]['generation']['id']}/move",
        headers={"X-CSRF-Token": csrf(client)},
        json={"collection_id": target},
    )
    assert moved.status_code == 200
    activity = client.get("/api/generation-activity").json()
    assert activity["collection_remaining_counts"] == {parent: 25, child: 25, target: 1}
    _finish(client, root["id"])
    login_ready_admin(client)
    assert client.get("/api/generation-activity").json() == {
        "run": None,
        "remaining_count": 0,
        "collection_remaining_counts": {},
        "collection_generation_counts": {},
    }
    restore_cookie(client, cookie)
    assert client.get("/api/generation-activity").json()["remaining_count"] == 26


def test_run_reports_eta_derived_completed_fraction(app_client):
    client = app_client
    provision_user(client)
    payload = generation_payload(client, "fraction")
    batch = _post(client, "/api/generations/batch", {"items": [payload] * 3})
    ids = [item["generation"]["id"] for item in batch["items"]]
    _finish(client, ids[0])

    # No member carries an ETA yet, so the fraction is unavailable.
    run = client.get("/api/generation-activity").json()["run"]
    assert run["completed_fraction"] is None

    now = datetime.now(UTC)
    with client.app.state.container.db.session_factory() as session:
        running = session.get(Generation, ids[1])
        running.status = GenerationStatus.RUNNING
        running.started_at = now - timedelta(seconds=30)
        running.progress_json = {
            "kind": "node",
            "label": "KSampler",
            "updated_at": now.isoformat(),
            "eta": {
                "remaining_seconds": 30,
                "completion_at": (now + timedelta(seconds=30)).isoformat(),
                "lower_seconds": 20,
                "upper_seconds": 45,
                "confidence": "medium",
                "basis": "historical_total",
                "updated_at": now.isoformat(),
            },
        }
        session.commit()

    run = client.get("/api/generation-activity").json()["run"]
    # resolved 1/3 plus the in-flight item halfway (30s elapsed of 60s total).
    assert run["completed_fraction"] == pytest.approx(0.5, abs=1 / 30)

    # A stale ETA written before the attempt must not skew the fraction.
    with client.app.state.container.db.session_factory() as session:
        running = session.get(Generation, ids[1])
        eta = {
            **running.progress_json["eta"],
            "updated_at": (now - timedelta(seconds=120)).isoformat(),
        }
        running.progress_json = {**running.progress_json, "eta": eta}
        session.commit()
    run = client.get("/api/generation-activity").json()["run"]
    assert run["completed_fraction"] is None

    _finish(client, ids[1])
    _finish(client, ids[2])
    run = client.get("/api/generation-activity").json()["run"]
    assert run["completed_fraction"] is None
    assert run["resolved_count"] == 3


def test_partial_submission_errors_resolve_planned_slots_without_losing_valid_jobs(app_client):
    client = app_client
    provision_user(client)
    payload = generation_payload(client, "valid")
    invalid = {**payload, "collection_id": "not-owned"}
    batch = _post(client, "/api/generations/batch", {"items": [invalid, payload, invalid, payload]})
    assert [bool(item["generation"]) for item in batch["items"]] == [False, True, False, True]
    assert batch["items"][0]["error"]["code"] == "not_found"
    activity = client.get("/api/generation-activity").json()["run"]
    assert (activity["total_count"], activity["resolved_count"], activity["failed_count"]) == (
        4,
        2,
        2,
    )
    with client.app.state.container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GenerationRunMember)) == 2
        assert session.scalar(select(func.count()).select_from(GenerationRun)) == 1


def test_activity_survives_application_restart_and_worker_completion(settings_factory, fake_state):
    del fake_state
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        _, cookie = provision_user(client)
        generation = create_generation(client, "restart progress")
        queued = client.get("/api/generation-activity").json()["run"]
        assert queued["remaining_count"] == 1
    settings.enable_background_worker = True
    with TestClient(create_app(settings)) as client:
        restore_cookie(client, cookie)
        wait_for_status(client, generation["id"], "succeeded")
        completed = client.get("/api/generation-activity").json()["run"]
        assert completed["id"] == queued["id"]
        assert completed["resolved_count"] == completed["total_count"] == 1
        assert completed["remaining_count"] == 0
        assert completed["succeeded_count"] == 1


def test_unexpected_batch_failure_rolls_back_the_full_plan(app_client, monkeypatch):
    client = app_client
    provision_user(client)
    create_generation(client, "existing work")
    before = client.get("/api/generation-activity").json()
    service = client.app.state.container.generations
    prepare = service._prepare_accept
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted batch transaction")
        return prepare(*args, **kwargs)

    monkeypatch.setattr(service, "_prepare_accept", fail_second)
    payload = generation_payload(client, "rolled back")
    with pytest.raises(RuntimeError, match="interrupted batch"):
        _post(client, "/api/generations/batch", {"items": [payload] * 3})
    assert client.get("/api/generation-activity").json() == before
    with client.app.state.container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Generation)) == 1
