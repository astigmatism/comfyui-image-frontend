from __future__ import annotations

import copy
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from app.demo_reel_models import DemoReelConfig, DemoReelEntry
from app.main import create_app
from app.models import Artifact, Generation, GenerationStatus, User, utcnow
from app.services.demo_reel_activity import SYSTEM_OWNER_ID
from app.services.demo_reels import DemoReelService
from app.services.gallery import GalleryService
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import csrf
from tests.helpers import login_ready_admin, provision_user, restore_cookie
from tests.publication_fixtures import moody_lora_library_files


@pytest.fixture
def reel_client(fake_state, settings_factory):
    fake_state.workflow_files = moody_lora_library_files()
    with TestClient(create_app(settings_factory())) as client:
        _, user_cookie = provision_user(client, username="reel.artist")
        admin_cookie = login_ready_admin(client)
        container = client.app.state.container
        client.portal.call(container.worker.start)
        sources = client.get("/api/workflows").json()
        detail = client.get(f"/api/workflows/{sources[0]['source_key']}").json()
        choices = detail["model_selectors"][0]["choices"]
        preferences = client.get("/api/preferences").json()
        response = client.put(
            "/api/preferences",
            headers={"X-CSRF-Token": csrf(client)},
            json={
                "expected_revision": preferences["revision"],
                "checkpoint_tiers": {
                    "A": [choices[0]["checkpoint_id"]],
                    "B": [choices[1]["checkpoint_id"]],
                },
            },
        )
        assert response.status_code == 200, response.text
        yield client, detail, admin_cookie, user_cookie


def configure(client, detail, *, slots=1):
    settings = client.get("/api/admin/demo-reels").json()
    payload = {
        key: settings[key]
        for key in ["revision", "paused", "rating_user_id", "slots", "recipes", "strengths"]
    }
    payload["slots"] = [
        {
            "id": str(uuid4()),
            "title": f"Slot {index + 1}",
            "prompt": f"portrait {index} of {{subject}}",
            "resolutions": {},
        }
        for index in range(slots)
    ]
    payload["recipes"] = {
        "krea2": {"source_key": detail["source_key"], "parameters": {}, "strength": 1}
    }
    result = client.put(
        "/api/admin/demo-reels", headers={"X-CSRF-Token": csrf(client)}, json=payload
    )
    assert result.status_code == 200, result.text
    control = next(
        control for control in detail["interface"]["inputs"] if control["type"] == "lora_stack"
    )
    identity = control["items"][0]["lora_identity"]
    return result.json(), f"/api/demo-reels/krea2/{identity}"


def tick(client, *, quiet=False):
    service = client.app.state.container.demo_reels
    if quiet:
        service._quiet_reset_requested = False
        with client.app.state.container.db.session_factory() as session:
            session.get(DemoReelConfig, 1).last_activity_at = utcnow() - timedelta(seconds=301)
            session.commit()
    service.step()


def jobs(client):
    with client.app.state.container.db.session_factory() as session:
        return list(
            session.scalars(
                select(Generation)
                .where(Generation.owner_id == SYSTEM_OWNER_ID)
                .order_by(Generation.queue_seq)
            )
        )


