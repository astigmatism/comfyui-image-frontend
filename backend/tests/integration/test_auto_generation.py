from __future__ import annotations

import asyncio
import time

from app.main import create_app
from app.models import (
    AutoGeneration,
    AutoGenerationCycle,
    Generation,
    GenerationStatus,
    WorkflowProfile,
)
from app.schemas import SharedSettings
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import csrf
from tests.helpers import create_generation, generation_payload, provision_user, restore_cookie


def command(client, path="", **payload):
    method = client.put if not path else client.post
    return method(
        "/api/auto-generation" + path, headers={"X-CSRF-Token": csrf(client)}, json=payload
    )


def enable(client, **overrides):
    snapshot = {"generation": generation_payload(client, "lighthouse"), **overrides}
    result = command(
        client,
        expected_revision=client.get("/api/auto-generation").json()["revision"],
        enabled=True,
        snapshot=snapshot,
    )
    assert result.status_code == 200, result.text
    return result.json()


def tick(client, owner):
    client.portal.call(client.app.state.container.automation.step, owner)


def jobs(client):
    with client.app.state.container.db.session_factory() as session:
        return list(session.scalars(select(Generation).order_by(Generation.queue_seq)))


def complete(client):
    with client.app.state.container.db.session_factory() as session:
        for job in session.scalars(select(Generation)):
            job.status = GenerationStatus.SUCCEEDED
        session.commit()


def test_singleton_blocks_manual_keeps_accepted_jobs_on_stop_and_protocol(app_client):
    user, cookie = provision_user(app_client)
    state = enable(app_client, quantity=3)
    duplicate = command(app_client, expected_revision=state["revision"], enabled=True)
    assert duplicate.status_code == 200
    assert duplicate.json()["revision"] == state["revision"]
    tick(app_client, user["id"])
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 3
    rejected = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=generation_payload(app_client, "manual"),
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "auto_generation_enabled"
    accepted = {job.id for job in jobs(app_client)}
    stopped = command(app_client, expected_revision=state["revision"], enabled=False)
    assert stopped.status_code == 200
    assert {job.id for job in jobs(app_client)} == accepted
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 3
    stale = command(
        app_client, expected_revision=state["revision"], enabled=True, snapshot=state["snapshot"]
    )
    assert stale.status_code == 409
    app_client.headers.pop("X-CIF-Generation-Protocol")
    old_browser = app_client.post(
        "/api/generations",
        headers={"X-CSRF-Token": csrf(app_client)},
        json=generation_payload(app_client, "old loop"),
    )
    assert old_browser.status_code == 409
    assert old_browser.json()["error"]["code"] == "client_reload_required"
    restore_cookie(app_client, cookie)


def test_limit_edit_preserves_total_and_truncates_final_batch(app_client):
    user, _ = provision_user(app_client)
    state = enable(app_client, quantity=3, max_generations=4)
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 3
    changed = command(app_client, "/limit", expected_revision=state["revision"], max_generations=5)
    assert changed.status_code == 200
    assert changed.json()["accepted_count"] == 3
    complete(app_client)
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 5
    ended = app_client.get("/api/auto-generation").json()
    assert not ended["enabled"]
    assert ended["remaining"] == 0
    assert ended["accepted_count"] == 5
    assert ended["status"] == "completed"
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 5
    assert len([job for job in jobs(app_client) if job.status == GenerationStatus.QUEUED]) == 2


def test_restart_without_browser_runs_until_limit(settings_factory, fake_state):
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first)
        enable(first, max_generations=3)
    settings.enable_background_worker = True
    with TestClient(create_app(settings)) as second:
        # No authenticated browser or API session drives the loop.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if len(fake_state.submitted) >= 3:
                break
            time.sleep(0.05)
        assert len(fake_state.submitted) == 3
        restore_cookie(second, cookie)
        state = second.get("/api/auto-generation").json()
        assert not state["enabled"]
        assert state["accepted_count"] == 3
        assert len(jobs(second)) == 3


