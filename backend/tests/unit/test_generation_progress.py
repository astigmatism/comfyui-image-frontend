from __future__ import annotations

import math
from typing import Any

import pytest
from app.schemas import GenerationProgress
from app.services.queue_worker import (
    QueueWorker,
    _finite_number,
    _progress_snapshot,
    _ProgressTracker,
    _runtime_node_identities,
    _safe_progress_label,
)


def test_progress_eta_requires_an_ordered_interval() -> None:
    payload = {
        "kind": "indeterminate",
        "label": "Loading model",
        "updated_at": "2026-07-18T12:00:00Z",
        "eta": {
            "remaining_seconds": 12,
            "completion_at": "2026-07-18T12:00:12Z",
            "lower_seconds": 8,
            "upper_seconds": 20,
            "confidence": "low",
            "basis": "historical_exact",
            "updated_at": "2026-07-18T12:00:00Z",
        },
    }
    assert GenerationProgress.model_validate(payload).eta is not None
    payload["eta"] = {**payload["eta"], "lower_seconds": 13}
    with pytest.raises(ValueError, match="ETA interval"):
        GenerationProgress.model_validate(payload)


def test_progress_value_and_label_boundaries_reject_unsafe_values() -> None:
    assert _finite_number(True) is None
    assert _finite_number(math.nan) is None
    assert _finite_number(math.inf) is None
    assert _finite_number(12) == 12
    assert _safe_progress_label("  Main\n sampling  ") == "Main sampling"
    assert _safe_progress_label(42) is None


def test_progress_snapshot_preserves_subgraph_identity_fields() -> None:
    identities = _runtime_node_identities(
        {
            "node_id": "54",
            "display_node_id": "12",
            "real_node_id": "54",
            "parent_node_id": "8",
        }
    )
    snapshot = _progress_snapshot(
        kind="node",
        label="Main sampling",
        identities=identities,
        value=12,
        maximum=24,
        fraction=0.5,
    )
    assert snapshot["node_id"] == "54"
    assert snapshot["display_node_id"] == "12"
    assert snapshot["real_node_id"] == "54"
    assert snapshot["parent_node_id"] == "8"


@pytest.mark.asyncio
async def test_progress_state_is_preferred_to_legacy_for_the_same_node() -> None:
    worker = object.__new__(QueueWorker)
    worker._progress_trackers = {"generation": _ProgressTracker()}
    snapshots: list[dict[str, Any]] = []

    async def label(_generation_id: str, _identities: object) -> str:
        return "Main sampling"

    async def record(
        _generation_id: str,
        snapshot: dict[str, Any],
        *,
        audit: bool = False,
    ) -> None:
        del audit
        snapshots.append(snapshot)

    worker._resolve_progress_label = label  # type: ignore[method-assign]
    worker._queue_progress_snapshot = record  # type: ignore[method-assign]
    await worker._record_progress_state(
        "generation",
        {
            "nodes": {
                "54": {
                    "state": "running",
                    "node_id": "54",
                    "value": 3,
                    "max": 8,
                }
            }
        },
    )
    await worker._record_legacy_progress(
        "generation",
        {"node": "54", "value": 4, "max": 8},
    )

    assert len(snapshots) == 1
    assert snapshots[0]["kind"] == "node"
    assert snapshots[0]["value"] == 3
    assert snapshots[0]["maximum"] == 8


@pytest.mark.asyncio
async def test_other_prompt_runtime_event_is_ignored() -> None:
    worker = object.__new__(QueueWorker)

    async def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("mismatched prompt reached progress processing")

    worker._record_legacy_progress = unexpected  # type: ignore[method-assign]
    terminal = await worker._process_runtime_event(
        "generation",
        "wanted-prompt",
        {
            "type": "progress",
            "data": {
                "prompt_id": "other-prompt",
                "node": "54",
                "value": 1,
                "max": 2,
            },
        },
    )
    assert terminal is False


@pytest.mark.asyncio
async def test_progress_flood_is_coalesced_while_preserving_first_and_final_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = object.__new__(QueueWorker)
    tracker = _ProgressTracker()
    worker._progress_trackers = {"generation": tracker}
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr("app.services.queue_worker.time.monotonic", lambda: 100.0)

    async def persist(
        _generation_id: str,
        snapshot: dict[str, Any],
        *,
        audit: bool,
    ) -> None:
        del audit
        saved.append(snapshot)
        tracker.last_persisted_monotonic = 100.0

    worker._persist_progress_snapshot = persist  # type: ignore[method-assign]
    identities = {"node_id": "54"}
    for value in range(100):
        await worker._queue_progress_snapshot(
            "generation",
            _progress_snapshot(
                kind="node",
                label="Main sampling",
                identities=identities,
                value=value,
                maximum=99,
                fraction=value / 99,
            ),
        )
    await worker._flush_pending_progress("generation", force=True, audit=True)

    assert [snapshot["value"] for snapshot in saved] == [0, 99]