def finish(client, generation_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with client.app.state.container.db.session_factory() as session:
            generation = session.get(Generation, generation_id)
            if generation and generation.status == GenerationStatus.SUCCEEDED:
                break
        time.sleep(0.03)
    else:
        raise AssertionError("Demo Reel generation did not succeed")
    # Returning after completion must preserve the already finished result.
    client.portal.call(client.app.state.container.demo_reels.activity)
    tick(client)


def update(client, settings, **changes):
    payload = {
        key: settings[key]
        for key in ["revision", "paused", "rating_user_id", "slots", "recipes", "strengths"]
    }
    payload.update(changes)
    return client.put("/api/admin/demo-reels", headers={"X-CSRF-Token": csrf(client)}, json=payload)


def test_idle_admission_order_prompt_strength_random_seeds_and_isolation(reel_client):
    client, detail, _, _ = reel_client
    settings, path = configure(client, detail, slots=2)
    tick(client)
    assert not jobs(client)
    tick(client, quiet=True)
    first = jobs(client)[0]
    assert first.final_prompt == "portrait 0 of Alpha"
    assert first.effective_controls_json["loras"] == [
        {"id": "a", "strength": 1},
        {"id": "b", "strength": 0},
    ]
    seed = first.resolved_seeds_json
    assert seed
    finish(client, first.id)
    view = client.get(path).json()
    assert view["completed"] == 1 and view["expected"] == 4
    assert [entry["slot_id"] for entry in view["entries"]] == [settings["slots"][0]["id"]] * 2 + [
        settings["slots"][1]["id"]
    ] * 2
    tick(client, quiet=True)
    second = jobs(client)[-1]
    assert second.id != first.id and second.checkpoint_id != first.checkpoint_id
    assert second.final_prompt == first.final_prompt
    assert second.resolved_seeds_json != seed
    assert client.get("/api/generations").json()["items"] == []
    assert client.get("/api/collections").json() == []
    assert all(user["id"] != SYSTEM_OWNER_ID for user in client.get("/api/admin/users").json())


def test_shared_permissions_favorites_copy_idempotency_and_independent_files(reel_client):
    client, detail, admin_cookie, user_cookie = reel_client
    _, path = configure(client, detail)
    tick(client, quiet=True)
    first = jobs(client)[0]
    finish(client, first.id)
    restore_cookie(client, user_cookie)
    view = client.get(path).json()
    image = next(entry["generation"] for entry in view["entries"] if entry["generation"])
    assert client.get(image["display_artifact"]["content_url"]).status_code == 200
    assert client.get(f"/api/generations/{first.id}").status_code == 404
    assert (
        client.get(f"/api/artifacts/{image['display_artifact']['id']}/content").status_code == 404
    )
    assert (
        client.post(
            path + "/actions",
            headers={"X-CSRF-Token": csrf(client)},
            json={"action": "delete", "generation_ids": [first.id]},
        ).status_code
        == 403
    )
    assert client.get(path + f"/generations/{first.id}/recall").json()["available"]
    assert (
        client.post(
            path + "/actions", json={"action": "favorite", "generation_ids": [first.id]}
        ).status_code
        == 403
    )
    headers = {"X-CSRF-Token": csrf(client)}
    assert (
        client.post(
            path + "/actions",
            headers=headers,
            json={"action": "favorite", "generation_ids": [first.id]},
        ).status_code
        == 200
    )
    assert client.get(path).json()["entries"][0]["generation"]["is_favorite"]
    request = {"action": "copy", "generation_ids": [first.id], "key": str(uuid4())}
    copied = client.post(path + "/actions", headers=headers, json=request)
    assert copied.status_code == 200, copied.text
    assert client.post(path + "/actions", headers=headers, json=request).json() == copied.json()
    clone_id = copied.json()["generation_ids"][0]
    cloned = client.get(f"/api/generations/{clone_id}").json()
    clone_url = cloned["artifacts"][0]["content_url"]
    restore_cookie(client, admin_cookie)
    assert not client.get(path).json()["entries"][0]["generation"]["is_favorite"]
    assert (
        client.post(
            path + "/actions",
            headers={"X-CSRF-Token": csrf(client)},
            json={"action": "delete", "generation_ids": [first.id]},
        ).status_code
        == 200
    )
    tick(client)
    restore_cookie(client, user_cookie)
    assert client.get(clone_url).status_code == 200
    with client.app.state.container.db.session_factory() as session:
        clone = session.get(Generation, clone_id)
        assert clone.owner_id == client.get("/api/auth/session").json()["user"]["id"]
        assert all(
            artifact.owner_id == clone.owner_id
            for artifact in session.scalars(
                select(Artifact).where(Artifact.generation_id == clone_id)
            )
        )
        assert session.get(Generation, first.id) is None


def test_activity_cancels_and_discards_late_results_without_penalty(reel_client, fake_state):
    client, detail, _, user_cookie = reel_client
    fake_state.default_stage_delay = 0.5
    _, path = configure(client, detail)
    tick(client, quiet=True)
    first = jobs(client)[0]
    restore_cookie(client, user_cookie)
    assert client.post("/api/activity", headers={"X-CSRF-Token": csrf(client)}).status_code == 204
    with client.app.state.container.db.session_factory() as session:
        entry = session.scalar(select(DemoReelEntry).where(DemoReelEntry.generation_id == first.id))
        assert entry.state == "discarded" and entry.failures == 0
    for _ in range(80):
        tick(client)
        if not jobs(client):
            break
        time.sleep(0.03)
    assert not jobs(client)
    assert client.get(path).json()["completed"] == 0
    tick(client)
    assert not jobs(client)
    tick(client, quiet=True)
    replacement = jobs(client)[0]
    assert replacement.resolved_seeds_json != first.resolved_seeds_json


def test_invalidation_reordering_downgrade_and_upgrade(reel_client):
    client, detail, _, _ = reel_client
    settings, path = configure(client, detail, slots=2)
    tick(client, quiet=True)
    first = jobs(client)[0]
    finish(client, first.id)
    slots = copy.deepcopy(settings["slots"])
    slots[0]["title"] = "Renamed only"
    reordered = update(client, settings, slots=slots[::-1])
    assert reordered.status_code == 200
    assert client.get(path).json()["completed"] == 1
    slots[0]["prompt"] = "a new portrait of {subject}"
    changed = update(client, reordered.json(), slots=slots)
    assert changed.status_code == 200
    assert client.get(path).json()["completed"] == 0
    tick(client)
    assert not jobs(client)
    preferences = client.get("/api/preferences").json()
    tiers = preferences["checkpoint_tiers"]
    tiers["C"] = tiers.pop("A")
    assert (
        client.put(
            "/api/preferences",
            headers={"X-CSRF-Token": csrf(client)},
            json={"expected_revision": preferences["revision"], "checkpoint_tiers": tiers},
        ).status_code
        == 200
    )
    assert client.get(path).json()["expected"] == 2
    preferences = client.get("/api/preferences").json()
    tiers["B"] += tiers.pop("C")
    assert (
        client.put(
            "/api/preferences",
            headers={"X-CSRF-Token": csrf(client)},
            json={"expected_revision": preferences["revision"], "checkpoint_tiers": tiers},
        ).status_code
        == 200
    )
    assert client.get(path).json()["expected"] == 4


def test_revisions_system_account_protection_and_cross_reel_denial(reel_client):
    client, detail, _, user_cookie = reel_client
    settings, _ = configure(client, detail)
    stale = update(client, {**settings, "revision": settings["revision"] - 1})
    assert stale.status_code == 409
    assert (
        client.post(
            f"/api/admin/users/{SYSTEM_OWNER_ID}/reset-password",
            headers={"X-CSRF-Token": csrf(client)},
            json={"temporary_password": "ResetPassword123!"},
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/admin/users/{SYSTEM_OWNER_ID}", headers={"X-CSRF-Token": csrf(client)}
        ).status_code
        == 403
    )
    tick(client, quiet=True)
    first = jobs(client)[0]
    finish(client, first.id)
    loras = next(
        control for control in detail["interface"]["inputs"] if control["type"] == "lora_stack"
    )["items"]
    wrong_path = f"/api/demo-reels/krea2/{loras[1]['lora_identity']}"
    assert client.get(wrong_path + f"/generations/{first.id}").status_code == 404
    restore_cookie(client, user_cookie)
    assert client.get("/api/admin/demo-reels").status_code == 403
    assert (
        client.put(
            "/api/admin/demo-reels",
            headers={"X-CSRF-Token": csrf(client)},
            json={
                key: settings[key]
                for key in ["revision", "paused", "rating_user_id", "slots", "recipes", "strengths"]
            },
        ).status_code
        == 403
    )
    with client.app.state.container.db.session_factory() as session:
        system = session.get(User, SYSTEM_OWNER_ID)
        assert system.is_system


def test_concurrent_admission_and_durable_restart_discard_queued_attempts(reel_client):
    client, detail, _, _ = reel_client
    settings, path = configure(client, detail, slots=2)
    container = client.app.state.container
    client.portal.call(container.worker.stop)
    with container.db.session_factory() as session:
        session.get(DemoReelConfig, 1).last_activity_at = utcnow() - timedelta(seconds=301)
        session.commit()
    container.demo_reels._quiet_reset_requested = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: container.demo_reels.step(), range(2)))
    assert len(jobs(client)) == 1
    first = jobs(client)[0]
    assert first.status == GenerationStatus.QUEUED
    restarted = DemoReelService(container)
    client.portal.call(restarted.recover)
    restarted.step()
    assert not jobs(client)
    assert client.get(path).json()["completed"] == 0
    assert client.get("/api/admin/demo-reels").json()["slots"] == settings["slots"]
    with container.db.session_factory() as session:
        assert all(row.failures == 0 for row in session.scalars(select(DemoReelEntry)))


