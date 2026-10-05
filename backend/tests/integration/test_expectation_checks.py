from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from app.errors import AppError
from app.main import create_app
from app.models import (
    Artifact,
    ArtifactState,
    AutoGeneration,
    ExpectationCheck,
    ExpectationCheckAttempt,
    Generation,
    GenerationStatus,
    PromptAssistantRun,
    ServiceHealth,
)
from app.services.ollama import ComposeResult
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.fake_services import make_png
from tests.helpers import (
    generation_payload,
    login_ready_admin,
    provision_user,
    restore_cookie,
)

EXPECTATIONS = ["The keeper wears a red raincoat", "A lit lighthouse beam is visible"]
CHECKS = "/api/prompt-assistant/checks"


def seed_vision(client: TestClient, *, vision: bool = True) -> None:
    container = client.app.state.container
    with container.db.session_factory() as session:
        health = session.get(ServiceHealth, "ollama")
        if health is None:
            health = ServiceHealth(service="ollama")
            session.add(health)
        health.available = True
        health.message = None
        health.checked_at = datetime.now(UTC)
        health.capabilities_json = {"vision": vision, "capabilities": ["completion"]}
        session.commit()


def check_body(
    client: TestClient,
    *,
    purpose: str = "apply",
    prompt: str = "a lighthouse keeper on a cliff",
    direction: str = "warm film still",
    mode: str = "refine",
    count: int = 1,
    threshold: int = 80,
    max_attempts: int = 3,
    expectations: list[str] | None = None,
) -> dict[str, Any]:
    item = generation_payload(client, prompt)
    item["prompt_assistant"] = {
        "mode": mode,
        "creative_direction": direction,
        "instructions": None,
        "thinking_enabled": False,
    }
    return {
        "purpose": purpose,
        "assistant": {
            "mode": mode,
            "prompt": prompt,
            "creative_direction": direction,
            "think": False,
        },
        "expectations": expectations or list(EXPECTATIONS),
        "threshold": threshold,
        "max_attempts": max_attempts,
        "items": [dict(item) for _ in range(count)],
    }


def start(client: TestClient, body: dict[str, Any], key: str | None = None):
    return client.post(
        CHECKS,
        headers={"X-CSRF-Token": csrf(client), "Idempotency-Key": key or str(uuid4())},
        json=body,
    )


