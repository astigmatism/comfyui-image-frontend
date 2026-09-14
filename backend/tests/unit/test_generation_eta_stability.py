from datetime import timedelta

import pytest
from app.models import GenerationRun, GenerationRunMember, GenerationStatus
from app.services.generation_eta import (
    TIMING_FEATURE_VERSION,
    GenerationEtaEstimator,
    _ProfileSnapshot,
    build_generation_timing_features,
    build_progress_landmark_key,
)
from app.services.queue_worker import QueueWorker
from tests.unit.test_generation_eta import (
    _checkpoint_generation,
    _progress,
    _session_factory,
)


def _profile(seconds, count=5):
    return _ProfileSnapshot(seconds, seconds * 0.8, seconds * 1.2, count, count)


def test_deadline_ages_through_bands_gaps_replay_and_overrun(tmp_path):
    estimator = GenerationEtaEstimator(_session_factory(tmp_path / "bands.db"))
    generation = _checkpoint_generation()
    start = generation.started_at
    features = build_generation_timing_features(generation)
    for fraction, seconds in [(0.5, 40), (0.7, 10), (0.8, 20)]:
        key = build_progress_landmark_key(features, _progress(fraction=fraction))
        estimator._profiles[("progress_landmark", key)] = _profile(seconds)
    estimator._profiles[("total_exact", features.exact_key)] = _profile(200)

    def estimate(elapsed, fraction, event_at=None):
        now = start + timedelta(seconds=elapsed)
        progress = _progress(fraction=fraction, at=start + timedelta(seconds=event_at or elapsed))
        return estimator.estimate(generation, progress, now)

    initial = estimate(10, 0.51)
    assert initial["remaining_seconds"] == 40
    assert estimate(20, 0.59)["remaining_seconds"] == 30
    gap = estimate(25, 0.61)
    assert gap["completion_at"] == initial["completion_at"]
    assert gap["basis"] == "progress_landmark"
    refined = estimate(30, 0.71)
    assert refined["remaining_seconds"] == 10
    assert estimate(31, 0.51, event_at=10)["remaining_seconds"] == 9
    assert estimate(32, 0.51)["remaining_seconds"] == 8
    for elapsed in [40, 45, 55]:
        overdue = estimate(elapsed, 0.71)
        assert overdue["remaining_seconds"] == 0
        assert overdue["completion_at"] == refined["completion_at"]
    assert estimate(60, 0.81)["remaining_seconds"] == 20


def test_deadline_survives_restart_and_resets_on_new_attempt(tmp_path):
    factory = _session_factory(tmp_path / "restart.db")
    generation = _checkpoint_generation()
    start = generation.started_at
    key = build_progress_landmark_key(build_generation_timing_features(generation), _progress())
    estimator = GenerationEtaEstimator(factory)
    estimator._profiles[("progress_landmark", key)] = _profile(40)
    generation.progress_json = _progress(at=start + timedelta(seconds=10))
    first = estimator.estimate(generation, now=start + timedelta(seconds=10))
    generation.progress_json = {
        **_progress(fraction=0.59, at=start + timedelta(seconds=20)),
        "eta": estimator.estimate(generation, now=start + timedelta(seconds=20)),
    }
    restarted = GenerationEtaEstimator(factory)
    restarted._profiles = estimator._profiles.copy()
    # The worker passes a new raw progress snapshot without ETA; the saved
    # deadline must still be restored from the generation row.
    recovered = restarted.estimate(
        generation,
        progress=_progress(fraction=0.59, at=start + timedelta(seconds=25)),
        now=start + timedelta(seconds=25),
    )
    assert recovered["completion_at"] == first["completion_at"]
    assert recovered["remaining_seconds"] == 25
    new_key = build_progress_landmark_key(
        build_generation_timing_features(generation), _progress(fraction=0.7)
    )
    restarted._profiles[("progress_landmark", new_key)] = _profile(10)
    newer = restarted.estimate(
        generation,
        progress=_progress(fraction=0.7, at=start + timedelta(seconds=30)),
        now=start + timedelta(seconds=30),
    )
    assert newer["remaining_seconds"] == 10
    cold_replay = GenerationEtaEstimator(factory)
    cold_replay._profiles = restarted._profiles.copy()
    replayed = cold_replay.estimate(
        generation,
        progress=_progress(fraction=0.7, at=start + timedelta(seconds=15)),
        now=start + timedelta(seconds=25),
    )
    assert replayed["completion_at"] == first["completion_at"]
    generation.status = GenerationStatus.QUEUED
    assert restarted.estimate(generation) is None
    assert generation.id not in restarted._deadlines
    generation.status = GenerationStatus.RUNNING
    generation.started_at = start + timedelta(seconds=100)
    generation.progress_json = None
    new = restarted.estimate(generation, now=generation.started_at, sibling_durations=[60])
    assert new["remaining_seconds"] == 60
    assert new["completion_at"] != first["completion_at"]