def test_foreground_work_requires_five_new_quiet_minutes_and_pause_is_revision_checked(reel_client):
    client, detail, _, _ = reel_client
    settings, path = configure(client, detail)
    container = client.app.state.container
    container.demo_reels.foreground_requests = 1
    tick(client, quiet=True)
    assert not jobs(client)
    container.demo_reels.foreground_requests = 0
    tick(client)
    assert not jobs(client)
    headers = {"X-CSRF-Token": csrf(client)}
    paused = client.post(
        path + "/actions",
        headers=headers,
        json={"action": "pause", "revision": settings["revision"]},
    )
    assert paused.status_code == 200
    assert (
        client.post(
            path + "/actions",
            headers=headers,
            json={"action": "resume", "revision": settings["revision"]},
        ).status_code
        == 409
    )
    tick(client, quiet=True)
    assert not jobs(client)
    view = client.get(path).json()
    assert view["paused"] and view["status"] == "paused"
    assert (
        client.post(
            path + "/actions",
            headers=headers,
            json={"action": "resume", "revision": view["revision"]},
        ).status_code
        == 200
    )
    tick(client)
    assert not jobs(client)
    tick(client, quiet=True)
    assert len(jobs(client)) == 1


def test_three_failures_have_persisted_backoff_then_allow_other_work_and_manual_retry(reel_client):
    client, detail, _, _ = reel_client
    _, path = configure(client, detail)
    container = client.app.state.container
    client.portal.call(container.worker.stop)
    entry_id = None
    for failures in range(1, 4):
        tick(client, quiet=True)
        job = jobs(client)[0]
        with container.db.session_factory() as session:
            entry = session.scalar(
                select(DemoReelEntry).where(DemoReelEntry.generation_id == job.id)
            )
            entry_id = entry.id
            session.get(Generation, job.id).status = GenerationStatus.FAILED_WITHOUT_ARTIFACTS
            session.commit()
        client.portal.call(container.demo_reels.activity)
        tick(client)
        with container.db.session_factory() as session:
            entry = session.get(DemoReelEntry, entry_id)
            assert entry.failures == failures
            assert entry.next_retry_at is not None
            entry.next_retry_at = utcnow() - timedelta(seconds=1)
            session.commit()
    with container.db.session_factory() as session:
        assert session.get(DemoReelEntry, entry_id).state == "failed"
    tick(client, quiet=True)
    assert jobs(client)[0].checkpoint_id != job.checkpoint_id
    assert (
        client.post(
            path + "/actions", headers={"X-CSRF-Token": csrf(client)}, json={"action": "retry"}
        ).status_code
        == 200
    )
    with container.db.session_factory() as session:
        assert session.get(DemoReelEntry, entry_id).failures == 0