def started(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = start(client, body)
    assert response.status_code == 202, response.text
    return response.json()


def get(client: TestClient, check: dict[str, Any]) -> dict[str, Any]:
    response = client.get(f"{CHECKS}/{check['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def step(client: TestClient, check: dict[str, Any]) -> dict[str, Any]:
    client.portal.call(client.app.state.container.expectation_checks.advance, check["id"])
    return get(client, check)


def attempt_row(client: TestClient, check: dict[str, Any]) -> ExpectationCheckAttempt:
    with client.app.state.container.db.session_factory() as session:
        rows = session.query(ExpectationCheckAttempt).filter_by(check_id=check["id"]).all()
        return max(rows, key=lambda row: row.number)


def finish_probe(
    client: TestClient,
    check: dict[str, Any],
    status: GenerationStatus = GenerationStatus.SUCCEEDED,
    content: bytes | None = None,
) -> str:
    """Complete the current probe the way the worker would, with a real stored image."""

    container = client.app.state.container
    generation_id = attempt_row(client, check).generation_id
    assert generation_id
    stored = container.assets.store_artifact(
        content or make_png("probe"), generation_id=generation_id
    )
    with container.db.session_factory() as session:
        generation = session.get(Generation, generation_id)
        assert generation is not None
        artifact = Artifact(
            generation_id=generation.id,
            owner_id=generation.owner_id,
            output_id="final",
            role="final",
            kind="image",
            state=ArtifactState.FINAL,
            storage_path=stored.relative_path,
            thumbnail_path=stored.thumbnail_path,
            mime_type=stored.mime_type,
            byte_size=stored.byte_size,
            width=stored.width,
            height=stored.height,
            sha256=stored.sha256,
            canonical=True,
            best_available=True,
        )
        session.add(artifact)
        session.flush()
        generation.status = status
        generation.canonical_artifact_id = artifact.id
        generation.best_available_artifact_id = artifact.id
        generation.final_artifact_count = 1
        generation.artifact_count = 1
        if status != GenerationStatus.SUCCEEDED:
            generation.error_message = "ComfyUI rejected the graph."
        session.commit()
    return generation_id


def rows(client: TestClient, model: Any) -> list[Any]:
    with client.app.state.container.db.session_factory() as session:
        return list(session.query(model).all())


def run_attempt(client, check, *, compose=True):
    if compose:
        assert step(client, check)["attempts"][-1]["status"] == "ready"
    assert step(client, check)["attempts"][-1]["status"] == "generating"
    finish_probe(client, check)
    return step(client, check)


def test_apply_check_passes_on_first_attempt_with_provenance(app_client, fake_state):
    provision_user(app_client)
    seed_vision(app_client)
    check = started(app_client, check_body(app_client))
    assert check["status"] == "composing"
    assert check["attempts"] == [
        {
            "number": 1,
            "status": "composing",
            "prompt": None,
            "composition_id": None,
            "generation": None,
            "score": None,
            "passed": None,
            "results": [],
            "summary": None,
            "error": None,
        }
    ]

    composed = step(app_client, check)
    prompt = composed["attempts"][0]["prompt"]
    # The first composition receives the user's direction plus every expectation.
    content = fake_state.ollama_calls[-1]["messages"][0]["content"]
    assert "warm film still\n\nThe image must also satisfy these expectations:" in content
    assert "- The keeper wears a red raincoat" in content

    probing = step(app_client, check)
    assert probing["status"] == "generating"
    generation_id = probing["attempts"][0]["generation"]["id"]
    with app_client.app.state.container.db.session_factory() as session:
        generation = session.get(Generation, generation_id)
        assert generation.final_prompt == prompt
        assert generation.auto_cycle_id is None
        snapshot = generation.prompt_assistant_json
        assert snapshot["creative_direction"] == "warm film still"
        assert snapshot["expectations"]["items"] == EXPECTATIONS
        assert snapshot["expectation_check"] == {"id": check["id"], "attempt": 1}
        run = session.get(PromptAssistantRun, composed["attempts"][0]["composition_id"])
        assert run.generation_id == generation_id

    finish_probe(app_client, check)
    done = step(app_client, check)
    assert done["status"] == "passed"
    assert done["final_prompt"] == prompt
    assert done["best_attempt"] == 1
    assert done["queued"] == {"generation_ids": [], "errors": []}
    attempt = done["attempts"][0]
    assert attempt["passed"] is True and attempt["score"] == 100
    assert [item["expectation"] for item in attempt["results"]] == EXPECTATIONS
    assert attempt["generation"]["thumbnail_url"].startswith("/api/artifacts/")
    vision = fake_state.ollama_vision_calls[-1]
    assert vision["messages"][0]["images"][0].startswith("data:image/jpeg;base64,")
    assert prompt not in vision["messages"][0]["content"]
    assert vision["think"] is False
    assert len(rows(app_client, Generation)) == 1

    recall = app_client.get(f"/api/generations/{generation_id}/recall").json()
    assert recall["prompt_assistant"]["expectations"]["items"] == EXPECTATIONS
    assert recall["prompt_assistant"]["creative_direction"] == "warm film still"
    latest = app_client.get(f"{CHECKS}/latest").json()
    assert latest["check"]["id"] == check["id"]


def test_generate_revises_until_pass_and_queues_the_rest_of_the_batch(app_client, fake_state):
    provision_user(app_client)
    seed_vision(app_client)
    fake_state.ollama_vision_scores.extend([[40, 95], [90, 85]])
    check = started(app_client, check_body(app_client, purpose="generate", count=3))
    assert check["planned_count"] == 3

    first = run_attempt(app_client, check)
    assert first["status"] == "composing"
    assert [item["status"] for item in first["attempts"]] == ["not_met", "composing"]
    assert first["attempts"][0]["score"] == 40

    revised = step(app_client, check)
    revision_call = fake_state.ollama_calls[-1]["messages"][0]["content"]
    assert f"Current prompt:\n{first['attempts'][0]['prompt']}" in revision_call
    assert "Unmet expectations:\n- The keeper wears a red raincoat (score 40/100)" in (
        revision_call
    )
    assert revised["attempts"][1]["prompt"] != first["attempts"][0]["prompt"]

    assert step(app_client, check)["attempts"][1]["status"] == "generating"
    finish_probe(app_client, check)
    done = step(app_client, check)
    assert done["status"] == "passed"
    assert done["best_attempt"] == 2
    final_prompt = done["attempts"][1]["prompt"]
    assert done["final_prompt"] == final_prompt
    queued = done["queued"]["generation_ids"]
    assert len(queued) == 2 and done["queued"]["errors"] == []
    with app_client.app.state.container.db.session_factory() as session:
        for generation_id in queued:
            generation = session.get(Generation, generation_id)
            assert generation.final_prompt == final_prompt
            assert generation.prompt_assistant_json["expectation_check"]["attempt"] == 2
    # Two probes plus the two remaining batch items; the passing probe is item one.
    assert len(rows(app_client, Generation)) == 4


def test_exhausted_check_reports_the_best_attempt_and_queues_nothing(app_client, fake_state):
    provision_user(app_client)
    seed_vision(app_client)
    fake_state.ollama_vision_scores.extend([[70, 10], [60, 50]])
    check = started(app_client, check_body(app_client, purpose="generate", count=2, max_attempts=2))
    run_attempt(app_client, check)
    done = run_attempt(app_client, check)
    assert done["status"] == "not_met"
    assert [item["score"] for item in done["attempts"]] == [10, 50]
    assert done["best_attempt"] == 2
    assert done["final_prompt"] is None
    assert done["queued"]["generation_ids"] == []
    assert done["completed_at"]
    assert len(rows(app_client, Generation)) == 2


def test_blank_refine_direction_verifies_the_current_prompt_as_written(app_client, fake_state):
    provision_user(app_client)
    seed_vision(app_client)
    check = started(app_client, check_body(app_client, direction=""))
    assert check["attempts"][0]["status"] == "ready"
    assert check["attempts"][0]["prompt"] == "a lighthouse keeper on a cliff"
    done = run_attempt(app_client, check, compose=False)
    assert done["status"] == "passed"
    assert fake_state.ollama_calls == fake_state.ollama_vision_calls


def test_create_mode_with_blank_direction_uses_expectations_as_direction(app_client, fake_state):
    provision_user(app_client)
    seed_vision(app_client)
    check = started(app_client, check_body(app_client, mode="create", direction="", prompt=""))
    composed = step(app_client, check)
    assert composed["attempts"][0]["status"] == "ready"
    content = fake_state.ollama_calls[-1]["messages"][0]["content"]
    assert content.endswith(
        "The image must also satisfy these expectations:\n- " + "\n- ".join(EXPECTATIONS)
    )


def test_stop_during_composition_discards_the_late_result(app_client, monkeypatch):
    provision_user(app_client)
    seed_vision(app_client)
    check = started(app_client, check_body(app_client))
    container = app_client.app.state.container
    owner = rows(app_client, ExpectationCheck)[0].owner_id

    async def compose(**kwargs):
        await container.expectation_checks.stop_check(owner, check["id"])
        return ComposeResult(prompt="late prompt", model="test", raw_response={}, duration_ms=1)

    monkeypatch.setattr(container.ollama, "compose", compose)
    done = step(app_client, check)
    assert done["status"] == "stopped"
    assert done["attempts"][0]["status"] == "stopped"
    assert done["attempts"][0]["prompt"] is None
    assert rows(app_client, Generation) == []
    # Stopping again is idempotent.
    again = app_client.post(
        f"{CHECKS}/{check['id']}/stop", headers={"X-CSRF-Token": csrf(app_client)}
    )
    assert again.status_code == 200 and again.json()["status"] == "stopped"


def test_failed_probe_fails_the_check_and_deleted_probe_stops_it(app_client):
    provision_user(app_client)
    seed_vision(app_client)
    check = started(app_client, check_body(app_client))
    step(app_client, check)
    step(app_client, check)
    finish_probe(app_client, check, GenerationStatus.FAILED_WITH_ARTIFACTS)
    failed = step(app_client, check)
    assert failed["status"] == "failed"
    assert failed["error"] == {
        "code": "expectation_check_image_failed",
        "message": "ComfyUI rejected the graph.",
    }
    assert failed["attempts"][0]["status"] == "failed"

    cancelled = started(app_client, check_body(app_client))
    step(app_client, cancelled)
    step(app_client, cancelled)
    finish_probe(app_client, cancelled, GenerationStatus.CANCELLED_WITH_ARTIFACTS)
    stopped_by_cancel = step(app_client, cancelled)
    assert stopped_by_cancel["status"] == "stopped"
    assert stopped_by_cancel["error"]["message"] == "The attempt image was cancelled."

    second = started(app_client, check_body(app_client))
    step(app_client, second)
    probing = step(app_client, second)
    response = app_client.delete(
        f"/api/generations/{probing['attempts'][0]['generation']['id']}",
        headers={"X-CSRF-Token": csrf(app_client)},
    )
    assert response.status_code == 204, response.text
    stopped = step(app_client, second)
    assert stopped["status"] == "stopped"
    assert stopped["error"]["message"] == "The attempt image was cancelled or deleted."


def test_vision_failures_end_the_check_with_a_visible_reason(app_client, fake_state):
    provision_user(app_client)
    seed_vision(app_client)
    check = started(app_client, check_body(app_client))
    step(app_client, check)
    step(app_client, check)
    finish_probe(app_client, check)
    # The router refuses image input when its profile lacks vision.
    fake_state.ollama_capabilities = ["completion"]
    done = step(app_client, check)
    assert done["status"] == "failed"
    assert done["error"]["code"] == "vision_unavailable"


def test_transient_failures_retry_with_backoff(app_client, monkeypatch):
    provision_user(app_client)
    seed_vision(app_client)
    check = started(app_client, check_body(app_client))
    container = app_client.app.state.container

    async def compose(**kwargs):
        raise AppError("ollama_generate_timeout", "Timed out.", status_code=504)

    monkeypatch.setattr(container.ollama, "compose", compose)
    retrying = step(app_client, check)
    assert retrying["status"] == "composing"
    row = rows(app_client, ExpectationCheck)[0]
    assert row.failures == 1 and row.next_retry_at is not None
    assert container.expectation_checks._pending() == []


def test_unchanged_revision_ends_with_the_best_attempt(app_client, fake_state, monkeypatch):
    provision_user(app_client)
    seed_vision(app_client)
    fake_state.ollama_vision_scores.append([30, 90])
    check = started(app_client, check_body(app_client))
    run_attempt(app_client, check)
    container = app_client.app.state.container

    async def compose(**kwargs):
        raise AppError(
            "prompt_refinement_unchanged", "Prompt Assistant repeated the prompt.", status_code=422
        )

    monkeypatch.setattr(container.ollama, "compose", compose)
    done = step(app_client, check)
    assert done["status"] == "not_met"
    assert [item["number"] for item in done["attempts"]] == [1]
    assert done["best_attempt"] == 1
    assert done["error"]["code"] == "prompt_refinement_unchanged"


def test_guards_receipts_and_owner_isolation(app_client):
    user, cookie = provision_user(app_client)
    seed_vision(app_client)
    body = check_body(app_client)
    key = str(uuid4())
    first = start(app_client, body, key)
    assert first.status_code == 202, first.text
    replay = start(app_client, body, key)
    assert replay.status_code == 202 and replay.json()["id"] == first.json()["id"]
    conflict = start(app_client, {**body, "threshold": 70}, key)
    assert (
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    )
    busy = start(app_client, body)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "expectation_check_active"
    receipt = app_client.get(f"/api/generation-submissions/{key}").json()
    assert receipt["endpoint"] == "expectation"
    assert receipt["result"]["id"] == first.json()["id"]

    container = app_client.app.state.container
    with pytest.raises(AppError) as auto:
        app_client.portal.call(lambda: container.automation.change(user["id"], 0, enabled=True))
    assert auto.value.code == "expectation_check_active"

    login_ready_admin(app_client)
    assert app_client.get(f"{CHECKS}/{first.json()['id']}").status_code == 404
    assert app_client.get(f"{CHECKS}/latest").json() == {"check": None}
    stop = app_client.post(
        f"{CHECKS}/{first.json()['id']}/stop", headers={"X-CSRF-Token": csrf(app_client)}
    )
    assert stop.status_code == 404
    restore_cookie(app_client, cookie)
    assert app_client.get(f"{CHECKS}/{first.json()['id']}").status_code == 200


def test_rejections_create_nothing(app_client):
    user, _ = provision_user(app_client)
    seed_vision(app_client, vision=False)
    unavailable = start(app_client, check_body(app_client))
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "vision_unavailable"
    seed_vision(app_client)
    refine_without_prompt = start(app_client, check_body(app_client, prompt="", direction="x"))
    assert refine_without_prompt.status_code == 400
    assert refine_without_prompt.json()["error"]["code"] == "prompt_required"
    too_many = start(
        app_client, {**check_body(app_client), "items": check_body(app_client, count=2)["items"]}
    )
    assert too_many.status_code == 422
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        session.add(AutoGeneration(user_id=user["id"], enabled=True, status="waiting"))
        session.commit()
    automated = start(app_client, check_body(app_client))
    assert automated.status_code == 409
    assert automated.json()["error"]["code"] == "auto_generation_enabled"
    assert rows(app_client, ExpectationCheck) == []


def test_preferences_round_trip_expectations(app_client):
    provision_user(app_client)
    current = app_client.get("/api/preferences").json()
    settings = {
        **current["settings"],
        "creative_direction_expectations": {
            "enabled": True,
            "text": "red raincoat\nlighthouse beam",
            "threshold": 85,
            "max_attempts": 4,
        },
    }
    saved = app_client.put(
        "/api/preferences",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={"expected_revision": current["revision"], "settings": settings},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["creative_direction_expectations"]["threshold"] == 85
    invalid = app_client.put(
        "/api/preferences",
        headers={"X-CSRF-Token": csrf(app_client)},
        json={
            "expected_revision": saved.json()["revision"],
            "settings": {**settings, "creative_direction_expectations": {"threshold": 0}},
        },
    )
    assert invalid.status_code == 422


def test_restart_resumes_a_saved_composition_without_recomposing(settings_factory, fake_state):
    settings = settings_factory()
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="restart.checks")
        seed_vision(first)
        check = started(first, check_body(first))
        prompt = step(first, check)["attempts"][0]["prompt"]
    calls = len(fake_state.ollama_calls)
    with TestClient(create_app(settings)) as second:
        restore_cookie(second, cookie)
        resumed = step(second, check)
        assert resumed["attempts"][0]["status"] == "generating"
        assert resumed["attempts"][0]["prompt"] == prompt
        assert len(fake_state.ollama_calls) == calls


def test_background_coordinator_runs_the_whole_loop(settings_factory, fake_state):
    settings = settings_factory(enable_background_worker=True)
    fake_state.ollama_vision_scores.append([50, 95])
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="loop.checks")
        # The health loop records the router's advertised vision capability.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = client.get("/api/prompt-assistant/status").json()
            if status["vision_available"]:
                break
            time.sleep(0.05)
        assert status["vision_available"] is True
        check = started(client, check_body(client, purpose="generate", count=2))
        deadline = time.monotonic() + 30
        current = check
        while time.monotonic() < deadline and current["status"] not in {
            "passed",
            "not_met",
            "failed",
            "stopped",
        }:
            time.sleep(0.1)
            current = get(client, check)
        assert current["status"] == "passed", current
        assert [item["status"] for item in current["attempts"]] == ["not_met", "passed"]
        assert len(current["queued"]["generation_ids"]) == 1
        assert len(fake_state.ollama_vision_calls) == 2


def test_coordinator_stop_cancels_cleanly(app_client):
    service = app_client.app.state.container.expectation_checks

    async def cycle() -> None:
        await service.start()
        service.wake()
        await asyncio.sleep(0.05)
        await service.stop()

    app_client.portal.call(cycle)
    assert service._task is None
