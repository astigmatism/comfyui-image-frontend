import copy
import json
import time
from uuid import uuid4

import pytest
from app.domain.prompt_generation import collect_text
from app.errors import AppError
from app.models import Artifact, Generation, GenerationPreparation, PromptGenerationRun
from sqlalchemy import func, select
from tests.conftest import csrf
from tests.helpers import generation_payload, provision_user
from tests.publication_fixtures import build_publication_bundle


def register(client, fake_state):
    bundle = build_publication_bundle("text")
    fake_state.workflow_files.update(bundle.files)
    client.portal.call(client.app.state.container.registry.refresh)
    sources = client.get("/api/workflows?output_kind=text").json()
    assert len(sources) == 1, sources
    assert all(item["output_kind"] == "image" for item in client.get("/api/workflows").json())
    source = sources[0]
    return {
        "source_key": source["source_key"],
        "revision": source["revision"],
        "parameters": {"subject_name": "Mira", "seed": "random"},
    }


def post(client, path, payload, key=None):
    return client.post(
        path,
        json=payload,
        headers={
            "X-CSRF-Token": csrf(client),
            "Idempotency-Key": key or str(uuid4()),
            "X-CIF-Generation-Protocol": "3",
        },
    )


def wait_for(client, path, done):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        result = client.get(path).json()
        if done(result):
            return result
        time.sleep(0.05)
    raise AssertionError(result)


def start(client):
    container = client.app.state.container
    client.portal.call(container.worker.start)
    client.portal.call(container.prompt_generation.start)


def test_prompt_roundtrip_empty_subject_receipt_and_no_images(app_client, fake_state):
    provision_user(app_client)
    payload = register(app_client, fake_state)
    payload["parameters"]["subject_name"] = ""
    key = str(uuid4())
    response = post(app_client, "/api/prompt-generations", payload, key)
    assert response.status_code == 202, response.text
    identity = response.json()["id"]
    assert post(app_client, "/api/prompt-generations", payload, key).json()["id"] == identity
    assert app_client.get(f"/api/generation-submissions/{key}").json()["result"]["id"] == identity
    changed = copy.deepcopy(payload)
    changed["parameters"]["subject_name"] = "different"
    assert post(app_client, "/api/prompt-generations", changed, key).status_code == 409
    start(app_client)
    result = wait_for(
        app_client,
        f"/api/prompt-generations/{identity}",
        lambda value: value["status"] in {"succeeded", "failed"},
    )
    assert result["status"] == "succeeded", result
    assert result["prompt"].startswith("the woman explores")
    with app_client.app.state.container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Generation)) == 0
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0
    assert app_client.get(f"/api/prompt-generations/{uuid4()}").status_code == 404


def test_manual_batch_shares_one_durable_refined_prompt(app_client, fake_state):
    provision_user(app_client)
    prompt = register(app_client, fake_state)
    image = generation_payload(app_client, "")
    payload = {
        "items": [
            {
                "generation": image,
                "prompt_generation": prompt,
                "assistant": {"mode": "refine", "creative_direction": "Soft morning light"},
            }
            for _ in range(3)
        ]
    }
    key = str(uuid4())
    response = post(app_client, "/api/generation-preparations", payload, key)
    assert response.status_code == 202, response.text
    identity = response.json()["id"]
    assert post(app_client, "/api/generation-preparations", payload, key).json()["id"] == identity
    start(app_client)
    result = wait_for(
        app_client,
        f"/api/generation-preparations/{identity}",
        lambda value: all(i["status"] in {"accepted", "failed"} for i in value["items"]),
    )
    assert all(item["status"] == "accepted" for item in result["items"]), result
    # One generated prompt and one refinement are shared by the whole batch.
    assert len({item["raw_prompt"] for item in result["items"]}) == 1
    assert len(fake_state.ollama_calls) == 1
    with app_client.app.state.container.db.session_factory() as session:
        rows = list(session.scalars(select(GenerationPreparation)))
        assert all(row.assistant_run_id and row.generation_id for row in rows)
        assert len({row.prompt_run_id for row in rows}) == 1
        assert session.scalar(select(func.count()).select_from(PromptGenerationRun)) == 1
        assert session.scalar(select(func.count()).select_from(Generation)) == 3
        generations = list(session.scalars(select(Generation)))
        assert len({generation.final_prompt for generation in generations}) == 1
        seeds = [
            json.dumps(generation.resolved_seeds_json, sort_keys=True) for generation in generations
        ]
        assert len(set(seeds)) == 3


