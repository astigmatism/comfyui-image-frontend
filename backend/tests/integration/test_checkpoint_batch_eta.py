from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from app.main import create_app
from app.models import Generation, GenerationStatus, GenerationTimingProfile
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import csrf
from tests.helpers import provision_user, wait_for_status
from tests.publication_fixtures import build_publication_bundle

MOODY_DISPLAY_NAME = "Moody Krea 2 Mix V4"


def _post(client, path, payload):
    response = client.post(path, headers={"X-CSRF-Token": csrf(client)}, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _moody_profile(client, *, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/api/workflows")
        assert response.status_code == 200, response.text
        match = next(
            (
                item
                for item in response.json()
                if item["display_name"] == MOODY_DISPLAY_NAME and item["available"] is True
            ),
            None,
        )
        if match is not None:
            return match
        time.sleep(0.02)
    raise AssertionError("Moody checkpoint source did not become ready")


def _moody_payload(client, prompt: str, **extra_parameters: object) -> dict[str, object]:
    profile = _moody_profile(client)
    parameters: dict[str, object] = {
        "prompt": prompt,
        "seed": 1234,
        "width": 512,
        "height": 512,
    }
    parameters.update(extra_parameters)
    return {
        "source_key": profile["source_key"],
        "revision": profile["revision"],
        "parameters": parameters,
    }


def _started_at(client, generation_id: str) -> datetime:
    with client.app.state.container.db.session_factory() as session:
        generation = session.get(Generation, generation_id)
        assert generation is not None and generation.started_at is not None
        started_at = generation.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return started_at


def _wait_eta(
    client, generation_id: str, basis: str, *, timeout: float = 15.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_eta: dict[str, object] | None = None
    while time.monotonic() < deadline:
        detail = client.get(f"/api/generations/{generation_id}").json()
        progress = detail.get("progress") or {}
        eta = progress.get("eta") or {}
        last_eta = dict(eta) if eta else None
        if eta.get("basis") == basis:
            return detail
        time.sleep(0.03)
    raise AssertionError(f"eta basis {basis!r} never observed; last_eta={last_eta}")


def test_running_sibling_estimates_the_rest_of_the_checkpoint_batch(
    fake_state, settings_factory
) -> None:
    fake_state.workflow_files = dict(build_publication_bundle("moody").files)
    fake_state.stage_delay_overrides = {
        "sibling eta alpha": 1.0,
        "sibling eta beta": 1.0,
        "sibling eta gamma": 1.0,
    }
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="eta.sibling")
        prompts = ["sibling eta alpha", "sibling eta beta", "sibling eta gamma"]
        batch = _post(
            client,
            "/api/generations/batch",
            {"items": [_moody_payload(client, prompt) for prompt in prompts]},
        )
        ids = [item["generation"]["id"] for item in batch["items"]]

        wait_for_status(client, ids[0], "succeeded", timeout=30)
        first = client.get(f"/api/generations/{ids[0]}").json()
        first_duration = float(first["generation_duration_seconds"])
        assert first_duration > 0.9

        observed = _wait_eta(client, ids[1], "run_sibling", timeout=15)
        eta = observed["progress"]["eta"]
        started_at = _started_at(client, ids[1])
        updated_at = datetime.fromisoformat(str(eta["updated_at"]))
        elapsed = max(0.0, (updated_at.astimezone(UTC) - started_at).total_seconds())
        assert eta["confidence"] == "medium"
        remaining = float(eta["remaining_seconds"])
        assert 0 < remaining < first_duration
        assert remaining == pytest.approx(first_duration - elapsed, abs=0.15)
        assert float(eta["lower_seconds"]) <= remaining <= float(eta["upper_seconds"])

        for generation_id in ids[1:]:
            wait_for_status(client, generation_id, "succeeded", timeout=30)

        # The finished run is fully torn down in the worker's in-memory state.
        worker = client.app.state.container.worker
        assert worker._generation_run_ids == {}
        assert worker._run_members == {}
        assert worker._run_sibling_durations == {}
        assert worker._run_cohorts == {}


def test_failed_sibling_is_excluded_from_the_batch_estimate(fake_state, settings_factory) -> None:
    fake_state.workflow_files = dict(build_publication_bundle("moody").files)
    # The failing sibling runs much longer than the healthy one, so including
    # its duration would visibly shift the sibling median.
    fake_state.stage_delay_overrides = {
        "eta skip alpha": 1.0,
        "eta skip fail beta": 2.0,
        "eta skip gamma": 1.0,
    }
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="eta.failed.sibling")
        prompts = ["eta skip alpha", "eta skip fail beta", "eta skip gamma"]
        batch = _post(
            client,
            "/api/generations/batch",
            {"items": [_moody_payload(client, prompt) for prompt in prompts]},
        )
        ids = [item["generation"]["id"] for item in batch["items"]]

        wait_for_status(client, ids[0], "succeeded", timeout=30)
        first_duration = float(
            client.get(f"/api/generations/{ids[0]}").json()["generation_duration_seconds"]
        )
        wait_for_status(
            client, ids[1], "failed_with_artifacts", "failed_without_artifacts", timeout=30
        )

        observed = _wait_eta(client, ids[2], "run_sibling", timeout=15)
        eta = observed["progress"]["eta"]
        started_at = _started_at(client, ids[2])
        updated_at = datetime.fromisoformat(str(eta["updated_at"]))
        elapsed = max(0.0, (updated_at.astimezone(UTC) - started_at).total_seconds())
        assert elapsed < first_duration - 0.2  # still inside the non-overrun branch
        # Only the healthy sibling is in evidence: the estimate tracks its
        # duration, not the average of healthy plus failed durations.
        assert float(eta["remaining_seconds"]) == pytest.approx(first_duration - elapsed, abs=0.15)
        assert eta["confidence"] == "medium"

        wait_for_status(client, ids[2], "succeeded", timeout=30)
        worker = client.app.state.container.worker
        assert worker._run_sibling_durations == {}
        assert worker._run_members == {}


def test_audit_folds_total_checkpoint_scope_and_single_generation_recalls_it(
    fake_state, settings_factory
) -> None:
    fake_state.workflow_files = dict(build_publication_bundle("moody").files)
    fake_state.stage_delay_overrides = {"checkpoint cohort recall": 0.5}
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="eta.checkpoint.scope")
        prompt = "checkpoint cohort recall"
        batch = _post(
            client,
            "/api/generations/batch",
            {"items": [_moody_payload(client, prompt) for _ in range(3)]},
        )
        ids = [item["generation"]["id"] for item in batch["items"]]
        for generation_id in ids:
            wait_for_status(client, generation_id, "succeeded", timeout=30)

        estimator = client.app.state.container.generation_eta
        estimator.audit_interval_seconds = 0.05
        estimator.notify()

        row = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with client.app.state.container.db.session_factory() as session:
                row = session.scalar(
                    select(GenerationTimingProfile).where(
                        GenerationTimingProfile.scope == "total_checkpoint"
                    )
                )
            if row is not None:
                break
            time.sleep(0.05)
        assert row is not None
        assert row.sample_count == 3

        # A single generation keeps the checkpoint cohort (same source, model
        # variant, prompt size, resolution) but changes its exact
        # configuration, so only the checkpoint scope can recall it.
        single = _post(
            client,
            "/api/generations",
            _moody_payload(client, prompt, enable_seedvr2_upscale=True),
        )
        observed = _wait_eta(client, single["id"], "historical_checkpoint", timeout=15)
        eta = observed["progress"]["eta"]
        assert eta["confidence"] == "low"
        assert float(eta["remaining_seconds"]) >= 0
        wait_for_status(client, single["id"], "succeeded", timeout=30)


