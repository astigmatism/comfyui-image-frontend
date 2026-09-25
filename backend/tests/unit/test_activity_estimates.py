import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.models import ComfyUIInstanceHealth, GenerationPreparation, GenerationStatus
from app.services.activity_estimates import ActivityEstimator
from app.services.generation_eta import GenerationEtaEstimator
from tests.unit.test_generation_eta import _generation, _session_factory, learn, timing


def setup_queue(tmp_path, *, runtimes=("test-instance",)):
    factory = _session_factory(tmp_path / "activity.db")
    estimator = GenerationEtaEstimator(factory)
    adapters = {
        r: SimpleNamespace(
            queue_observation=(time.monotonic(), {"queue_running": [], "queue_pending": []})
        )
        for r in runtimes
    }
    instances = SimpleNamespace(
        configs=[SimpleNamespace(id=r) for r in runtimes], get=adapters.__getitem__
    )
    for runtime in runtimes:
        learn(estimator, _generation(generation_id="learned-" + runtime, instance_id=runtime), 60)
    with factory() as session:
        for runtime in runtimes:
            session.add(ComfyUIInstanceHealth(instance_id=runtime, available=True))
        session.commit()
    return factory, estimator, adapters, ActivityEstimator(estimator, instances)


def active(generation_id="active", *, owner="owner-a", runtime="test-instance", elapsed=10, seq=1):
    g = _generation(
        generation_id=generation_id,
        owner_id=owner,
        instance_id=runtime,
        started_at=datetime.now(UTC) - timedelta(seconds=elapsed),
    )
    g.comfyui_prompt_id = generation_id
    g.execution_timing_json = timing(g, 60)
    g.execution_timing_json.pop("finished_at")
    g.execution_timing_json.pop("duration_seconds")
    g.queue_seq = seq
    g.dispatched_at = g.started_at
    return g


def queued(generation_id="queued", *, owner="owner-a", seq=2, **kwargs):
    g = _generation(
        generation_id=generation_id, owner_id=owner, status=GenerationStatus.QUEUED, **kwargs
    )
    g.queue_seq = seq
    return g


def test_serial_queue_heterogeneous_work_and_cached_deadline(tmp_path):
    factory, estimator, _, projection = setup_queue(tmp_path)
    learn(estimator, _generation(generation_id="large-sample", width=2048), 120)
    with factory() as session:
        session.add_all([active(), queued(width=2048)])
        session.commit()
        result = projection.project(session, "owner-a")
        assert result["running_count"] == result["queued_count"] == 1
        assert result["current_eta"]["remaining_seconds"] == pytest.approx(50, abs=1)
        assert result["queue_eta"]["remaining_seconds"] == pytest.approx(170, abs=1)
        assert (
            projection.project(session, "owner-a")["queue_eta"]["completion_at"]
            == result["queue_eta"]["completion_at"]
        )
        assert projection.project(session, "other")["current_generation_id"] is None


def test_independent_recorded_runtimes_use_latest_finish_and_next(tmp_path):
    factory, _, _, projection = setup_queue(tmp_path, runtimes=("test-instance", "old-gpu"))
    with factory() as session:
        session.add_all([active(elapsed=10), active("old", runtime="old-gpu", elapsed=30, seq=2)])
        session.commit()
        result = projection.project(session, "owner-a")
        assert result["running_count"] == 2
        assert result["current_generation_id"] == "old"
        assert result["queue_eta"]["remaining_seconds"] == pytest.approx(50, abs=1)


@pytest.mark.parametrize(
    "reason", ["offline", "external", "unknown", "overdue", "cancel", "stale_queue"]
)
def test_unknown_blockers_invalidate_total(tmp_path, reason):
    factory, _, adapters, projection = setup_queue(tmp_path)
    with factory() as session:
        g = active(elapsed=80 if reason == "overdue" else 10)
        if reason == "cancel":
            g.status = GenerationStatus.CANCEL_REQUESTED
        session.add_all([g, queued(width=2048 if reason == "unknown" else 1024)])
        if reason == "offline":
            session.get(ComfyUIInstanceHealth, "test-instance").available = False
        elif reason == "external":
            adapters["test-instance"].queue_observation[1]["queue_running"] = [
                [0, "unmapped-private-job"]
            ]
        elif reason == "stale_queue":
            adapters["test-instance"].queue_observation = (0, {})
        session.commit()
        result = projection.project(session, "owner-a")
        assert result["queue_eta"] is None
        assert "unmapped-private-job" not in str(result)


def test_shared_preparation_counts_images_and_does_not_multiply_or_omit_wait(tmp_path):
    factory, _, _, projection = setup_queue(tmp_path)
    with factory() as session:
        session.add(active())
        for i in range(3):
            session.add(
                GenerationPreparation(
                    id=f"prep{i}",
                    owner_id="owner-a",
                    group_id="shared",
                    activity_run_id="run",
                    status="preparing",
                    request_json={},
                    profile_id="profile",
                    prompt_run_id="prompt",
                    position=i,
                )
            )
        session.commit()
        result = projection.project(session, "owner-a")
        assert result["queued_count"] == 3
        assert result["queue_eta"] is None
        assert result["current_eta"] is not None


def test_other_account_work_delays_total_without_disclosing_identity(tmp_path):
    factory, _, _, projection = setup_queue(tmp_path)
    with factory() as session:
        session.add_all([active(owner="private-owner"), queued()])
        session.commit()
        result = projection.project(session, "owner-a")
        assert result["current_eta"] is None
        assert result["queue_eta"]["remaining_seconds"] == pytest.approx(110, abs=1)
        assert "private-owner" not in str(result)


def test_cached_total_expires_when_another_accounts_blocker_overruns(tmp_path):
    factory, _, _, projection = setup_queue(tmp_path)
    with factory() as session:
        session.add_all([active(owner="someone-else"), queued()])
        session.commit()
        result = projection.project(session, "owner-a")
        assert result["queue_eta"] is not None
        snapshot = projection._cache["owner-a"][1]
        aged = projection._age(snapshot, datetime.now(UTC) + timedelta(seconds=55))
        assert aged["queue_eta"] is None
        assert "_valid_until" not in aged


def test_external_work_behind_owned_native_jobs_does_not_delay_their_total(tmp_path):
    factory, _, adapters, projection = setup_queue(tmp_path)
    adapters["test-instance"].queue_observation[1]["queue_running"] = [[0, "active"]]
    adapters["test-instance"].queue_observation[1]["queue_pending"] = [[1, "external-afterwards"]]
    with factory() as session:
        session.add(active())
        session.commit()
        result = projection.project(session, "owner-a")
        assert result["queue_eta"]["remaining_seconds"] == pytest.approx(50, abs=1)
        session.add(queued())
        session.commit()
        # A newly accepted application job must wait behind the external native queue item.
        assert projection.project(session, "owner-a")["queue_eta"] is None