def test_compatible_catalog_metadata_preserves_images_but_render_changes_require_recipe_review(
    reel_client,
):
    client, detail, _, _ = reel_client
    settings, path = configure(client, detail)
    tick(client, quiet=True)
    first = jobs(client)[0]
    finish(client, first.id)
    from app.models import WorkflowProfile

    with client.app.state.container.db.session_factory() as session:
        profile = session.get(WorkflowProfile, first.workflow_profile_id)
        graph = copy.deepcopy(profile.source_api_json)
        graph["99"]["_meta"] = {"title": "A harmless new caption"}
        graph["99"]["inputs"].update(
            label="Updated LoRA caption", description="New guidance", group="Models", order=42
        )
        graph["201"]["inputs"]["description"] = "A revised final-image description"
        profile.source_api_json = graph
        session.commit()
    assert client.get(path).json()["completed"] == 1
    with client.app.state.container.db.session_factory() as session:
        profile = session.get(WorkflowProfile, first.workflow_profile_id)
        graph = copy.deepcopy(profile.source_api_json)
        graph["99"]["inputs"]["changed_render_behavior"] = True
        profile.source_api_json = graph
        session.commit()
    view = client.get(path).json()
    assert view["completed"] == 0
    assert any("Rendering workflow changed" in (entry["error"] or "") for entry in view["entries"])
    # An explicit recipe save accepts the changed rendering graph.
    saved = update(client, settings)
    assert saved.status_code == 200, saved.text
    assert all(entry["state"] == "missing" for entry in client.get(path).json()["entries"])