def test_retired_revision_blocks_future_cycles(app_client):
    user, _ = provision_user(app_client)
    enable(app_client, max_generations=None)
    with app_client.app.state.container.db.session_factory() as session:
        row = session.get(AutoGeneration, user["id"])
        profile = session.get(WorkflowProfile, row.profile_id)
        profile.is_current = False
        session.commit()
    tick(app_client, user["id"])
    assert jobs(app_client) == []
    assert app_client.get("/api/auto-generation").json()["status"] == "blocked"


def test_settings_revision_import_and_owner_isolation(app_client):
    _, cookie = provision_user(app_client)
    current = app_client.get("/api/preferences").json()
    settings = SharedSettings(quantity=4, creative_direction="blue harbor").model_dump()
    saved = app_client.put(
        "/api/preferences",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "expected_revision": current["revision"],
            "settings": settings,
            "import_if_empty": True,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["settings"]["quantity"] == 4
    stale = app_client.put(
        "/api/preferences",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"expected_revision": current["revision"], "settings": settings},
    )
    assert stale.status_code == 409
    settings["quantity"] = 2
    imported_again = app_client.put(
        "/api/preferences",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "expected_revision": current["revision"],
            "settings": settings,
            "import_if_empty": True,
        },
    )
    assert imported_again.json()["settings"]["quantity"] == 4
    restore_cookie(app_client, cookie)
    assert (
        app_client.get("/api/preferences").json()["settings"]["creative_direction"] == "blue harbor"
    )


def test_disable_during_composition_cannot_enqueue(app_client, monkeypatch):
    user, _ = provision_user(app_client)
    started = asyncio.Event()
    release = asyncio.Event()
    from app.schemas import PromptComposeResponse
    from app.services import auto_generation

    async def compose(*args):
        started.set()
        await release.wait()
        return PromptComposeResponse(
            composition_id="discarded", prompt="late prompt", model="fake", template_version="test"
        )

    monkeypatch.setattr(auto_generation, "compose_prompt", compose)
    state = enable(
        app_client,
        assistant={"mode": "refine", "prompt": "lighthouse", "creative_direction": "night"},
    )

    async def race():
        service = app_client.app.state.container.automation
        task = asyncio.create_task(service.step(user["id"]))
        await started.wait()
        await service.change(user["id"], state["revision"], enabled=False)
        release.set()
        await task

    app_client.portal.call(race)
    assert jobs(app_client) == []
    assert not app_client.get("/api/auto-generation").json()["enabled"]


def test_concurrent_enables_and_steps_do_not_duplicate(app_client):
    user, _ = provision_user(app_client)
    snapshot = {"generation": generation_payload(app_client, "concurrent")}
    from app.schemas import AutoGenerationSnapshot

    service = app_client.app.state.container.automation

    async def race():
        results = await asyncio.gather(
            *[
                service.change(
                    user["id"],
                    0,
                    enabled=True,
                    snapshot=AutoGenerationSnapshot.model_validate(snapshot),
                )
                for _ in range(2)
            ],
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in results) == 1
        await asyncio.gather(service.step(user["id"]), service.step(user["id"]))

    app_client.portal.call(race)
    assert len(jobs(app_client)) == 1


def test_prefetch_is_bounded_and_refinement_evolves_without_editing_settings(
    app_client, fake_state
):
    user, _ = provision_user(app_client)
    before = app_client.get("/api/preferences").json()
    state = enable(
        app_client,
        assistant={"mode": "refine", "prompt": "lighthouse", "creative_direction": "night"},
    )
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 1
    first_prompt = jobs(app_client)[0].final_prompt
    assert first_prompt != "lighthouse"
    tick(app_client, user["id"])
    count = len(fake_state.ollama_calls)
    tick(app_client, user["id"])
    assert len(fake_state.ollama_calls) == count
    assert len(jobs(app_client)) == 1
    with app_client.app.state.container.db.session_factory() as session:
        prepared = session.scalars(
            select(AutoGenerationCycle).where(AutoGenerationCycle.state == "ready")
        ).all()
        assert len(prepared) == 1
        from app.models import PromptAssistantRun

        run = session.get(PromptAssistantRun, prepared[0].prompt_run_id)
        assert run.prompt_before == first_prompt
    complete(app_client)
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 2
    assert len(fake_state.ollama_calls) == count
    assert app_client.get("/api/preferences").json() == before
    command(app_client, expected_revision=state["revision"], enabled=False)


