from __future__ import annotations

import asyncio
import time
from typing import Any

from app.domain.results import NativeFileOutput
from app.main import create_app
from app.models import Artifact, Generation, GenerationStatus
from app.services.queue_worker import (
    QueueWorker,
    _artifact_requires_persistence,
    _artifact_sequence,
    _history_terminal,
    _is_better_presentation_candidate,
)
from fastapi.testclient import TestClient
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


def test_real_comfyui_interruption_history_is_terminal_cancellation() -> None:
    history = {
        "outputs": {"900": {"images": []}},
        "status": {
            "status_str": "error",
            "completed": False,
            "messages": [["execution_interrupted", {"prompt_id": "native-prompt"}]],
        },
    }

    assert _history_terminal(history) == "cancelled"


def test_only_declared_final_durable_files_are_required_for_success() -> None:
    def file_output(*, declared: bool, role: str, storage_type: str) -> NativeFileOutput:
        return NativeFileOutput(
            node_id="130",
            output_id="final_image" if declared else "native:130",
            role=role,
            kind="image",
            batch_index=0,
            reference={"filename": "result.png", "subfolder": "", "type": storage_type},
            declared=declared,
        )

    assert _artifact_requires_persistence(
        file_output(declared=True, role="final", storage_type="output")
    )
    assert not _artifact_requires_persistence(
        file_output(declared=True, role="preview", storage_type="output")
    )
    assert not _artifact_requires_persistence(
        file_output(declared=True, role="final", storage_type="temp")
    )
    assert not _artifact_requires_persistence(
        file_output(declared=False, role="unmapped", storage_type="output")
    )


def test_authored_artifact_sequence_prioritizes_roles_then_manifest_order() -> None:
    def file_output(role: str, sequence: int) -> NativeFileOutput:
        return NativeFileOutput(
            node_id="130",
            output_id=role,
            role=role,
            kind="image",
            sequence=sequence,
            batch_index=0,
            reference={"filename": f"{role}.png", "subfolder": "", "type": "output"},
            declared=role != "unmapped",
        )

    ranked = [
        _artifact_sequence(file_output("unmapped", 0)),
        _artifact_sequence(file_output("auxiliary", 4)),
        _artifact_sequence(file_output("preview", 0)),
        _artifact_sequence(file_output("comparison", 1)),
        _artifact_sequence(file_output("final", 0)),
    ]
    assert ranked == sorted(ranked)
    assert _artifact_sequence(file_output("preview", 2)) > _artifact_sequence(
        file_output("preview", 1)
    )


def test_presentation_candidate_advances_stage_but_keeps_batch_zero_stable() -> None:
    current_batch_zero = Artifact(sequence=3_001, batch_index=0)
    same_stage_batch_one = Artifact(sequence=3_001, batch_index=1)
    later_final = Artifact(sequence=4_002, batch_index=1)
    earlier_arriving_batch = Artifact(sequence=4_002, batch_index=2)
    final_batch_zero = Artifact(sequence=4_002, batch_index=0)

    assert not _is_better_presentation_candidate(same_stage_batch_one, current_batch_zero)
    assert _is_better_presentation_candidate(later_final, current_batch_zero)
    assert _is_better_presentation_candidate(final_batch_zero, earlier_arriving_batch)


async def test_terminal_history_wait_returns_latest_partial_snapshot_when_terminal_entry_lags(
    monkeypatch,
) -> None:
    partial_histories = [
        {
            "outputs": {"900": {"images": [{"filename": "partial.png", "type": "temp"}]}},
            "status": {"status_str": "running", "completed": False},
        },
        {
            "outputs": {
                "900": {"images": [{"filename": "partial.png", "type": "temp"}]},
                "901": {"text": ["latest partial metadata"]},
            },
            "status": {"status_str": "running", "completed": False},
        },
    ]

    class PartialHistoryAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def history(self, prompt_id: str) -> dict[str, Any] | None:
            assert prompt_id == "terminal-hint-prompt"
            self.calls += 1
            if self.calls <= len(partial_histories):
                return partial_histories[self.calls - 1]
            return None

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_delay)
    worker = object.__new__(QueueWorker)
    worker.comfyui = PartialHistoryAdapter()

    history = await worker._wait_for_history("terminal-hint-prompt")

    assert history == partial_histories[-1]
    assert history is not None
    assert set(history["outputs"]) == {"900", "901"}


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
        assert completed["best_available_artifact_id"] == completed["canonical_artifact_id"]
        assert completed["final_artifact_count"] == 1
        assert [item["prompt"] for item in fake_state.submitted] == ["queued across restart"]