def test_shared_text_failure_fails_the_whole_batch(app_client, fake_state):
    provision_user(app_client)
    prompt = register(app_client, fake_state)
    image = generation_payload(app_client, "original")
    payload = {"items": [{"generation": image, "prompt_generation": prompt} for _ in range(3)]}
    response = post(app_client, "/api/generation-preparations", payload)
    assert response.status_code == 202, response.text
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        rows = list(session.scalars(select(GenerationPreparation)))
        text = session.get(PromptGenerationRun, rows[0].prompt_run_id)
        text.status, text.error_code, text.error_message = (
            "failed",
            "prompt_output_invalid",
            "Invalid result",
        )
        session.commit()
    app_client.portal.call(container.prompt_generation.advance, rows[0].id)
    with container.db.session_factory() as session:
        rows = list(session.scalars(select(GenerationPreparation)))
        assert all(row.status == "failed" for row in rows)
        assert all(row.error_code == "prompt_output_invalid" for row in rows), [
            row.error_code for row in rows
        ]
        assert session.scalar(select(func.count()).select_from(Generation)) == 0


def test_items_must_request_the_same_prompt_and_refinement(app_client, fake_state):
    provision_user(app_client)
    prompt = register(app_client, fake_state)
    image = generation_payload(app_client, "original")
    different = dict(prompt)
    different["parameters"] = {"subject_name": "Someone else", "seed": "random"}
    assert (
        post(
            app_client,
            "/api/generation-preparations",
            {
                "items": [
                    {"generation": image, "prompt_generation": prompt},
                    {"generation": image, "prompt_generation": different},
                ]
            },
        ).status_code
        == 422
    )
    refined = {"mode": "refine", "creative_direction": "Soft morning light"}
    assert (
        post(
            app_client,
            "/api/generation-preparations",
            {
                "items": [
                    {"generation": image, "prompt_generation": prompt, "assistant": refined},
                    {"generation": image, "prompt_generation": prompt},
                ]
            },
        ).status_code
        == 422
    )


def test_invalid_generator_and_create_mode_are_rejected(app_client, fake_state):
    provision_user(app_client)
    prompt = register(app_client, fake_state)
    image = generation_payload(app_client, "original")
    assert (
        post(
            app_client,
            "/api/generations",
            {
                **image,
                "source_key": prompt["source_key"],
                "revision": prompt["revision"],
                "parameters": prompt["parameters"],
            },
        ).status_code
        == 422
    )
    assert (
        post(
            app_client,
            "/api/generation-preparations",
            {
                "items": [
                    {
                        "generation": image,
                        "prompt_generation": prompt,
                        "assistant": {"mode": "create", "creative_direction": "forest"},
                    }
                ]
            },
        ).status_code
        == 422
    )
    del prompt["parameters"]["subject_name"]
    assert post(app_client, "/api/prompt-generations", prompt).status_code == 422