def test_cross_owner_move_rolls_back_files_and_source_on_copy_failure(reel_client, monkeypatch):
    client, detail, _, _ = reel_client
    _, path = configure(client, detail)
    tick(client, quiet=True)
    first = jobs(client)[0]
    finish(client, first.id)
    original = GalleryService._copy_generation

    def fail_after_copy(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("simulated destination failure")

    monkeypatch.setattr(GalleryService, "_copy_generation", fail_after_copy)
    with pytest.raises(OSError, match="destination failure"):
        client.post(
            path + "/actions",
            headers={"X-CSRF-Token": csrf(client)},
            json={"action": "move", "generation_ids": [first.id], "key": str(uuid4())},
        )
    assert client.get(path).json()["completed"] == 1
    assert client.get("/api/generations").json()["items"] == []
    with client.app.state.container.db.session_factory() as session:
        assert len(list(session.scalars(select(Generation)))) == 1


def test_strength_and_resolution_invalidation_and_moved_image_survives(reel_client):
    client, detail, _, _ = reel_client
    settings, path = configure(client, detail)
    tick(client, quiet=True)
    first = jobs(client)[0]
    finish(client, first.id)
    with client.app.state.container.db.session_factory() as session:
        signatures = {entry.id: entry.signature for entry in session.scalars(select(DemoReelEntry))}
    identity = path.rsplit("/", 1)[1]
    saved = update(client, settings, strengths={"krea2": {identity: 0.5}})
    assert saved.status_code == 200
    assert client.get(path).json()["completed"] == 0
    with client.app.state.container.db.session_factory() as session:
        changed = [
            entry
            for entry in session.scalars(select(DemoReelEntry))
            if signatures[entry.id] != entry.signature
        ]
        assert len(changed) == 2  # Only the target LoRA's A and B combinations.
    tick(client, quiet=True)
    replacement = jobs(client)[-1]
    assert replacement.effective_controls_json["loras"][0]["strength"] == 0.5
    finish(client, replacement.id)
    moved = client.post(
        path + "/actions",
        headers={"X-CSRF-Token": csrf(client)},
        json={"action": "move", "generation_ids": [replacement.id], "key": str(uuid4())},
    )
    assert moved.status_code == 200, moved.text
    assert client.get(path).json()["completed"] == 0
    tick(client)
    assert client.get(f"/api/generations/{moved.json()['generation_ids'][0]}").status_code == 200
    settings = saved.json()
    slots = copy.deepcopy(settings["slots"])
    slots[0]["resolutions"] = {"krea2": {"width": 512, "height": 512}}
    resized = update(client, settings, slots=slots)
    assert resized.status_code == 200, resized.text
    slots[0]["resolutions"] = {"krea2": {"seed": 1}}
    assert update(client, resized.json(), slots=slots).status_code == 422


def test_shared_download_and_prompt_rerun_create_personal_work_without_changing_reel(reel_client):
    client, detail, _, user_cookie = reel_client
    _, path = configure(client, detail)
    tick(client, quiet=True)
    first = jobs(client)[0]
    finish(client, first.id)
    restore_cookie(client, user_cookie)
    headers = {
        "X-CSRF-Token": csrf(client),
        "X-CIF-Generation-Protocol": "3",
        "Idempotency-Key": str(uuid4()),
    }
    selection = {"generation_ids": [first.id]}
    archive = client.post(path + "/download", headers=headers, json=selection)
    assert archive.status_code == 200, archive.text
    assert archive.content.startswith(b"PK")
    preview = client.post(path + "/prompt-rerun/preview", headers=headers, json=selection)
    assert preview.status_code == 200 and preview.json()["prompt_count"] == 1
    parameters = {
        control["id"]: control.get("default")
        for control in detail["interface"]["inputs"]
        if control["type"] != "seed" and control.get("semantic_role") != "positive_prompt"
    }
    payload = {
        **selection,
        "folder_name": "Personal reel re-run",
        "source_key": detail["source_key"],
        "revision": detail["revision"],
        "parameters": parameters,
        "demo_reel": {"library_key": "krea2", "lora_identity": path.rsplit("/", 1)[1]},
    }
    rerun = client.post("/api/gallery/prompt-rerun", headers=headers, json=payload)
    assert rerun.status_code == 201, rerun.text
    assert len(rerun.json()["items"]) == 1
    assert client.get(path).json()["completed"] == 1
    item = rerun.json()["items"][0]["generation"]
    assert client.get(f"/api/generations/{item['id']}").status_code == 200


def reel_stack(detail) -> list[dict]:
    control = next(item for item in detail["interface"]["inputs"] if item["type"] == "lora_stack")
    return control["items"]


def switch(client, identity, *, enabled, cookie=None):
    """An administrator takes one LoRA out of the Demo Reel, or puts it back."""

    if cookie is not None:
        restore_cookie(client, cookie)
    return client.put(
        f"/api/admin/lora-library/krea2/demo-reel/{identity}",
        headers={"X-CSRF-Token": csrf(client)},
        json={"enabled": enabled},
    )


def test_disabling_a_lora_erases_its_images_and_leaves_the_reel_to_the_others(reel_client):
    client, detail, _, _ = reel_client
    items = reel_stack(detail)
    _, path = configure(client, detail, slots=2)
    identity = path.rsplit("/", 1)[1]
    tick(client, quiet=True)
    produced = jobs(client)[0]
    finish(client, produced.id)
    container = client.app.state.container
    with container.db.session_factory() as session:
        files = [
            stored
            for artifact in session.scalars(
                select(Artifact).where(Artifact.generation_id == produced.id)
            )
            for stored in (artifact.storage_path, artifact.thumbnail_path)
            if stored
        ]
    assert files and client.get(path).json()["completed"] == 1

    answer = switch(client, identity, enabled=False)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "library_key": "krea2",
        "lora_identity": identity,
        "demo_reel_enabled": False,
        "images_removed": 1,
        "changed": True,
    }

    # Erased, not hidden: no card, no work, and no file on disk.
    view = client.get(path).json()
    assert view["status"] == "off" and view["demo_reel_enabled"] is False
    assert (view["entries"], view["expected"], view["completed"]) == ([], 0, 0)
    with container.db.session_factory() as session:
        assert session.get(Generation, produced.id) is None
    assert not any((container.assets.data_dir / stored).is_file() for stored in files)

    # The LoRA gallery is told, so its menu can offer to put the LoRA back.
    excluded = {
        item["id"]: item["demo_reel_enabled"]
        for item in client.get("/api/admin/lora-library").json()["libraries"][0]["items"]
    }
    assert excluded[items[0]["id"]] is False and excluded[items[1]["id"]] is True
    assert client.get("/api/admin/demo-reels").json()["disabled_reels"] == 1

    # Generation continues for everybody else and never enables the disabled LoRA again.
    for _ in range(3):
        tick(client, quiet=True)
        finish(client, jobs(client)[-1].id)
    assert client.get(path).json()["entries"] == []
    assert jobs(client)
    assert all(
        any(
            entry["id"] == items[0]["id"] and entry["strength"] == 0
            for entry in job.effective_controls_json["loras"]
        )
        for job in jobs(client)
    )
    settings = client.get("/api/admin/demo-reels").json()
    assert settings["progress"]["completed"] > 0 and settings["disabled_reels"] == 1