def test_fresh_worker_reseeds_completed_siblings_from_the_database(
    fake_state, settings_factory
) -> None:
    """A restarted worker has no in-memory run state at all.

    Registering the remaining batch member must seed the already-completed
    sibling's duration from the database so the live estimate survives an
    application restart mid-batch. The background worker stays disabled so
    nothing auto-dispatches and registration is driven deterministically.
    """
    fake_state.workflow_files = dict(build_publication_bundle("moody").files)
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="eta.restart")
        batch = _post(
            client,
            "/api/generations/batch",
            {
                "items": [
                    _moody_payload(client, "restart eta alpha"),
                    _moody_payload(client, "restart eta beta"),
                ]
            },
        )
        ids = [item["generation"]["id"] for item in batch["items"]]
        first_id, second_id = ids

        # The sibling that completed in the previous process: its lifecycle
        # interval is durable, everything else is gone with the old process.
        completed_at = datetime.now(UTC) - timedelta(seconds=1.0)
        started_at = completed_at - timedelta(seconds=1.5)
        with client.app.state.container.db.session_factory() as session:
            first = session.get(Generation, first_id)
            assert first is not None
            first.status = GenerationStatus.SUCCEEDED
            first.started_at = started_at
            first.completed_at = completed_at
            session.commit()

        worker = client.app.state.container.worker
        worker._register_run_timing(second_id)

        run_id = worker._generation_run_ids[second_id]
        assert worker._run_cohorts[run_id] is not None
        assert worker._run_sibling_durations[run_id] == pytest.approx([1.5])

        with client.app.state.container.db.session_factory() as session:
            second = session.get(Generation, second_id)
        assert worker._sibling_durations_for(second) == pytest.approx((1.5,))
