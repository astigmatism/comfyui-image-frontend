import asyncio
import copy
from uuid import uuid4

import pytest
from app.errors import AppError
from app.models import (
    AutoGeneration,
    AutoGenerationCycle,
    Generation,
    GenerationPreparation,
    PromptAssistantRun,
    PromptGenerationRun,
    User,
    WorkflowProfile,
)
from app.schemas import GenerationCreate
from app.services.prompt_generation import PromptGenerationService
from sqlalchemy import func, select
from tests.conftest import csrf
from tests.helpers import generation_payload, provision_user
from tests.integration.test_auto_generation import command, complete, enable, tick
from tests.integration.test_prompt_generation import post, register


def prepare(client, fake_state, **overrides):
    user, _ = provision_user(client)
    prompt = register(client, fake_state)
    generation = generation_payload(client, "original", seed=overrides.pop("image_seed", "random"))
    state = enable(
        client,
        **{"prompt_generation": prompt, "generation": generation, "quantity": 2, **overrides},
    )
    tick(client, user["id"])
    container = client.app.state.container
    with container.db.session_factory() as session:
        rows = list(
            session.scalars(select(GenerationPreparation).order_by(GenerationPreparation.position))
        )
        assert len({row.prompt_run_id for row in rows}) == 1
        run = session.get(PromptGenerationRun, rows[0].prompt_run_id)
        run.status, run.prompt = "succeeded", "A lighthouse at night"
        session.commit()
        identities = [row.id for row in rows]
    return user, state, identities


@pytest.mark.parametrize(
    "overrides", [{"quantity": 1, "variants": [{}, {}]}, {"image_seed": "123"}]
)
def test_batch_preserves_checkpoint_comparison_and_fixed_seeds(app_client, fake_state, overrides):
    _, _, identities = prepare(app_client, fake_state, **overrides)
    container = app_client.app.state.container
    app_client.portal.call(container.prompt_generation.advance, identities[0])
    with container.db.session_factory() as session:
        images = list(session.scalars(select(Generation)))
        assert len(images) == 2
        assert images[0].resolved_seeds_json == images[1].resolved_seeds_json
        if "image_seed" in overrides:
            assert str(images[0].resolved_seeds_json["seed"]) == "123"


def test_batch_refines_once_reuses_saved_work_and_accepts_once(app_client, fake_state, monkeypatch):
    user, _state, identities = prepare(
        app_client, fake_state, assistant={"mode": "refine", "creative_direction": "add mist"}
    )
    container = app_client.app.state.container
    original = container.generations._prepare_accept

    def unavailable(*args, **kwargs):
        raise AppError("comfyui_instance_unavailable", "offline")

    monkeypatch.setattr(container.generations, "_prepare_accept", unavailable)
    app_client.portal.call(container.prompt_generation.advance, identities[0])
    assert len(fake_state.ollama_calls) == 1
    with container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Generation)) == 0
        assert session.get(GenerationPreparation, identities[0]).assistant_run_id
    monkeypatch.setattr(container.generations, "_prepare_accept", original)
    # A new service represents process-local coordination being lost at restart.
    restarted = PromptGenerationService(container)

    async def race():
        await asyncio.gather(*(restarted.advance(identity) for identity in identities))

    app_client.portal.call(race)
    app_client.portal.call(restarted.advance, identities[0])
    with container.db.session_factory() as session:
        images = list(session.scalars(select(Generation)))
        assert len(images) == 2
        assert images[0].final_prompt == images[1].final_prompt
        assert images[0].resolved_seeds_json != images[1].resolved_seeds_json
        assert images[0].prompt_assistant_json == images[1].prompt_assistant_json
        assert session.scalar(select(func.count()).select_from(PromptAssistantRun)) == 1
        assert session.get(AutoGeneration, user["id"]).accepted_count == 2
    assert len(fake_state.ollama_calls) == 1
    complete(app_client)
    tick(app_client, user["id"])
    with container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PromptGenerationRun)) == 2


def test_batch_acceptance_rolls_back_all_images_and_blocks_once(
    app_client, fake_state, monkeypatch
):
    user, _, identities = prepare(app_client, fake_state)
    container = app_client.app.state.container
    original = container.generations._prepare_accept
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AppError("source_unavailable", "Image settings need attention.")
        return original(*args, **kwargs)

    monkeypatch.setattr(container.generations, "_prepare_accept", fail_second)
    app_client.portal.call(container.prompt_generation.advance, identities[0])
    app_client.portal.call(container.prompt_generation.advance, identities[1])
    tick(app_client, user["id"])
    with container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Generation)) == 0
        assert set(session.scalars(select(GenerationPreparation.status))) == {"failed"}
        auto = session.get(AutoGeneration, user["id"])
        assert (auto.status, auto.accepted_count) == ("blocked", 0)