def test_rejoining_the_demo_reel_starts_the_lora_over(reel_client):
    client, detail, _, _ = reel_client
    _, path = configure(client, detail)
    identity = path.rsplit("/", 1)[1]
    tick(client, quiet=True)
    finish(client, jobs(client)[0].id)
    assert client.get(path).json()["completed"] == 1

    assert switch(client, identity, enabled=False).status_code == 200
    assert switch(client, identity, enabled=True).json()["demo_reel_enabled"] is True

    # Nothing is restored: the reel has the same work to do it had before.
    view = client.get(path).json()
    assert view["demo_reel_enabled"] is True and view["completed"] == 0 and view["expected"] == 2
    assert {entry["state"] for entry in view["entries"]} == {"missing"}
    tick(client, quiet=True)
    finish(client, jobs(client)[-1].id)
    assert client.get(path).json()["completed"] == 1


def test_only_an_administrator_may_switch_a_loras_demo_reel(reel_client):
    client, detail, admin_cookie, user_cookie = reel_client
    _, path = configure(client, detail)
    identity = path.rsplit("/", 1)[1]
    url = f"/api/admin/lora-library/krea2/demo-reel/{identity}"

    assert switch(client, identity, enabled=False, cookie=user_cookie).status_code == 403
    restore_cookie(client, admin_cookie)
    assert client.put(url, json={"enabled": False}).status_code == 403
    assert (
        client.put(
            "/api/admin/lora-library/krea2/demo-reel/lr1_missing",
            headers={"X-CSRF-Token": csrf(client)},
            json={"enabled": False},
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/admin/lora-library/notebook/demo-reel/{identity}",
            headers={"X-CSRF-Token": csrf(client)},
            json={"enabled": False},
        ).status_code
        == 404
    )

    assert switch(client, identity, enabled=False).json()["changed"] is True
    again = switch(client, identity, enabled=False)
    assert again.status_code == 200 and again.json()["changed"] is False
    assert client.get(path).json()["status"] == "off"