def test_running_generation_reconciles_after_application_restart(
    settings_factory, fake_state
) -> None:
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
        assert reconciled["artifact_count"] == 5
        assert reconciled["final_artifact_count"] == 1
        assert reconciled["canonical_artifact_id"] is not None
        assert len(fake_state.submitted) == 1


def test_startup_recovery_retries_delayed_terminal_history_before_interrupting(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="restart.delayed.history")
        generation = create_generation(first, "slow delayed startup history", seed=18)
        running = wait_for_generation(
            first,
            generation["id"],
            lambda item: item["status"] == "running" and item["artifact_count"] >= 1,
        )
        prompt_id = running["prompt_id"]
        assert prompt_id

    deadline = time.monotonic() + 5
    while prompt_id in fake_state.running_prompt_ids and time.monotonic() < deadline:
        time.sleep(0.02)
    assert prompt_id not in fake_state.running_prompt_ids
    assert fake_state.histories[prompt_id]["status"]["status_str"] == "success"
    fake_state.history_calls[prompt_id] = 0
    fake_state.history_delay_polls = 4

    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie, name=settings.session_cookie_name)
        recovered = wait_for_status(second, generation["id"], "succeeded", timeout=10)
        assert recovered["comfyui_status"]["status_str"] == "success"
        assert fake_state.history_calls[prompt_id] > fake_state.history_delay_polls


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
        # Startup recovery is managed in the background so local HTTP readiness is immediate.
        # The durable running row remains visible while reconciliation reaches its terminal state.
        detail = wait_for_status(second, generation["id"], "interrupted", timeout=10)
        assert detail["error_code"] == "execution_interrupted"
        assert second.get(f"/api/generations/{generation['id']}/recall").json()["available"] is True


def test_comfyui_outage_preserves_history_and_pauses_dispatch(settings_factory, fake_state) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="outage.user")
        queued = create_generation(first, "resume after outage", seed=14)
        blocked_payload = generation_payload(first, "blocked while down", seed=15)

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
            json=blocked_payload,
        )
        assert rejected.status_code == 503

        fake_state.service_available = True
        completed = wait_for_status(second, queued["id"], "succeeded", timeout=10)
        assert completed["canonical_artifact_id"] is not None
        assert completed["best_available_artifact_id"] == completed["canonical_artifact_id"]


def test_orphaned_live_prompt_releases_slot_for_next_generation(
    settings_factory, fake_state
) -> None:
    fake_state.orphan_prompt_substrings.add("orphaned outside app")
    settings = settings_factory(enable_background_worker=True, comfyui_concurrency=1)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="queue.orphan.recovery")
        orphaned = create_generation(client, "orphaned outside app", seed=141)
        successor = create_generation(client, "runs after orphan recovery", seed=142)

        interrupted = wait_for_status(client, orphaned["id"], "interrupted", timeout=10)
        completed = wait_for_status(client, successor["id"], "succeeded", timeout=10)

        assert interrupted["error_code"] == "execution_interrupted"
        assert interrupted["comfyui_status"] == {
            "status_str": "running",
            "completed": False,
            "messages": [],
        }
        assert completed["canonical_artifact_id"] is not None
        assert [item["prompt"] for item in fake_state.submitted] == [
            "orphaned outside app",
            "runs after orphan recovery",
        ]


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


def test_empty_catalog_recovers_when_comfyui_returns_without_application_restart(
    settings_factory, fake_state
) -> None:
    fake_state.service_available = False
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="catalog.recovery")
        assert client.get("/api/workflows").json() == []

        fake_state.service_available = True
        deadline = time.monotonic() + 5
        recovered: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            recovered = client.get("/api/workflows").json()
            if any(
                item["display_name"] == "Krea 2 NSFW V4" and item["available"] is True
                for item in recovered
            ):
                break
            time.sleep(0.03)
        else:
            raise AssertionError(f"published source catalog did not recover: {recovered}")

        accepted = create_generation(client, "catalog recovered generation", seed=17)
        complete = wait_for_status(client, accepted["id"], "succeeded")
        assert complete["best_available_artifact_id"] is not None
