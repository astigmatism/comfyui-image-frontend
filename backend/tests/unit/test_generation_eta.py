from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.models import Base, Generation, GenerationStatus, GenerationTimingProfile
from app.services.generation_eta import (
    TIMING_FEATURE_VERSION,
    GenerationEtaEstimator,
    build_generation_timing_features,
    native_execution_timing,
    reliable_statistics,
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
    instance_id: str = "test-instance",
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> Generation:
    started_at = started_at or datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    return Generation(
        id=generation_id,
        owner_id=owner_id,
        status=status,
        queue_seq=1,
        comfyui_instance_id=instance_id,
        comfyui_instance_label=instance_id,
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


def _progress(
    *, node_id: str = "sampler-1", fraction: float = 0.5, at: datetime | None = None
) -> dict[str, Any]:
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
        "updated_at": at.isoformat() if at else "2026-07-18T12:00:00Z",
    }


def _checkpoint_generation(**kwargs: Any) -> Generation:
    """A generation whose model selector matches the checkpoint rule (role "model")."""
    generation = _generation(**kwargs)
    for definition in generation.resolved_contract_json["inputs"]:
        if definition.get("id") == "model":
            definition["semantic_role"] = "model"
    return generation


def timing(generation, seconds):
    return {
        "version": TIMING_FEATURE_VERSION,
        "prompt_id": generation.comfyui_prompt_id,
        "provenance": "native",
        "started_at": generation.started_at.isoformat(),
        "finished_at": (generation.started_at + timedelta(seconds=seconds)).isoformat(),
        "duration_seconds": seconds,
    }


def learn(estimator, generation, seconds):
    generation.status = GenerationStatus.SUCCEEDED
    generation.comfyui_prompt_id = generation.comfyui_prompt_id or generation.id
    generation.execution_timing_json = timing(generation, seconds)
    with estimator.session_factory() as session:
        estimator.record_success(session, generation)
        session.commit()
    estimator.refresh()


def history(seconds=60, *, prompt_id="p", start=1_750_000_000_000):
    return {
        "prompt": [0, prompt_id, {"1": {}, "2": {}}],
        "status": {
            "status_str": "success",
            "messages": [
                ["execution_start", {"prompt_id": prompt_id, "timestamp": start}],
                [
                    "execution_success",
                    {"prompt_id": prompt_id, "timestamp": start + seconds * 1000},
                ],
            ],
        },
    }


def test_native_milliseconds_not_submission_or_delivery_time():
    result = native_execution_timing(history(60), "p")
    assert result["duration_seconds"] == 60
    assert result["provenance"] == "native"


@pytest.mark.parametrize("bad", [None, True, float("nan"), -1, 1_750_000_000, 9e20])
def test_bad_native_timestamps_are_not_evidence(bad):
    h = history()
    h["status"]["messages"][0][1]["timestamp"] = bad
    assert native_execution_timing(h, "p") is None


def test_prompt_identity_duplicate_attempts_and_cache():
    h = history()
    assert native_execution_timing(h, "other") is None
    h["status"]["messages"] *= 2
    assert native_execution_timing(h, "p")["duration_seconds"] == 60
    h["status"]["messages"].append(
        ["execution_start", {"prompt_id": "p", "timestamp": 1_750_000_000_001}]
    )
    assert native_execution_timing(h, "p") is None
    h = history()
    h["status"]["messages"].append(["execution_cached", {"prompt_id": "p", "nodes": ["1", "2"]}])
    assert native_execution_timing(h, "p") is None
    assert native_execution_timing(history(-1), "p") is None


@pytest.mark.parametrize(
    "values, expected",
    [
        ([60, 87600], None),
        ([60, 120, 43200], 90),
        ([60, 61, 62, 87600], 61),
        ([1, 100, 200], None),
        ([60], 60),
        ([60, 120], 90),
    ],
)
def test_robust_sparse_statistics(values, expected):
    result = reliable_statistics(values, "batch")
    assert (result.seconds if result else None) == expected
    if result and len(values) < 5:
        assert result.confidence != "high"


def test_batch_first_last_five_then_exact_history(tmp_path):
    estimator = GenerationEtaEstimator(_session_factory(tmp_path / "timing.db"))
    current = _generation(generation_id="current")
    current.timing_batch_id = "batch"
    for i in range(12):
        g = _generation(
            generation_id=f"sample-{i:02}",
            started_at=current.started_at + timedelta(minutes=i * 10),
        )
        g.timing_batch_id = "batch" if i < 6 else "other"
        learn(estimator, g, 500 if i == 0 else 60 if i < 6 else 120)
    result = estimator.duration(current)
    assert (result.seconds, result.sample_count, result.basis) == (60, 5, "batch")
    current.timing_batch_id = "unrelated"
    assert estimator.duration(current).basis == "historical_exact"


def test_prompt_boundaries_nearby_and_compute_identity(tmp_path):
    estimator = GenerationEtaEstimator(_session_factory(tmp_path / "timing.db"))
    current = _generation(prompt="x" * 63)
    for i in range(3):
        learn(estimator, _generation(generation_id=f"s{i}", prompt="y" * 62), 60 + i)
        assert (estimator.duration(current) is not None) == (i >= 2)
    assert estimator.duration(current).basis == "historical_nearby"
    for field, value in [
        ("comfyui_instance_id", "other"),
        ("api_graph_sha256", "new"),
        ("final_prompt", "z" * 200),
    ]:
        changed = _generation(prompt="x" * 63)
        setattr(changed, field, value)
        assert estimator.duration(changed) is None
    for kwargs in (
        {"width": 2048},
        {"width": 512, "height": 2048},
        {"choice": "other"},
        {"iterations": 40},
    ):
        assert estimator.duration(_generation(prompt="x" * 63, **kwargs)) is None
    a = build_generation_timing_features(_generation(prompt="secret", owner_id="a", seed="1"))
    b = build_generation_timing_features(_generation(prompt="hidden", owner_id="b", seed="2"))
    assert a == b and "secret" not in repr(a)


def test_samples_are_durable_deduplicated_bounded_and_failed_excluded(tmp_path):
    factory = _session_factory(tmp_path / "timing.db")
    estimator = GenerationEtaEstimator(factory, max_profile_samples=5, max_profiles=2)
    for i in range(8):
        g = _generation(generation_id=f"s{i}")
        learn(estimator, g, 60)
        learn(estimator, g, 60)
    with factory() as session:
        row = session.scalar(select(GenerationTimingProfile))
        assert row.sample_count == len(row.samples_json) == 5
        g.status = GenerationStatus.FAILED_WITHOUT_ARTIFACTS
        g.id = "failed"
        estimator.record_success(session, g)
        session.commit()
        assert row.sample_count == 5
    fresh = GenerationEtaEstimator(factory)
    fresh.refresh()
    assert fresh.duration(_generation()).seconds == 60
    for model in ("b", "c", "d"):
        learn(estimator, _generation(generation_id=model, choice=model), 60)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(GenerationTimingProfile)) == 2
    assert len(estimator._profiles) == 2


def test_backfill_only_trains_native_history_not_legacy_wall_time(tmp_path):
    factory = _session_factory(tmp_path / "backfill.db")
    estimator = GenerationEtaEstimator(factory, audit_batch_size=1)
    with factory() as session:
        for i in range(2):
            g = _generation(
                generation_id=f"old-{i}",
                status=GenerationStatus.SUCCEEDED,
                completed_at=datetime(2026, 7, 18, 20, i, tzinfo=UTC),
            )
            g.comfyui_prompt_id = f"p{i}"
            if i:
                g.raw_history_json = history(60, prompt_id=g.comfyui_prompt_id)
            session.add(g)
        session.commit()
    while estimator._audit_batch():
        pass
    result = estimator.duration(_generation())
    assert result.seconds == 60
    assert result.sample_count == 1
    estimator._audit_batch()
    assert estimator.duration(_generation()).sample_count == 1