def test_checkpoint_history_precedes_compatible_siblings_and_matching_siblings_win(tmp_path):
    estimator = GenerationEtaEstimator(_session_factory(tmp_path / "priority.db"))
    generation = _checkpoint_generation()
    features = build_generation_timing_features(generation)
    now = generation.started_at + timedelta(seconds=10)
    estimator._profiles[("total_revision", features.revision_key)] = _profile(300)
    compatible = estimator.estimate(generation, now=now, compatible_sibling_durations=[80])
    assert compatible["basis"] == "run_sibling_compatible"
    assert compatible["confidence"] == "low"
    estimator._profiles[("total_checkpoint", features.checkpoint_key)] = _profile(50)
    checkpoint = estimator.estimate(generation, now=now, compatible_sibling_durations=[80])
    assert checkpoint["basis"] == "historical_checkpoint"
    assert checkpoint["remaining_seconds"] == 40
    sibling = estimator.estimate(generation, now=now, sibling_durations=[30])
    assert sibling["basis"] == "run_sibling"
    assert sibling["remaining_seconds"] == 20
    later = estimator.estimate(generation, now=now + timedelta(seconds=5), sibling_durations=[30])
    assert later["completion_at"] == sibling["completion_at"]
    revised = estimator.estimate(
        generation, now=now + timedelta(seconds=5), sibling_durations=[30, 50]
    )
    assert revised["remaining_seconds"] == 25


def test_prompt_bands_separate_exact_and_landmark_profiles_without_content():
    short = _checkpoint_generation(prompt="x" * 10)
    same_band = _checkpoint_generation(prompt="y" * 12)
    long = _checkpoint_generation(prompt="x" * 1000)
    features = build_generation_timing_features(short)
    assert features == build_generation_timing_features(same_band)
    assert features.exact_key != build_generation_timing_features(long).exact_key
    assert features.compatible_key != build_generation_timing_features(long).compatible_key
    assert build_progress_landmark_key(features, _progress()) != build_progress_landmark_key(
        build_generation_timing_features(long), _progress()
    )
    assert TIMING_FEATURE_VERSION == 2


def test_live_learning_is_immediate_bounded_and_deduplicated_by_idle_audit(tmp_path):
    factory = _session_factory(tmp_path / "learning.db")
    estimator = GenerationEtaEstimator(factory, max_profile_samples=8)
    first = _checkpoint_generation(generation_id="first", status=GenerationStatus.SUCCEEDED)
    first.completed_at = first.started_at + timedelta(seconds=40)
    active = _checkpoint_generation(generation_id="active", started_at=first.completed_at)
    features = build_generation_timing_features(first)
    with factory() as session:
        session.add_all([first, active])
        session.commit()
    estimator.observe_success(first.id, features, 40, first.completed_at)
    estimator.observe_success(first.id, features, 40, first.completed_at)
    assert estimator.estimate(active, now=active.started_at)["remaining_seconds"] == 40
    assert estimator._audit_batch().observed == 0  # A continuously busy queue.
    assert estimator._profile_for("total_exact", features.exact_key).sample_count == 1
    with factory() as session:
        session.delete(session.get(type(active), active.id))
        session.commit()
    result = estimator._audit_batch()
    estimator._apply_audit_result(result)
    assert estimator._recent == {}
    assert estimator._profile_for("total_exact", features.exact_key).sample_count == 1
    # A delayed terminal notification cannot re-add an already audited result.
    estimator.observe_success(first.id, features, 40, first.completed_at)
    assert estimator._recent == {}
    for index in range(20):
        estimator.observe_success(
            str(index), features, 50, first.completed_at + timedelta(seconds=index + 1)
        )
    assert len(estimator._recent) == 8
    assert len(estimator._profile_for("total_exact", features.exact_key).samples) == 8


