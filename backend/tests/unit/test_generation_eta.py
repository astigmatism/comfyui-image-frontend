from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.models import (
    Base,
    Generation,
    GenerationEvent,
    GenerationStatus,
    GenerationTimingAuditState,
    GenerationTimingProfile,
)
from app.services.generation_eta import (
    TIMING_FEATURE_VERSION,
    GenerationEtaEstimator,
    _empty_audit_result,
    build_generation_timing_features,
    build_progress_landmark_key,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


def _session_factory(path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False, class_=Session)


def _generation(
    *,
    generation_id: str = "00000000-0000-4000-8000-000000000001",
    status: GenerationStatus = GenerationStatus.RUNNING,
    owner_id: str = "owner-a",
    prompt: str = "private prompt",
    seed: str = "100",
    width: int = 1024,
    height: int = 1024,
    iterations: int = 20,
    choice: str = "model-a",
    feature_enabled: bool = True,
    api_hash: str = "a" * 64,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> Generation:
    started_at = started_at or datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    return Generation(
        id=generation_id,
        owner_id=owner_id,
        status=status,
        queue_seq=1,
        workflow_profile_id="profile-a",
        workflow_id="source-a",
        workflow_display_name="Source A",
        workflow_version="1",
        contract_schema_version="interface/v1",
        adapter_version="1",
        ui_graph_sha256="b" * 64,
        api_graph_sha256=api_hash,
        contract_sha256="c" * 64,
        resolved_contract_json={
            "inputs": [
                {"id": "prompt", "type": "string", "semantic_role": "positive_prompt"},
                {"id": "seed", "type": "seed", "semantic_role": "seed"},
                {"id": "width", "type": "integer", "semantic_role": "width"},
                {"id": "height", "type": "integer", "semantic_role": "height"},
                {
                    "id": "iterations",
                    "type": "integer",
                    "semantic_role": "iteration_count",
                },
                {"id": "model", "type": "choice", "semantic_role": "model_choice"},
                {
                    "id": "enhance",
                    "type": "boolean",
                    "semantic_role": "feature_toggle",
                },
                {"id": "reference", "type": "image", "semantic_role": "reference_image"},
            ]
        },
        requested_controls_json={},
        effective_controls_json={
            "prompt": prompt,
            "seed": seed,
            "width": width,
            "height": height,
            "iterations": iterations,
            "model": choice,
            "enhance": feature_enabled,
            "reference": {
                "asset_id": f"asset-{owner_id}",
                "sha256": "f" * 64,
                "width": 800,
                "height": 600,
            },
        },
        resolved_seeds_json={"seed": seed},
        requested_outputs_json=[],
        final_prompt=prompt,
        compiled_graph_json={},
        compiled_graph_sha256="d" * 64,
        generation_source_json={
            "instance_id": "test-instance",
            "source_key": "source-key-a",
            "api_sha256": api_hash,
        },
        started_at=started_at,
        completed_at=completed_at,
    )


def _progress(*, node_id: str = "sampler-1", fraction: float = 0.5) -> dict[str, Any]:
    return {
        "kind": "node",
        "node_id": node_id,
        "display_node_id": node_id,
        "real_node_id": node_id,
        "parent_node_id": None,
        "label": "Sampling",
        "value": fraction * 100,
        "maximum": 100,
        "fraction": fraction,
        "updated_at": "2026-07-18T12:00:00Z",
    }


def test_timing_features_hash_compute_inputs_without_content_or_identity() -> None:
    first = _generation(
        owner_id="private-owner-a",
        prompt="a secret portrait prompt",
        seed="123",
    )
    second = _generation(
        owner_id="private-owner-b",
        prompt="a completely different private prompt",
        seed="999999999999999",
    )

    first_features = build_generation_timing_features(first)
    second_features = build_generation_timing_features(second)

    assert first_features == second_features
    assert first_features.feature_version == TIMING_FEATURE_VERSION
    persisted = first_features.persisted_hashes()
    assert all(len(value) == 64 for key, value in persisted.items() if key != "feature_version")
    assert "prompt" not in repr(first_features).casefold()
    assert "owner" not in repr(first_features).casefold()
    assert "asset" not in repr(first_features).casefold()

    changed_compute = build_generation_timing_features(
        _generation(iterations=30, choice="model-b", feature_enabled=False)
    )
    assert changed_compute.exact_key != first_features.exact_key
    assert changed_compute.revision_resolution_key == first_features.revision_resolution_key

    changed_resolution = build_generation_timing_features(_generation(width=1536, height=1024))
    assert changed_resolution.revision_resolution_key != first_features.revision_resolution_key
    assert changed_resolution.revision_key == first_features.revision_key

    changed_revision = build_generation_timing_features(_generation(api_hash="e" * 64))
    assert changed_revision.revision_key != first_features.revision_key

    changed_outputs = _generation()
    changed_outputs.requested_outputs_json = ["preview"]
    assert build_generation_timing_features(changed_outputs).exact_key != first_features.exact_key

    dotted_steps = _generation()
    dotted_steps.resolved_contract_json["inputs"].append(
        {"id": "sampling.main.steps", "type": "integer", "semantic_role": "tuning"}
    )
    dotted_steps.effective_controls_json["sampling.main.steps"] = 40
    assert build_generation_timing_features(dotted_steps).exact_key != first_features.exact_key


def test_hierarchical_fallback_uses_robust_recent_statistics_and_low_confidence_first_sample(
    tmp_path: Path,
) -> None:
    factory = _session_factory(tmp_path / "eta-fallback.db")
    estimator = GenerationEtaEstimator(factory)
    generation = _generation()
    features = build_generation_timing_features(generation)
    with factory() as session:
        for sample in [20.0, 19.0, 21.0, 20.0, 20.0, 1_000.0]:
            estimator._add_profile_sample(session, "total_revision", features.revision_key, sample)
        session.commit()
    estimator._profiles = estimator._load_profiles()

    current = generation.started_at + timedelta(seconds=5)  # type: ignore[operator]
    estimate = estimator.estimate(generation, now=current)

    assert estimate is not None
    assert estimate["basis"] == "historical_revision"
    assert estimate["remaining_seconds"] == 15.0
    assert estimate["upper_seconds"] < 40
    assert estimate["confidence"] == "low"

    with factory() as session:
        estimator._add_profile_sample(session, "total_exact", features.exact_key, 30.0)
        session.commit()
    estimator._profiles = estimator._load_profiles()
    first_sample = estimator.estimate(generation, now=current)

    assert first_sample is not None
    assert first_sample["basis"] == "historical_exact"
    assert first_sample["remaining_seconds"] == 25.0
    assert first_sample["confidence"] == "low"

    overrun = estimator.estimate(
        generation,
        now=generation.started_at + timedelta(seconds=40),  # type: ignore[operator]
    )
    assert overrun is not None
    assert overrun["remaining_seconds"] > 0
    assert overrun["confidence"] == "low"

    generation.status = GenerationStatus.CANCEL_REQUESTED
    assert estimator.estimate(generation, now=current) is None


def test_idle_audit_deduplicates_success_and_learns_exact_node_decile_residual(
    tmp_path: Path,
) -> None:
    factory = _session_factory(tmp_path / "eta-landmark.db")
    estimator = GenerationEtaEstimator(factory, audit_batch_size=4)
    started = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    completed = started + timedelta(seconds=100)
    completed_generation = _generation(
        status=GenerationStatus.SUCCEEDED,
        started_at=started,
        completed_at=completed,
    )
    queued = _generation(
        generation_id="00000000-0000-4000-8000-000000000002",
        status=GenerationStatus.QUEUED,
    )
    with factory() as session:
        session.add_all([completed_generation, queued])
        session.add(
            GenerationEvent(
                generation_id=completed_generation.id,
                owner_id=completed_generation.owner_id,
                event_type="generation.progress",
                payload_json={"progress": _progress(fraction=0.5)},
                created_at=completed - timedelta(seconds=12),
            )
        )
        session.add(
            GenerationEvent(
                generation_id=completed_generation.id,
                owner_id=completed_generation.owner_id,
                event_type="generation.progress",
                payload_json={"progress": _progress(fraction=0.59)},
                created_at=completed - timedelta(seconds=8),
            )
        )
        session.commit()

    assert estimator._audit_batch().observed == 0
    with factory() as session:
        session.delete(session.get(Generation, queued.id))  # type: ignore[arg-type]
        session.commit()

    learned = estimator._audit_batch()
    assert learned.observed == 1
    estimator._apply_audit_result(learned)
    assert estimator._audit_batch().observed == 0
    with factory() as session:
        state = session.get(GenerationTimingAuditState, "generation_eta")
        assert state is not None
        assert state.feature_version == TIMING_FEATURE_VERSION
        assert state.cursor_generation_id == completed_generation.id
        assert state.backfill_complete is True
        assert session.scalar(select(func.count()).select_from(GenerationTimingAuditState)) == 1

    running = _generation(started_at=completed)
    landmark_key = build_progress_landmark_key(
        build_generation_timing_features(running), _progress(fraction=0.5)
    )
    assert landmark_key is not None
    current = completed + timedelta(seconds=10)
    landmark_estimate = estimator.estimate(
        running,
        progress=_progress(fraction=0.5),
        now=current,
    )

    assert landmark_estimate is not None
    assert landmark_estimate["basis"] == "progress_landmark"
    assert landmark_estimate["remaining_seconds"] == 12.0
    assert landmark_estimate["confidence"] == "low"

    other_node = estimator.estimate(
        running,
        progress=_progress(node_id="different-node", fraction=0.5),
        now=current,
    )
    assert other_node is not None
    assert other_node["basis"] == "historical_exact"
    assert other_node["remaining_seconds"] == 90.0


def test_confidence_is_capped_by_profile_compatibility(tmp_path: Path) -> None:
    factory = _session_factory(tmp_path / "eta-confidence.db")
    estimator = GenerationEtaEstimator(factory)
    generation = _generation()
    features = build_generation_timing_features(generation)
    progress = _progress(fraction=0.5)
    landmark_key = build_progress_landmark_key(features, progress)
    assert landmark_key is not None
    with factory() as session:
        for index in range(20):
            sample = 30.0 + (index % 3)
            estimator._add_profile_sample(session, "total_revision", features.revision_key, sample)
            estimator._add_profile_sample(
                session,
                "total_revision_resolution",
                features.revision_resolution_key,
                sample,
            )
            estimator._add_profile_sample(session, "total_exact", features.exact_key, sample)
            estimator._add_profile_sample(
                session, "progress_landmark", landmark_key, 12.0 + (index % 3)
            )
        session.commit()

    profiles = estimator._load_profiles()
    current = generation.started_at + timedelta(seconds=5)  # type: ignore[operator]

    estimator._profiles = {
        ("total_revision", features.revision_key): profiles[
            ("total_revision", features.revision_key)
        ]
    }
    revision = estimator.estimate(generation, now=current)
    assert revision is not None
    assert revision["basis"] == "historical_revision"
    assert revision["confidence"] == "low"

    estimator._profiles = {
        ("total_revision_resolution", features.revision_resolution_key): profiles[
            ("total_revision_resolution", features.revision_resolution_key)
        ]
    }
    revision_resolution = estimator.estimate(generation, now=current)
    assert revision_resolution is not None
    assert revision_resolution["basis"] == "historical_revision_resolution"
    assert revision_resolution["confidence"] == "medium"

    estimator._profiles = {
        ("total_exact", features.exact_key): profiles[("total_exact", features.exact_key)]
    }
    exact = estimator.estimate(generation, now=current)
    assert exact is not None
    assert exact["basis"] == "historical_exact"
    assert exact["confidence"] == "high"

    estimator._profiles = {
        ("progress_landmark", landmark_key): profiles[("progress_landmark", landmark_key)]
    }
    landmark = estimator.estimate(generation, progress=progress, now=current)
    assert landmark is not None
    assert landmark["basis"] == "progress_landmark"
    assert landmark["confidence"] == "high"


def test_profile_retention_has_deterministic_total_and_landmark_quotas(
    tmp_path: Path,
) -> None:
    factory = _session_factory(tmp_path / "eta-profile-bounds.db")
    estimator = GenerationEtaEstimator(factory, max_profiles=8)
    base = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    total_keys = [f"{index:064x}" for index in range(10)]
    landmark_keys = [f"{index + 100:064x}" for index in range(6)]
    with factory() as session:
        for index, key in enumerate(total_keys):
            estimator._add_profile_sample(
                session,
                "total_exact",
                key,
                10.0 + index,
                observed_at=base + timedelta(seconds=index),
            )
        for index, key in enumerate(landmark_keys):
            estimator._add_profile_sample(
                session,
                "progress_landmark",
                key,
                5.0 + index,
                observed_at=base + timedelta(seconds=index),
            )
        session.commit()

    loaded = estimator._load_profiles()
    with factory() as session:
        retained = session.execute(
            select(GenerationTimingProfile.scope, GenerationTimingProfile.scope_key)
        ).all()

    retained_total = {key for scope, key in retained if scope != "progress_landmark"}
    retained_landmark = {key for scope, key in retained if scope == "progress_landmark"}
    assert len(loaded) == 8
    assert retained_total == set(total_keys[-6:])
    assert retained_landmark == set(landmark_keys[-2:])


def test_audit_cursor_is_single_bounded_versioned_state_and_survives_deletion(
    tmp_path: Path,
) -> None:
    factory = _session_factory(tmp_path / "eta-cursor.db")
    estimator = GenerationEtaEstimator(factory, audit_batch_size=2)
    base = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)

    generations = [
        _generation(
            generation_id=f"00000000-0000-4000-8000-{index:012d}",
            status=GenerationStatus.SUCCEEDED,
            started_at=base + timedelta(seconds=index),
            completed_at=base + timedelta(seconds=index + 10),
        )
        for index in range(1, 4)
    ]
    with factory() as session:
        session.add_all(generations)
        session.commit()

    first = estimator._audit_batch()
    second = estimator._audit_batch()
    assert (first.observed, first.has_more) == (2, True)
    assert (second.observed, second.has_more) == (1, False)
    estimator._apply_audit_result(first)
    estimator._apply_audit_result(second)
    assert estimator._audit_batch().observed == 0

    with factory() as session:
        state = session.get(GenerationTimingAuditState, "generation_eta")
        assert state is not None
        assert state.cursor_generation_id == generations[-1].id
        assert state.backfill_complete is True
        session.delete(session.get(Generation, generations[-1].id))  # type: ignore[arg-type]
        session.commit()

    later = _generation(
        generation_id="00000000-0000-4000-8000-000000000004",
        status=GenerationStatus.SUCCEEDED,
        started_at=base + timedelta(seconds=20),
        completed_at=base + timedelta(seconds=40),
    )
    with factory() as session:
        session.add(later)
        session.commit()
    assert estimator._audit_batch().observed == 1

    with factory() as session:
        state = session.get(GenerationTimingAuditState, "generation_eta")
        assert state is not None
        assert state.cursor_generation_id == later.id
        assert session.scalar(select(func.count()).select_from(GenerationTimingAuditState)) == 1
        state.feature_version = TIMING_FEATURE_VERSION - 1
        state.cursor_completed_at = datetime(2100, 1, 1, tzinfo=UTC)
        state.cursor_generation_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        session.commit()

    version_backfill = estimator._audit_batch()
    assert version_backfill.observed == 2
    with factory() as session:
        state = session.get(GenerationTimingAuditState, "generation_eta")
        assert state is not None
        assert state.feature_version == TIMING_FEATURE_VERSION
        assert state.cursor_generation_id == generations[1].id
        assert session.scalar(select(func.count()).select_from(GenerationTimingAuditState)) == 1


def test_progress_event_cap_is_per_generation_and_uses_earliest_bucket_sample(
    tmp_path: Path,
) -> None:
    factory = _session_factory(tmp_path / "eta-progress-cap.db")
    estimator = GenerationEtaEstimator(factory, audit_batch_size=4)
    base = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    verbose = _generation(
        generation_id="00000000-0000-4000-8000-000000000101",
        status=GenerationStatus.SUCCEEDED,
        started_at=base,
        completed_at=base + timedelta(seconds=200),
    )
    quiet = _generation(
        generation_id="00000000-0000-4000-8000-000000000102",
        status=GenerationStatus.SUCCEEDED,
        started_at=base + timedelta(seconds=5),
        completed_at=base + timedelta(seconds=210),
    )
    with factory() as session:
        session.add_all([verbose, quiet])
        for index in range(80):
            session.add(
                GenerationEvent(
                    generation_id=verbose.id,
                    owner_id=verbose.owner_id,
                    event_type="generation.progress",
                    payload_json={"progress": _progress(node_id="verbose", fraction=0.55)},
                    created_at=verbose.completed_at - timedelta(seconds=100 - index),
                )
            )
        session.add(
            GenerationEvent(
                generation_id=quiet.id,
                owner_id=quiet.owner_id,
                event_type="generation.progress",
                payload_json={"progress": _progress(node_id="quiet", fraction=0.55)},
                created_at=quiet.completed_at - timedelta(seconds=15),
            )
        )
        session.commit()
        loaded_events = estimator._load_progress_events(session, [verbose.id, quiet.id])
        assert len(loaded_events[verbose.id]) == 64
        assert len(loaded_events[quiet.id]) == 1

    result = estimator._audit_batch()
    assert result.observed == 2
    estimator._apply_audit_result(result)
    running = _generation(started_at=quiet.completed_at)
    current = quiet.completed_at + timedelta(seconds=1)
    verbose_eta = estimator.estimate(
        running,
        progress=_progress(node_id="verbose", fraction=0.55),
        now=current,
    )
    quiet_eta = estimator.estimate(
        running,
        progress=_progress(node_id="quiet", fraction=0.55),
        now=current,
    )
    assert verbose_eta is not None
    assert verbose_eta["basis"] == "progress_landmark"
    assert verbose_eta["remaining_seconds"] == 100.0
    assert quiet_eta is not None
    assert quiet_eta["basis"] == "progress_landmark"
    assert quiet_eta["remaining_seconds"] == 15.0


def test_audit_rolls_back_if_generation_becomes_active_before_commit(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    factory = _session_factory(tmp_path / "eta-active-race.db")
    estimator = GenerationEtaEstimator(factory)
    completed = _generation(
        status=GenerationStatus.SUCCEEDED,
        completed_at=datetime(2026, 7, 18, 12, 1, tzinfo=UTC),
    )
    with factory() as session:
        session.add(completed)
        session.commit()

    checks = iter((False, True))
    monkeypatch.setattr(estimator, "_has_active_generation", lambda _session: next(checks))
    assert estimator._audit_batch().observed == 0
    with factory() as session:
        assert session.get(GenerationTimingAuditState, "generation_eta") is None
        assert session.scalar(select(func.count()).select_from(GenerationTimingProfile)) == 0


async def test_stop_cooperatively_joins_inflight_audit(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    factory = _session_factory(tmp_path / "eta-stop.db")
    estimator = GenerationEtaEstimator(factory, audit_interval_seconds=60)
    entered = threading.Event()
    exited = threading.Event()

    def blocking_audit() -> Any:
        entered.set()
        estimator._audit_stop_event.wait(timeout=1)
        exited.set()
        return _empty_audit_result()

    monkeypatch.setattr(estimator, "_audit_batch", blocking_audit)
    await estimator.start()
    assert await asyncio.to_thread(entered.wait, 0.5)
    await asyncio.wait_for(estimator.stop(), timeout=0.5)
    assert exited.is_set()