def test_transient_composition_retries_but_invalid_configuration_blocks(app_client, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.errors import AppError
    from app.services import auto_generation

    user, _ = provision_user(app_client)
    state = enable(
        app_client,
        assistant={"mode": "refine", "prompt": "lighthouse", "creative_direction": "night"},
    )
    original = auto_generation.compose_prompt

    async def unavailable(*args):
        raise AppError("ollama_generate_timeout", "Temporarily unavailable", status_code=503)

    monkeypatch.setattr(auto_generation, "compose_prompt", unavailable)
    tick(app_client, user["id"])
    waiting = app_client.get("/api/auto-generation").json()
    assert waiting["enabled"] and waiting["status"] == "retrying"
    assert waiting["next_retry_at"]
    assert jobs(app_client) == []
    with app_client.app.state.container.db.session_factory() as session:
        row = session.get(AutoGeneration, user["id"])
        row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    monkeypatch.setattr(auto_generation, "compose_prompt", original)
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 1
    complete(app_client)

    async def invalid(*args):
        raise AppError("invalid_instructions", "Review instructions", status_code=422)

    monkeypatch.setattr(auto_generation, "compose_prompt", invalid)
    tick(app_client, user["id"])
    blocked = app_client.get("/api/auto-generation").json()
    assert blocked["enabled"] and blocked["status"] == "blocked"
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 1
    retried = command(app_client, "/retry", expected_revision=state["revision"])
    assert retried.status_code == 200
    assert retried.json()["status"] == "waiting"


def test_apply_discards_prefetch_and_only_changes_future_jobs(app_client):
    user, _ = provision_user(app_client)
    state = enable(app_client)
    tick(app_client, user["id"])
    tick(app_client, user["id"])
    snapshot = state["snapshot"]
    snapshot["generation"]["parameters"]["prompt"] = "new captured prompt"
    applied = command(app_client, "/apply", expected_revision=state["revision"], snapshot=snapshot)
    assert applied.status_code == 200
    assert jobs(app_client)[0].final_prompt == "lighthouse"
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 1
    complete(app_client)
    tick(app_client, user["id"])
    assert [job.final_prompt for job in jobs(app_client)] == ["lighthouse", "new captured prompt"]


def test_deleted_destination_blocks_and_other_account_cannot_control(app_client):
    from tests.helpers import login_ready_admin

    user, cookie = provision_user(app_client)
    result = app_client.post(
        "/api/collections",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"name": "Automation target"},
    )
    folder = result.json()["id"]
    payload = generation_payload(app_client, "folder target")
    payload["collection_id"] = folder
    state = command(
        app_client, expected_revision=0, enabled=True, snapshot={"generation": payload}
    ).json()
    assert state["enabled"]
    deleted = app_client.delete(
        f"/api/collections/{folder}", headers={"X-CSRF-Token": csrf(app_client)}
    )
    assert deleted.status_code in {202, 204}
    tick(app_client, user["id"])
    assert jobs(app_client) == []
    blocked = app_client.get("/api/auto-generation").json()
    assert blocked["status"] == "blocked"
    for path, update in [("/retry", {}), ("/limit", {"max_generations": 10})]:
        rejected = command(app_client, path, expected_revision=blocked["revision"], **update)
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "collection_deleted"
    login_ready_admin(app_client)
    assert app_client.get("/api/auto-generation").json()["enabled"] is False
    assert app_client.get("/api/preferences").json()["settings_initialized"] is False
    restore_cookie(app_client, cookie)
    assert app_client.get("/api/auto-generation").json()["enabled"] is True


def test_invalid_batch_is_rejected_without_partial_acceptance(app_client):
    _, _ = provision_user(app_client)
    result = command(
        app_client,
        expected_revision=0,
        enabled=True,
        snapshot={
            "generation": generation_payload(app_client, "batch"),
            "variants": [{}, {"width": "invalid"}],
        },
    )
    assert result.status_code == 422
    assert not app_client.get("/api/auto-generation").json()["enabled"]
    assert jobs(app_client) == []


