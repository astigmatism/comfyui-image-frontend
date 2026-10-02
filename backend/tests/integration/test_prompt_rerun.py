from __future__ import annotations

from uuid import uuid4

import pytest
from app.main import create_app
from app.models import Collection, Generation, GenerationSubmission, PromptRerunRun
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.conftest import csrf
from tests.helpers import (
    create_generation,
    first_profile,
    login_ready_admin,
    provision_user,
    restore_cookie,
)
from tests.integration.test_auto_generation import enable
from tests.integration.test_checkpoint_batch_eta import _moody_payload, _moody_profile
from tests.publication_fixtures import build_publication_bundle


def folder(client, name, parent_id=None):
    response = client.post(
        "/api/collections",
        headers={"X-CSRF-Token": csrf(client)},
        json={"name": name, "parent_id": parent_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def move(client, generation_id, collection_id):
    response = client.post(
        f"/api/generations/{generation_id}/move",
        headers={"X-CSRF-Token": csrf(client)},
        json={"collection_id": collection_id},
    )
    assert response.status_code == 200, response.text


def preview(client, **selection):
    return client.post(
        "/api/gallery/prompt-rerun/preview",
        headers={"X-CSRF-Token": csrf(client)},
        json=selection,
    )


def rerun(client, body, key=None):
    return client.post(
        "/api/gallery/prompt-rerun",
        headers={"X-CSRF-Token": csrf(client), "Idempotency-Key": key or str(uuid4())},
        json=body,
    )


def body(client, generation_ids=(), collection_ids=(), **overrides):
    profile = first_profile(client)
    data = {
        "generation_ids": list(generation_ids),
        "collection_ids": list(collection_ids),
        "folder_name": "Favorites re-run",
        "source_key": profile["source_key"],
        "revision": profile["revision"],
        "parameters": {"width": 640, "height": 768, "enable_seedvr2_upscale": False},
    }
    data.update(overrides)
    return data


def rows(client, model, *where):
    with client.app.state.container.db.session_factory() as session:
        return list(session.scalars(select(model).where(*where)))


def test_preview_counts_direct_cards_nested_folders_duplicates(app_client):
    provision_user(app_client)
    first = create_generation(app_client, "red lighthouse")
    second = create_generation(app_client, "red lighthouse")
    third = create_generation(app_client, "blue harbor")
    outer = folder(app_client, "Outer")
    inner = folder(app_client, "Inner", outer["id"])
    move(app_client, third["id"], inner["id"])

    response = preview(
        app_client, generation_ids=[first["id"], second["id"]], collection_ids=[outer["id"]]
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["generation_count"] == 3
    assert result["unique_prompt_count"] == 2
    assert result["duplicate_count"] == 1
    assert result["skipped_count"] == 0
    assert [item["excerpt"] for item in result["prompts"]] == ["red lighthouse", "blue harbor"]
    assert result["prompts"][0]["width"] == 512

    assert preview(app_client).status_code == 422
    assert app_client.post(
        "/api/gallery/prompt-rerun/preview", json={"generation_ids": [first["id"]]}
    ).status_code in {401, 403}


def test_rerun_creates_folder_and_queues_exact_prompts(app_client):
    provision_user(app_client)
    parent = folder(app_client, "Parent")
    prompts = ["  first exact\nprompt ", "second prompt", "first exact\nprompt"]
    sources = [create_generation(app_client, text) for text in prompts]
    request = body(app_client, [item["id"] for item in sources], quantity=2, skip_duplicates=False)
    request["parent_collection_id"] = parent["id"]
    key = str(uuid4())
    response = rerun(app_client, request, key)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["collection"]["name"] == "Favorites re-run"
    assert result["collection"]["parent_id"] == parent["id"]
    assert result["prompt_count"] == 3
    assert result["planned_count"] == 6
    assert all(item["error"] is None for item in result["items"])

    created = rows(app_client, Generation, Generation.collection_id == result["collection"]["id"])
    assert sorted(item.final_prompt for item in created) == sorted(prompts * 2)
    for item in created:
        assert item.prompt_assistant_json is None
        assert item.effective_controls_json["width"] == 640
        assert item.effective_controls_json["height"] == 768
    seeds = {item.resolved_seeds_json["seed"] for item in created}
    assert len(seeds) == 6

    recalled = app_client.get(f"/api/generations/{created[0].id}/recall").json()
    assert recalled["prompt_assistant"] is None

    # A replay returns the same folder and jobs without creating anything.
    replay = rerun(app_client, request, key)
    assert replay.status_code == 201, replay.text
    assert replay.json()["collection"]["id"] == result["collection"]["id"]
    assert [item["generation"]["id"] for item in replay.json()["items"]] == [
        item["generation"]["id"] for item in result["items"]
    ]
    assert len(rows(app_client, Collection, Collection.name == "Favorites re-run")) == 1
    lookup = app_client.get(f"/api/generation-submissions/{key}").json()
    assert lookup["endpoint"] == "prompt_rerun"
    assert lookup["result"]["collection"]["id"] == result["collection"]["id"]

    conflict = rerun(app_client, {**request, "folder_name": "Other"}, key)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_rerun_keeps_original_resolution_and_seed(app_client):
    provision_user(app_client)
    source = create_generation(app_client, "seeded prompt", seed="987654321")
    response = rerun(
        app_client,
        body(app_client, [source["id"]], keep_original_resolution=True, seed_mode="original"),
    )
    assert response.status_code == 201, response.text
    [created] = rows(
        app_client, Generation, Generation.collection_id == response.json()["collection"]["id"]
    )
    assert created.effective_controls_json["width"] == 512
    assert created.effective_controls_json["height"] == 512
    assert created.resolved_seeds_json["seed"] == "987654321"


def test_rerun_rejections_create_nothing(app_client):
    user, cookie = provision_user(app_client)
    source = create_generation(app_client, "kept")
    before = rows(app_client, Generation)

    stale = body(app_client, [source["id"]])
    stale["revision"] = {**stale["revision"], "api_sha256": "0" * 64}
    response = rerun(app_client, stale)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "source_republished"

    response = rerun(app_client, body(app_client, [source["id"]], parent_collection_id="missing"))
    assert response.status_code == 404

    missing_protocol = app_client.post(
        "/api/gallery/prompt-rerun",
        headers={"X-CSRF-Token": csrf(app_client), "X-CIF-Generation-Protocol": ""},
        json=body(app_client, [source["id"]]),
    )
    assert missing_protocol.status_code in {400, 409}

    no_csrf = app_client.post(
        "/api/gallery/prompt-rerun",
        headers={"Idempotency-Key": str(uuid4())},
        json=body(app_client, [source["id"]]),
    )
    assert no_csrf.status_code in {401, 403}

    oversized = body(
        app_client, [source["id"]], quantity=16, model_variants=[{}], parameters={"steps": 1}
    )
    oversized["model_variants"] = [{"enable_seedvr2_upscale": str(i)} for i in range(17)]
    response = rerun(app_client, oversized)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "prompt_rerun_too_large"

    invalid = body(app_client, [source["id"]], parameters={"width": 3})
    response = rerun(app_client, invalid)
    assert response.status_code == 422, response.text

    assert len(rows(app_client, Generation)) == len(before)
    assert rows(app_client, Collection) == []
    assert (
        rows(app_client, GenerationSubmission, GenerationSubmission.endpoint == "prompt_rerun")
        == []
    )

    enable(app_client)
    response = rerun(app_client, body(app_client, [source["id"]]))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "auto_generation_enabled"
    del user, cookie


def test_rerun_is_owner_scoped_even_for_admin(app_client):
    _, owner_cookie = provision_user(app_client, username="owner.rerun")
    source = create_generation(app_client, "private prompt")
    login_ready_admin(app_client)
    admin_folder = folder(app_client, "Administrator folder")
    response = rerun(app_client, body(app_client, [source["id"]]))
    assert response.status_code == 404
    assert preview(app_client, generation_ids=[source["id"]]).status_code == 404
    restore_cookie(app_client, owner_cookie)
    response = rerun(
        app_client, body(app_client, [source["id"]], parent_collection_id=admin_folder["id"])
    )
    assert response.status_code == 404
    with app_client.app.state.container.db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Collection)) == 1
        assert session.scalar(select(func.count()).select_from(Generation)) == 1


@pytest.mark.parametrize("refine", [False, True])
def test_rerun_fans_out_across_checkpoints(fake_state, settings_factory, refine):
    fake_state.workflow_files = dict(build_publication_bundle("moody").files)
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="rerun.checkpoints")
        source = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=_moody_payload(client, "checkpoint prompt"),
        )
        assert source.status_code == 201, source.text
        profile = _moody_profile(client)
        response = rerun(
            client,
            {
                "generation_ids": [source.json()["id"]],
                "folder_name": "Checkpoint comparison",
                "source_key": profile["source_key"],
                "revision": profile["revision"],
                "parameters": {"width": 512, "height": 512},
                "model_variants": [{"checkpoint": "v4_int8"}, {"checkpoint": "v4_bf16"}],
                "quantity": 3,
                **({"refinement": {"creative_direction": "at dusk"}} if refine else {}),
            },
        )
        assert response.status_code == (202 if refine else 201), response.text
        if refine:
            owner = rows(client, PromptRerunRun)[0].owner_id
            client.portal.call(
                client.app.state.container.prompt_generation.advance, f"rerun:{owner}"
            )
            assert len(fake_state.ollama_calls) == 1
        created = rows(
            client, Generation, Generation.collection_id == response.json()["collection"]["id"]
        )
        assert sorted(item.effective_controls_json["checkpoint"] for item in created) == [
            "v4_bf16",
            "v4_bf16",
            "v4_bf16",
            "v4_int8",
            "v4_int8",
            "v4_int8",
        ]
        assert {item.final_prompt for item in created} == {
            "checkpoint prompt, at dusk" if refine else "checkpoint prompt"
        }