def test_declared_text_matching_and_bounds():
    bundle = build_publication_bundle("text")
    final = bundle.manifest()["interface"]["outputs"][0]
    contract = {"outputs": [{**final, "kind": "text"}]}
    value = "Mira in a forest"
    metadata = {
        "output_id": "prompt",
        "instance_uuid": final["instance_uuid"],
        "role": "final",
        "kind": "text",
        "cardinality": "one",
        "value": value,
    }
    history = {"outputs": {"130": {"text": [value], "comfyui_image_frontend": [metadata]}}}
    assert collect_text(contract, history) == value
    for field, invalid in [("value", "wrong"), ("instance_uuid", str(uuid4())), ("kind", "image")]:
        changed = copy.deepcopy(history)
        changed["outputs"]["130"]["comfyui_image_frontend"][0][field] = invalid
        with pytest.raises(AppError, match="declared text"):
            collect_text(contract, changed)


def test_automation_shares_one_prompt_and_stops_at_limit(app_client, fake_state):
    from tests.integration.test_auto_generation import enable, tick

    user, _ = provision_user(app_client)
    prompt = register(app_client, fake_state)
    enabled = enable(app_client, prompt_generation=prompt, quantity=3, max_generations=2)
    assert enabled["snapshot"]["prompt_generation"] == {
        **prompt,
        "comfyui_instance_id": "test-instance",
    }
    tick(app_client, user["id"])
    start(app_client)
    result = wait_for(
        app_client,
        "/api/auto-generation",
        lambda value: value["status"] in {"completed", "blocked"},
    )
    assert result["status"] == "completed", result
    assert result["accepted_count"] == 2
    with app_client.app.state.container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PromptGenerationRun)) == 1
        assert len(set(session.scalars(select(Generation.final_prompt)))) == 1
        images = list(session.scalars(select(Generation)))
        assert len(images) == 2
        assert images[0].resolved_seeds_json != images[1].resolved_seeds_json


def test_stop_discards_unaccepted_preparations_and_keeps_accepted_images(app_client, fake_state):
    from tests.integration.test_auto_generation import command, enable, tick

    user, _ = provision_user(app_client)
    prompt = register(app_client, fake_state)
    enabled = enable(app_client, prompt_generation=prompt, quantity=2)
    tick(app_client, user["id"])
    stopped = command(app_client, expected_revision=enabled["revision"], enabled=False)
    assert stopped.status_code == 200
    with app_client.app.state.container.db.session_factory() as session:
        assert set(session.scalars(select(GenerationPreparation.status))) == {"discarded"}
        assert set(session.scalars(select(PromptGenerationRun.status))) == {"discarded"}
    start(app_client)
    time.sleep(0.2)
    assert fake_state.submitted == []


def test_text_restart_does_not_resubmit_uncertain_acceptance(app_client, fake_state):
    provision_user(app_client)
    payload = register(app_client, fake_state)
    response = post(app_client, "/api/prompt-generations", payload)
    identity = response.json()["id"]
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        session.get(PromptGenerationRun, identity).status = "submitting"
        session.commit()
    app_client.portal.call(container.prompt_generation.recover, container.worker)
    result = app_client.get(f"/api/prompt-generations/{identity}").json()
    assert result["status"] == "failed"
    assert result["error"]["code"] == "comfyui_submission_uncertain"
    assert not fake_state.submitted


def test_failed_text_never_substitutes_the_original_image_prompt(app_client, fake_state):
    provision_user(app_client)
    prompt = register(app_client, fake_state)
    response = post(
        app_client,
        "/api/generation-preparations",
        {
            "items": [
                {
                    "generation": generation_payload(app_client, "old prompt"),
                    "prompt_generation": prompt,
                }
            ]
        },
    )
    identity = response.json()["items"][0]["id"]
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        preparation = session.get(GenerationPreparation, identity)
        text = session.get(PromptGenerationRun, preparation.prompt_run_id)
        text.status, text.error_code, text.error_message = (
            "failed",
            "prompt_output_invalid",
            "Invalid result",
        )
        session.commit()
    app_client.portal.call(container.prompt_generation.advance, identity)
    with container.db.session_factory() as session:
        assert session.get(GenerationPreparation, identity).status == "failed"
        assert session.scalar(select(func.count()).select_from(Generation)) == 0