@pytest.mark.parametrize(
    "changes",
    [
        {"instance_id": "other-runtime"},
        {"source_key": "other-source"},
        {"outputs": ["extra"]},
        {"preset": "other-preset"},
        {"api_hash": "other-revision"},
        {"width": 2048},
        {"prompt": "x" * 1000},
        {"iterations": 40},
        {"choice": "other-checkpoint"},
        {"feature_enabled": False},
    ],
)
def test_worker_filters_sibling_evidence_during_recovery_and_live_completion(tmp_path, changes):
    factory = _session_factory(tmp_path / "siblings.db")
    good = _checkpoint_generation(generation_id="good", status=GenerationStatus.SUCCEEDED)
    parameters = {
        key: value
        for key, value in changes.items()
        if key not in {"source_key", "outputs", "preset"}
    }
    bad = _checkpoint_generation(
        generation_id="bad", status=GenerationStatus.SUCCEEDED, **parameters
    )
    if "source_key" in changes:
        bad.generation_source_json = {
            **bad.generation_source_json,
            "source_key": changes["source_key"],
        }
    if "outputs" in changes:
        bad.requested_outputs_json = changes["outputs"]
    if "preset" in changes:
        bad.selected_preset = changes["preset"]
    good.completed_at = good.started_at + timedelta(seconds=10)
    bad.completed_at = bad.started_at + timedelta(seconds=200)
    active = _checkpoint_generation(generation_id="active")
    with factory() as session:
        session.add(GenerationRun(id="run", owner_id="owner-a", total_count=3))
        session.add_all([good, bad, active])
        session.add_all(
            [GenerationRunMember(generation_id=g.id, run_id="run") for g in [good, bad, active]]
        )
        session.commit()
    worker = QueueWorker.__new__(QueueWorker)
    worker.session_factory = factory
    worker.generation_eta = GenerationEtaEstimator(factory)
    worker._generation_run_ids = {}
    worker._run_members = {}
    worker._run_sibling_durations = {}
    worker._run_cohorts = {}
    worker._register_run_timing(active.id)
    assert worker._sibling_durations_for(active) == (10,)
    expected_compatible = (200,) if "choice" in changes else None
    assert worker._sibling_durations_for(active, compatible=True) == expected_compatible
    worker._run_sibling_durations.clear()
    for generation in [good, bad]:
        worker._generation_run_ids[generation.id] = "run"
        worker._run_members["run"].add(generation.id)
        worker._record_run_timing(
            generation.id,
            (
                generation.status,
                generation.started_at,
                generation.completed_at,
                build_generation_timing_features(generation),
            ),
        )
    assert worker._sibling_durations_for(active) == (10,)
    assert worker._sibling_durations_for(active, compatible=True) == expected_compatible


def test_version_upgrade_rebuilds_profiles_without_altering_retained_generations(tmp_path):
    from app.models import GenerationTimingAuditState, GenerationTimingProfile

    factory = _session_factory(tmp_path / "version.db")
    generation = _checkpoint_generation(status=GenerationStatus.SUCCEEDED)
    generation.completed_at = generation.started_at + timedelta(seconds=30)
    with factory() as session:
        session.add(generation)
        session.add(
            GenerationTimingAuditState(
                key="generation_eta",
                feature_version=TIMING_FEATURE_VERSION - 1,
                cursor_completed_at=generation.completed_at,
                cursor_generation_id=generation.id,
                backfill_complete=True,
            )
        )
        session.add(
            GenerationTimingProfile(
                id="old",
                feature_version=TIMING_FEATURE_VERSION - 1,
                scope="total_exact",
                scope_key="old",
                sample_count=1,
                samples_json=[900],
                median_seconds=900,
                lower_seconds=800,
                upper_seconds=1000,
            )
        )
        session.commit()
    estimator = GenerationEtaEstimator(factory)
    estimator._profiles = estimator._load_profiles()
    assert estimator._profiles == {}
    result = estimator._audit_batch()
    estimator._apply_audit_result(result)
    assert result.observed == 1
    key = build_generation_timing_features(generation).exact_key
    assert estimator._profile_for("total_exact", key).median_seconds == 30
    with factory() as session:
        assert session.get(type(generation), generation.id).status == GenerationStatus.SUCCEEDED
        assert (
            session.get(GenerationTimingAuditState, "generation_eta").feature_version
            == TIMING_FEATURE_VERSION
        )


def test_audit_application_retains_completions_newer_than_its_watermark(tmp_path):
    factory = _session_factory(tmp_path / "race.db")
    estimator = GenerationEtaEstimator(factory)
    first = _checkpoint_generation(generation_id="first", status=GenerationStatus.SUCCEEDED)
    first.completed_at = first.started_at + timedelta(seconds=30)
    with factory() as session:
        session.add(first)
        session.commit()
    features = build_generation_timing_features(first)
    estimator.observe_success(first.id, features, 30, first.completed_at)
    audit = estimator._audit_batch()
    estimator.observe_success("later", features, 40, first.completed_at + timedelta(seconds=1))
    estimator._apply_audit_result(audit)
    assert list(estimator._recent) == ["later"]
    profile = estimator._profile_for("total_exact", features.exact_key)
    assert profile.sample_count == 2
    assert profile.median_seconds == 35
