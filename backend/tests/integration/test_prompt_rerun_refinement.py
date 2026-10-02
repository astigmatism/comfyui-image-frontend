from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from app.errors import AppError
from app.models import (
    Collection,
    Generation,
    GenerationPreparation,
    PromptAssistantRun,
    PromptGenerationRun,
    PromptRerunRun,
)
from app.services.ollama import ComposeResult
from app.services.prompt_generation import PromptGenerationService
from tests.conftest import csrf
from tests.helpers import create_generation, login_ready_admin, provision_user, restore_cookie
from tests.integration.test_prompt_generation import wait_for
from tests.integration.test_prompt_rerun import body, rerun, rows


def accept(client, prompts=("a red lighthouse", "an orchard in fog"), **options):
    originals = [create_generation(client, prompt) for prompt in prompts]
    request = body(
        client,
        [item["id"] for item in originals],
        refinement={
            "creative_direction": "soft cinematic light",
            "think": False,
        },
        **options,
    )
    response = rerun(client, request)
    assert response.status_code == 202, response.text
    return response.json(), originals, request


def status(client, result):
    response = client.get(f"/api/gallery/prompt-rerun/{result['run']['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def advance(client):
    service = client.app.state.container.prompt_generation
    owner = rows(client, PromptRerunRun)[0].owner_id
    client.portal.call(service.advance, f"rerun:{owner}")


def stop(client, result):
    return client.post(
        f"/api/gallery/prompt-rerun/{result['run']['id']}/stop",
        headers={"X-CSRF-Token": csrf(client)},
    )


def test_refines_once_per_prompt_queues_incrementally_and_copies_provenance(app_client, fake_state):
    provision_user(app_client)
    result, originals, request = accept(app_client, quantity=3)
    assert result["items"] == []
    assert result["run"]["counts"]["waiting"] == 2
    assert rows(app_client, PromptGenerationRun) == []
    assert len(rows(app_client, GenerationPreparation)) == 6
    assert len(rows(app_client, Generation)) == 2

    advance(app_client)
    first = status(app_client, result)
    assert first["queued_count"] == 3
    assert first["counts"]["waiting"] == 1
    assert len(fake_state.ollama_calls) == 1
    # No image worker runs here: the second refinement must not wait for those images.
    advance(app_client)
    done = status(app_client, result)
    assert done["status"] == "completed"
    assert done["queued_count"] == 6
    assert len(fake_state.ollama_calls) == 2
    created = rows(app_client, Generation, Generation.collection_id == result["collection"]["id"])
    for image in created:
        snapshot = image.prompt_assistant_json
        assert snapshot["mode"] == "refine"
        assert snapshot["thinking_enabled"] is False
        assert snapshot["creative_direction"] == request["refinement"]["creative_direction"]
        assert snapshot["source_generation_id"] in {item["id"] for item in originals}
        assert snapshot["prompt_before"] in {item["original_prompt"] for item in done["items"]}
        assert image.final_prompt == snapshot["prompt_before"] + ", soft cinematic light"
        assert snapshot["composition_id"]
    assert {item.prompt_before for item in rows(app_client, PromptAssistantRun)} == {
        item["original_prompt"] for item in done["items"]
    }
    assert app_client.get(
        f"/api/gallery/prompt-rerun?collection_id={result['collection']['id']}"
    ).json() == [done]


def test_failed_refinement_continues_without_fallback(app_client, monkeypatch):
    provision_user(app_client)
    result, _, _ = accept(app_client)
    calls = []

    async def compose(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise AppError("ollama_unavailable", "Local model unavailable.")
        return ComposeResult(prompt="refined orchard", model="test", raw_response={}, duration_ms=1)

    monkeypatch.setattr(app_client.app.state.container.ollama, "compose", compose)
    advance(app_client)
    advance(app_client)
    done = status(app_client, result)
    assert done["counts"]["failed"] == 1
    assert done["counts"]["finished"] == 1
    assert done["queued_count"] == 1
    assert done["items"][0]["error"]["code"] == "ollama_unavailable"
    assert done["items"][0]["prompt"] is None


def test_coordinator_serializes_prompts_across_reruns_in_acceptance_order(app_client, monkeypatch):
    provision_user(app_client)
    first, _, _ = accept(app_client, prompts=("one", "two"))
    second, _, _ = accept(app_client, prompts=("three", "four"))
    calls = []
    active = 0
    max_active = 0

    async def compose(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(active, max_active)
        calls.append(kwargs["prompt"])
        await asyncio.sleep(0.05)
        active -= 1
        return ComposeResult(
            prompt=kwargs["prompt"] + " refined", model="test", raw_response={}, duration_ms=1
        )

    container = app_client.app.state.container
    monkeypatch.setattr(container.ollama, "compose", compose)
    app_client.portal.call(container.prompt_generation.start)
    wait_for(
        app_client,
        f"/api/gallery/prompt-rerun/{second['run']['id']}",
        lambda run: run["status"] == "completed",
    )
    assert calls == ["one", "two", "three", "four"]
    assert max_active == 1
    assert status(app_client, first)["queued_count"] == 2


def test_each_prompt_image_group_accepts_atomically_and_later_prompts_continue(
    app_client, monkeypatch
):
    provision_user(app_client)
    result, _, _ = accept(app_client, quantity=2)
    service = app_client.app.state.container.generations
    prepare = service._prepare_accept
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AppError("image_invalid", "Invalid image settings.")
        return prepare(*args, **kwargs)

    monkeypatch.setattr(service, "_prepare_accept", fail_second)
    advance(app_client)
    failed = status(app_client, result)
    assert failed["counts"]["failed"] == 1
    assert failed["queued_count"] == 0
    assert (
        rows(app_client, Generation, Generation.collection_id == result["collection"]["id"]) == []
    )
    advance(app_client)
    assert status(app_client, result)["queued_count"] == 2


def test_restart_reuses_saved_refinements_and_does_not_wait_for_image_availability(
    app_client, fake_state, monkeypatch
):
    provision_user(app_client)
    result, _, _ = accept(app_client, quantity=2)
    container = app_client.app.state.container
    prepare = container.generations._prepare_accept

    def unavailable(*args, **kwargs):
        raise AppError("comfyui_instance_unavailable", "Image service offline.", status_code=503)

    monkeypatch.setattr(container.generations, "_prepare_accept", unavailable)
    advance(app_client)
    # A new coordinator simulates restart, with no in-memory work retained.
    container.prompt_generation = PromptGenerationService(container)
    advance(app_client)
    ready = status(app_client, result)
    assert ready["counts"]["ready"] == 2
    assert ready["queued_count"] == 0
    assert len(fake_state.ollama_calls) == 2
    monkeypatch.setattr(container.generations, "_prepare_accept", prepare)
    container.prompt_generation = PromptGenerationService(container)
    advance(app_client)
    assert status(app_client, result)["queued_count"] == 4
    container.prompt_generation = PromptGenerationService(container)
    advance(app_client)
    assert status(app_client, result)["queued_count"] == 4
    assert len(fake_state.ollama_calls) == 2


@pytest.mark.parametrize("delete_destination", [False, True])
def test_stop_during_llm_call_prevents_late_acceptance(app_client, monkeypatch, delete_destination):
    provision_user(app_client)
    result, _, _ = accept(app_client, quantity=2)
    container = app_client.app.state.container
    started = asyncio.Event()
    release = asyncio.Event()

    async def compose(**kwargs):
        started.set()
        await release.wait()
        return ComposeResult(prompt="late refinement", model="test", raw_response={}, duration_ms=1)

    monkeypatch.setattr(container.ollama, "compose", compose)
    owner = rows(app_client, PromptRerunRun)[0].owner_id
    task = app_client.portal.start_task_soon(container.prompt_generation.advance, f"rerun:{owner}")
    app_client.portal.call(asyncio.wait_for, started.wait(), 3)
    try:
        assert status(app_client, result)["counts"]["refining"] == 1
        if delete_destination:
            response = app_client.delete(
                f"/api/collections/{result['collection']['id']}",
                headers={"X-CSRF-Token": csrf(app_client)},
            )
        else:
            response = stop(app_client, result)
        assert response.status_code in {200, 204}, response.text
    finally:
        app_client.portal.call(release.set)
        task.result(timeout=3)
    done = status(app_client, result)
    assert done["status"] == "stopped"
    assert done["counts"]["cancelled"] == 2
    assert done["queued_count"] == 0
    assert len(rows(app_client, Generation)) == 2
    activity = app_client.get("/api/generation-activity").json()["run"]
    assert activity["cancelled_count"] == 4
    assert activity["failed_count"] == 0


def test_stop_preserves_accepted_images_and_is_idempotent(app_client):
    provision_user(app_client)
    result, _, _ = accept(app_client)
    advance(app_client)
    assert stop(app_client, result).status_code == 200
    assert stop(app_client, result).status_code == 200
    advance(app_client)
    done = status(app_client, result)
    assert done["queued_count"] == 1
    assert done["counts"]["cancelled"] == 1
    assert done["counts"]["finished"] == 1


def test_receipt_recovery_is_durable_and_owner_scoped(app_client):
    _, cookie = provision_user(app_client)
    source = create_generation(app_client, "an original")
    request = body(app_client, [source["id"]], refinement={"creative_direction": "at night"})
    key = str(uuid4())
    accepted = rerun(app_client, request, key).json()
    advance(app_client)
    replay = rerun(app_client, request, key)
    assert replay.status_code == 202
    assert replay.json()["run"]["id"] == accepted["run"]["id"]
    assert replay.json()["run"]["queued_count"] == 1
    assert app_client.get(f"/api/generation-submissions/{key}").json()["result"] == replay.json()
    assert len(rows(app_client, Collection)) == 1
    request["refinement"]["creative_direction"] = "at noon"
    assert rerun(app_client, request, key).status_code == 409
    login_ready_admin(app_client)
    assert app_client.get(f"/api/gallery/prompt-rerun/{accepted['run']['id']}").status_code == 404
    assert stop(app_client, accepted).status_code == 404
    assert (
        app_client.get(
            f"/api/gallery/prompt-rerun?collection_id={accepted['collection']['id']}"
        ).status_code
        == 404
    )
    restore_cookie(app_client, cookie)
    assert (
        app_client.post(f"/api/gallery/prompt-rerun/{accepted['run']['id']}/stop").status_code
        == 403
    )


def test_duplicate_handling_occurs_before_refinement_and_validation_is_atomic(
    app_client, fake_state
):
    provision_user(app_client)
    result, _, _ = accept(app_client, prompts=("same", "same"))
    assert result["prompt_count"] == 1
    advance(app_client)
    repeated, _, request = accept(app_client, prompts=("same", "same"), skip_duplicates=False)
    assert repeated["prompt_count"] == 2
    advance(app_client)
    advance(app_client)
    assert len(fake_state.ollama_calls) == 3
    assert len(rows(app_client, PromptRerunRun)) == 2
    request["refinement"]["creative_direction"] = " "
    assert rerun(app_client, request).status_code == 422
    request["refinement"]["creative_direction"] = "at night"
    request["parameters"]["width"] = -2
    assert rerun(app_client, request).status_code == 422
    assert len(rows(app_client, PromptRerunRun)) == 2
    assert len(rows(app_client, Collection)) == 2