def test_stop_keeps_an_accepted_image_and_apply_invalidates_pending_work(app_client, fake_state):
    from tests.integration.test_auto_generation import command, enable, tick

    user, _ = provision_user(app_client)
    prompt = register(app_client, fake_state)
    enabled = enable(app_client, prompt_generation=prompt, quantity=2)
    tick(app_client, user["id"])
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        rows = list(
            session.scalars(select(GenerationPreparation).order_by(GenerationPreparation.position))
        )
        first_id = rows[0].id
        text = session.get(PromptGenerationRun, rows[0].prompt_run_id)
        text.status, text.prompt = "succeeded", "Mira explores a forest"
        session.commit()
    app_client.portal.call(container.prompt_generation.advance, first_id)
    stopped = command(app_client, expected_revision=enabled["revision"], enabled=False)
    assert stopped.status_code == 200
    with container.db.session_factory() as session:
        assert set(session.scalars(select(GenerationPreparation.status))) == {"accepted"}
        assert session.scalar(select(func.count()).select_from(Generation)) == 2
    enabled = enable(app_client, prompt_generation=prompt, quantity=2)
    # The accepted image continues; complete it to allow another automatic cycle.
    from tests.integration.test_auto_generation import complete

    complete(app_client)
    tick(app_client, user["id"])
    applied = command(
        app_client,
        "/apply",
        expected_revision=enabled["revision"],
        snapshot={**enabled["snapshot"], "quantity": 1},
    )
    assert applied.status_code == 200, applied.text
    with container.db.session_factory() as session:
        assert not session.scalar(
            select(GenerationPreparation.id).where(GenerationPreparation.status == "preparing")
        )


def test_text_and_image_jobs_share_capacity_and_manual_priority(
    app_client, fake_state, monkeypatch
):
    import asyncio

    from tests.helpers import create_generation
    from tests.integration.test_auto_generation import enable, tick

    user, _ = provision_user(app_client)
    prompt = register(app_client, fake_state)
    image = create_generation(app_client, "manual image")
    enable(app_client, prompt_generation=prompt)
    tick(app_client, user["id"])
    worker = app_client.app.state.container.worker
    assert worker._claim_next()[0] == image["id"]
    from tests.integration.test_auto_generation import complete

    complete(app_client)
    tick(app_client, user["id"])
    claim = worker._claim_next()
    assert claim[0].startswith("text:")

    async def capacity():
        instance = worker.comfyui_instances.default_id
        task = asyncio.create_task(asyncio.sleep(60))
        worker._active[claim[0]] = task
        worker._active_instance_ids[claim[0]] = instance

        def unexpected_claim(*args):
            raise AssertionError("Text job must occupy the shared capacity")

        monkeypatch.setattr(worker, "_claim_next", unexpected_claim)
        try:
            await worker._dispatch_iteration()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            worker._active.pop(claim[0])
            worker._active_instance_ids.pop(claim[0])

    app_client.portal.call(capacity)


def test_known_text_result_recovers_without_a_second_submission(app_client, fake_state):
    provision_user(app_client)
    prompt = register(app_client, fake_state)
    identity = post(app_client, "/api/prompt-generations", prompt).json()["id"]
    container = app_client.app.state.container
    app_client.portal.call(container.prompt_generation.execute, identity)
    original = app_client.get(f"/api/prompt-generations/{identity}").json()
    assert original["status"] == "succeeded"
    with container.db.session_factory() as session:
        run = session.get(PromptGenerationRun, identity)
        run.status, run.prompt = "running", None
        session.commit()
    app_client.portal.call(container.prompt_generation.recover, container.worker)
    recovered = wait_for(
        app_client,
        f"/api/prompt-generations/{identity}",
        lambda value: value["status"] == "succeeded",
    )
    assert recovered["prompt"] == original["prompt"]
    assert len(fake_state.submitted) == 1