def test_prompt_rejection_is_retained_and_blocks_the_entire_batch(app_client, fake_state):
    user, _, identities = prepare(app_client, fake_state)
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        row = session.get(GenerationPreparation, identities[0])
        run_id = row.prompt_run_id
        session.get(PromptGenerationRun, run_id).status = "queued"
        session.commit()
    fake_state.reject_prompts = True
    app_client.portal.call(container.prompt_generation.execute, run_id)
    for identity in identities:
        app_client.portal.call(container.prompt_generation.advance, identity)
    with container.db.session_factory() as session:
        run = session.get(PromptGenerationRun, run_id)
        assert run.internal_diagnostics_json["status"] == 400
        assert run.error_message.startswith("Prompt generation failed:")
        assert set(session.scalars(select(GenerationPreparation.status))) == {"failed"}
        assert session.get(AutoGeneration, user["id"]).status == "blocked"
        assert session.scalar(select(func.count()).select_from(Generation)) == 0
    assert "internal_diagnostics" not in app_client.get(f"/api/prompt-generations/{run_id}").text


def test_settings_changed_during_batch_refinement_discard_old_results(
    app_client, fake_state, monkeypatch
):
    from app.schemas import AutoGenerationSnapshot
    from app.services import prompt_generation

    user, state, identities = prepare(
        app_client, fake_state, assistant={"mode": "refine", "creative_direction": "mist"}
    )
    container = app_client.app.state.container
    original = prompt_generation.compose_prompt

    async def edit_during_refinement(*args, **kwargs):
        await container.automation.change(
            user["id"],
            state["revision"],
            snapshot=AutoGenerationSnapshot.model_validate({**state["snapshot"], "quantity": 3}),
        )
        return await original(*args, **kwargs)

    monkeypatch.setattr(prompt_generation, "compose_prompt", edit_during_refinement)
    app_client.portal.call(container.prompt_generation.advance, identities[0])
    with container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Generation)) == 0
        assert set(session.scalars(select(GenerationPreparation.status))) == {"discarded"}


def test_legacy_pending_groups_are_retired_without_touching_accepted_images(app_client, fake_state):
    _, _, identities = prepare(app_client, fake_state)
    container = app_client.app.state.container
    with container.db.session_factory() as session:
        row = session.get(GenerationPreparation, identities[1])
        original = session.get(PromptGenerationRun, row.prompt_run_id)
        other = PromptGenerationRun(
            **{
                column.name: copy.deepcopy(getattr(original, column.name))
                for column in PromptGenerationRun.__table__.columns
                if column.name != "id"
            }
        )
        session.add(other)
        session.flush()
        row.prompt_run_id = other.id
        accepted = session.get(GenerationPreparation, identities[0])
        image, _ = container.generations._prepare_accept(
            session,
            user=session.get(User, accepted.owner_id),
            request=GenerationCreate.model_validate(accepted.request_json["generation"]),
            frozen_profile=session.get(WorkflowProfile, accepted.profile_id),
        )
        accepted.status, accepted.generation_id = "accepted", image.id
        image_id = image.id
        session.commit()
    container.prompt_generation._retire_legacy_batches()
    with container.db.session_factory() as session:
        assert session.get(GenerationPreparation, identities[0]).status == "accepted"
        assert session.get(GenerationPreparation, identities[1]).status == "discarded"
        assert session.get(Generation, image_id) is not None
        assert session.scalar(select(AutoGenerationCycle.state)) == "discarded"


@pytest.mark.parametrize(
    "path", ["/api/generations", "/api/generations/batch", "/api/generation-preparations"]
)
def test_manual_guard_keeps_existing_receipts_replayable(app_client, fake_state, path):
    provision_user(app_client)
    image = generation_payload(app_client, "manual receipt")
    payload = image if path == "/api/generations" else {"items": [image]}
    if path == "/api/generation-preparations":
        payload = {
            "items": [{"generation": image, "prompt_generation": register(app_client, fake_state)}]
        }
    key = str(uuid4())
    accepted = post(app_client, path, payload, key)
    assert accepted.is_success
    enable(app_client)
    replay = post(app_client, path, payload, key)
    assert replay.is_success

    def identities(value):
        if path == "/api/generations/batch":
            return [item["generation"]["id"] for item in value["items"]]
        return value["id"]

    assert identities(replay.json()) == identities(accepted.json())
    rejected = post(app_client, path, payload)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "auto_generation_enabled"


def test_folder_pin_and_limits_survive_edits_until_next_enable(app_client):
    user, _ = provision_user(app_client)
    folders = [
        app_client.post(
            "/api/collections", headers={"X-CSRF-Token": csrf(app_client)}, json={"name": name}
        ).json()["id"]
        for name in ["Original", "Next"]
    ]
    generation = generation_payload(app_client, "folder pin")
    state = enable(app_client, generation={**generation, "collection_id": folders[0]}, quantity=2)
    tick(app_client, user["id"])
    changed = copy.deepcopy(state["snapshot"])
    changed["generation"]["collection_id"] = folders[1]
    rejected = command(app_client, "/apply", expected_revision=state["revision"], snapshot=changed)
    assert rejected.status_code == 409
    changed["generation"]["collection_id"] = folders[0]
    changed["max_generations"] = 1
    limited = command(
        app_client, "/apply", expected_revision=state["revision"], snapshot=changed
    ).json()
    assert limited["accepted_count"] == 2
    assert limited["enabled"] is False
    assert limited["remaining"] == 0
    with app_client.app.state.container.db.session_factory() as session:
        assert set(session.scalars(select(Generation.collection_id))) == {folders[0]}
    enabled = enable(app_client, generation={**generation, "collection_id": folders[1]})
    assert enabled["accepted_count"] == 0
    assert enabled["snapshot"]["generation"]["collection_id"] == folders[1]
