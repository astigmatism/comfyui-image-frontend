"""Regressions for verified evidence and stable, execution-anchored countdowns."""

from datetime import timedelta

import pytest
from app.models import GenerationStatus
from app.services.generation_eta import TIMING_FEATURE_VERSION, GenerationEtaEstimator
from tests.unit.test_generation_eta import _generation, _session_factory, learn, timing


def test_deadline_survives_progress_replay_restart_and_overrun(tmp_path):
    factory = _session_factory(tmp_path / "timing.db")
    estimator = GenerationEtaEstimator(factory)
    current = _generation(generation_id="active")
    current.comfyui_prompt_id = "p"
    current.execution_timing_json = timing(current, 60)
    current.execution_timing_json.pop("finished_at")
    current.execution_timing_json.pop("duration_seconds")
    learn(estimator, _generation(generation_id="completed"), 60)
    first = estimator.estimate(current, now=current.started_at + timedelta(seconds=10))
    assert first["remaining_seconds"] == 50
    for elapsed in (20, 40, 80):
        estimate = estimator.estimate(
            current,
            progress={"kind": "node", "fraction": 0.99},
            now=current.started_at + timedelta(seconds=elapsed),
        )
        assert estimate["completion_at"] == first["completion_at"]
        assert estimate["remaining_seconds"] == max(0, 60 - elapsed)
    fresh = GenerationEtaEstimator(factory)
    fresh.refresh()
    assert (
        fresh.estimate(current, now=current.started_at + timedelta(seconds=20))["completion_at"]
        == first["completion_at"]
    )
    current.execution_timing_json["version"] = TIMING_FEATURE_VERSION - 1
    assert fresh.estimate(current) is None


@pytest.mark.parametrize(
    "status",
    [GenerationStatus.QUEUED, GenerationStatus.DISPATCHING, GenerationStatus.CANCEL_REQUESTED],
)
def test_submission_time_and_saved_old_eta_cannot_create_execution(status, tmp_path):
    estimator = GenerationEtaEstimator(_session_factory(tmp_path / "timing.db"))
    learn(estimator, _generation(generation_id="sample"), 60)
    current = _generation(status=status)
    current.progress_json = {
        "eta": {"completion_at": "2099-01-01T00:00:00Z", "basis": "progress_landmark"}
    }
    assert estimator.estimate(current) is None
    current.status = GenerationStatus.RUNNING
    assert estimator.estimate(current) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["complete", "disconnect", "restart", "failed", "cached", "malformed_native"]
)
async def test_monotonic_fallback_requires_fully_observed_uninterrupted_success(tmp_path, scenario):
    from app.services.queue_worker import QueueWorker
    from tests.unit.test_generation_eta import history

    factory = _session_factory(tmp_path / "observations.db")
    worker = QueueWorker.__new__(QueueWorker)
    worker.session_factory = factory
    worker._execution_observations = {}
    g = _generation()
    g.comfyui_prompt_id = "p"
    with factory() as session:
        session.add(g)
        session.commit()
    await worker._observe_execution(g.id, "p", "execution_start", {}, 10)
    await worker._observe_execution(g.id, "p", "executing", {"node": "1"}, 11)
    if scenario in {"disconnect", "restart"}:
        worker._execution_observations.clear()
    await worker._observe_execution(g.id, "p", "execution_success", {}, 70)
    h = {"status": {"status_str": "success", "messages": []}}
    if scenario == "cached":
        h = history()
        h["status"]["messages"].append(
            ["execution_cached", {"prompt_id": "p", "nodes": ["1", "2"]}]
        )
    elif scenario == "malformed_native":
        h = history()
        h["status"]["messages"][1][1]["timestamp"] = -1
    worker._save_execution_timing(g.id, h, "failed" if scenario == "failed" else "success")
    with factory() as session:
        saved = session.get(type(g), g.id).execution_timing_json
        if scenario == "complete":
            assert saved["duration_seconds"] == 60
            assert saved["provenance"] == "monotonic"
        else:
            assert "duration_seconds" not in saved


@pytest.mark.asyncio
async def test_native_host_clock_offset_and_buffered_delivery_do_not_shift_countdown(tmp_path):
    from datetime import UTC, datetime

    from app.services.queue_worker import QueueWorker

    factory = _session_factory(tmp_path / "clock.db")
    estimator = GenerationEtaEstimator(factory)
    learn(estimator, _generation(generation_id="sample"), 60)
    current = _generation(generation_id="current")
    current.comfyui_prompt_id = "p"
    with factory() as session:
        session.add(current)
        session.commit()
    worker = QueueWorker.__new__(QueueWorker)
    worker.session_factory = factory
    worker._execution_observations = {}
    now = datetime.now(UTC)
    observed = (now - timedelta(seconds=10)).isoformat()
    await worker._observe_execution(
        current.id,
        "p",
        "execution_start",
        {"timestamp": (now.timestamp() + 120) * 1000},
        10,
        observed,
    )
    with factory() as session:
        current = session.get(type(current), current.id)
        estimate = estimator.estimate(current, now=now)
        assert estimate["remaining_seconds"] == 50
    worker._execution_observations.clear()
    await worker._observe_execution(current.id, "p", "execution_start", {}, 20)
    assert current.id not in worker._execution_observations