def test_restart_reuses_durable_prefetched_prompt(settings_factory, fake_state):
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as first:
        user, cookie = provision_user(first)
        enable(
            first,
            assistant={"mode": "refine", "prompt": "lighthouse", "creative_direction": "night"},
        )
        tick(first, user["id"])
        tick(first, user["id"])
        assert len(jobs(first)) == 1
        call_count = len(fake_state.ollama_calls)
    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie)
        assert second.get("/api/auto-generation").json()["enabled"]

        async def discovery_ready():
            task = second.app.state.container._startup_discovery_task
            if task:
                await task

        second.portal.call(discovery_ready)
        tick(second, user["id"])
        assert len(jobs(second)) == 1
        complete(second)
        tick(second, user["id"])
        assert len(jobs(second)) == 2, second.get("/api/auto-generation").json()
        assert len(fake_state.ollama_calls) == call_count


def test_saved_upload_survives_deleting_last_generation(app_client):
    from app.models import GenerationUpload
    from tests.fake_services import make_png

    provision_user(app_client)
    uploaded = app_client.post(
        "/api/uploads/images",
        headers={"X-CSRF-Token": csrf(app_client)},
        files={"file": ("reference.png", make_png("saved"), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    asset_id = uploaded.json()["id"]
    saved = app_client.put(
        "/api/preferences",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "expected_revision": 0,
            "settings": {
                "sources": {"saved-workflow": {"values": {"reference": {"asset_id": asset_id}}}}
            },
        },
    )
    assert saved.status_code == 200, saved.text
    generation = create_generation(app_client, "with retained reference")
    with app_client.app.state.container.db.session_factory() as session:
        session.add(
            GenerationUpload(
                generation_id=generation["id"],
                upload_id=asset_id,
                control_id="reference",
                sha256=uploaded.json()["sha256"],
            )
        )
        session.get(Generation, generation["id"]).status = GenerationStatus.SUCCEEDED
        session.commit()
    deleted = app_client.delete(
        f"/api/generations/{generation['id']}", headers={"X-CSRF-Token": csrf(app_client)}
    )
    assert deleted.status_code == 204
    assert app_client.get(f"/api/uploads/{asset_id}/content").status_code == 200


def test_every_automatic_batch_item_preserves_assistant_recall(app_client):
    user, _ = provision_user(app_client)
    enable(
        app_client,
        quantity=2,
        assistant={
            "mode": "refine",
            "prompt": "lighthouse",
            "creative_direction": "night",
            "think": False,
            "instructions": "Describe the scene in one sentence.",
        },
    )
    tick(app_client, user["id"])
    accepted = jobs(app_client)
    assert len(accepted) == 2
    for job in accepted:
        recalled = app_client.get(f"/api/generations/{job.id}/recall").json()
        assistant = recalled["prompt_assistant"]
        assert assistant["mode"] == "refine"
        assert assistant["creative_direction"] == "night"
        assert assistant["thinking_enabled"] is False
        assert assistant["instructions"] == "Describe the scene in one sentence."


def test_cancelled_preparation_releases_its_committed_cycle_claim(app_client, monkeypatch):
    import threading

    user, _ = provision_user(app_client)
    enable(app_client)
    coordinator = app_client.app.state.container.automation
    original = coordinator._prepare_cycle
    claimed = threading.Event()
    release = threading.Event()

    def prepare(owner):
        result = original(owner)
        claimed.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(coordinator, "_prepare_cycle", prepare)

    async def cancel_after_claim():
        task = asyncio.create_task(coordinator.step(user["id"]))
        assert await asyncio.to_thread(claimed.wait, 3)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    app_client.portal.call(cancel_after_claim)
    with app_client.app.state.container.db.session_factory() as session:
        cycles = list(session.scalars(select(AutoGenerationCycle)))
        assert len(cycles) == 1
        assert cycles[0].claim is None
    monkeypatch.setattr(coordinator, "_prepare_cycle", original)
    tick(app_client, user["id"])
    assert len(jobs(app_client)) == 1
