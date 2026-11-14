from __future__ import annotations

import copy
import json
from functools import partial

from app.main import create_app
from app.models import Generation
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.helpers import provision_user, wait_for_status
from tests.integration.test_checkpoint_batch_eta import _moody_payload, _moody_profile
from tests.publication_fixtures import add_lora_stack, build_publication_bundle


def test_checkpoint_batch_persists_stack_and_recalls_exactly(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client, username="lora.batch")
        stack = [{"id": "b", "strength": 1.25}, {"id": "a", "strength": 0}]
        payload = {
            "items": [
                _moody_payload(
                    client,
                    "ordered LoRA fixture",
                    checkpoint=checkpoint,
                    loras=copy.deepcopy(stack),
                )
                for checkpoint in ("v4_int8", "v4_bf16")
            ]
        }
        response = client.post(
            "/api/generations/batch", headers={"X-CSRF-Token": csrf(client)}, json=payload
        )
        assert response.status_code == 201, response.text
        for item in response.json()["items"]:
            assert item.get("error") is None, item
            generation_id = item["generation"]["id"]
            wait_for_status(client, generation_id, "succeeded")
            with client.app.state.container.db.session_factory() as session:
                generation = session.get(Generation, generation_id)
                assert generation.requested_controls_json["loras"] == stack
                assert generation.effective_controls_json["loras"] == stack
                assert json.loads(generation.compiled_graph_json["99"]["inputs"]["value"]) == stack
            recalled = client.get(f"/api/generations/{generation_id}/recall").json()
            assert recalled["parameters"]["loras"] == stack
            assert next(item for item in recalled["input_definitions"] if item["id"] == "loras")[
                "items"
            ] == [{"id": "a", "label": "Alpha"}, {"id": "b", "label": "Beta"}]


def test_cached_lora_publication_can_queue_after_offline_restart(fake_state, settings_factory):
    from tests.helpers import restore_cookie

    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    settings = settings_factory()
    with TestClient(create_app(settings)) as first:
        _, cookie = provision_user(first, username="lora.cached")
        payload = _moody_payload(
            first,
            "cached LoRA",
            checkpoint="v4_int8",
            loras=[{"id": "a", "strength": 1}, {"id": "b", "strength": 0}],
        )
    fake_state.service_available = False
    with TestClient(create_app(settings)) as restarted:
        restore_cookie(restarted, cookie)
        assert not restarted.app.state.container.comfyui.cached_object_info()
        response = restarted.post(
            "/api/generations", headers={"X-CSRF-Token": csrf(restarted)}, json=payload
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "queued"
        assert not fake_state.submitted


def test_a_negative_strength_is_persisted_rejected_and_recalled(fake_state, settings_factory):
    """The Krea 2 wetness slider is loaded at -1.0, so the whole pipeline has to carry it."""

    fake_state.workflow_files = dict(
        build_publication_bundle(
            "moody",
            mutate_artifacts=partial(add_lora_stack, minimum=-1.0, preferred_strength=-1.0),
        ).files
    )
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client, username="lora.signed")
        stack = [{"id": "a", "strength": -1.0}, {"id": "b", "strength": 0}]
        response = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=_moody_payload(
                client, "dry skin", checkpoint="v4_int8", loras=copy.deepcopy(stack)
            ),
        )
        assert response.status_code == 201, response.text
        generation_id = response.json()["id"]
        wait_for_status(client, generation_id, "succeeded")
        with client.app.state.container.db.session_factory() as session:
            generation = session.get(Generation, generation_id)
            assert generation.requested_controls_json["loras"] == stack
            assert generation.effective_controls_json["loras"] == stack
            assert json.loads(generation.compiled_graph_json["99"]["inputs"]["value"]) == stack

        recalled = client.get(f"/api/generations/{generation_id}/recall").json()
        assert recalled["parameters"]["loras"] == stack
        definition = next(item for item in recalled["input_definitions"] if item["id"] == "loras")
        # The published hint travels with the record, so an old generation still says
        # which way this LoRA was pulled when the workflow itself is long gone.
        assert definition["items"][0]["preferred_strength"] == -1.0
        assert definition["items"][1] == {"id": "b", "label": "Beta"}

        # And the live workflow hands the browser the range it may offer.
        source_key = _moody_profile(client)["source_key"]
        control = next(
            item
            for item in client.get(f"/api/workflows/{source_key}").json()["interface"]["inputs"]
            if item["id"] == "loras"
        )
        assert control["minimum"] == -1.0
        assert control["items"][0]["preferred_strength"] == -1.0
        assert "filename" not in json.dumps(control)

        for strength, reason in ((-1.05, "from -1.0 to 2"), (-0.73, "increments of 0.05")):
            rejected = client.post(
                "/api/generations",
                headers={"X-CSRF-Token": csrf(client)},
                json=_moody_payload(
                    client,
                    "off the grid",
                    checkpoint="v4_int8",
                    loras=[{"id": "a", "strength": strength}, {"id": "b", "strength": 0}],
                ),
            )
            assert rejected.status_code == 422, rejected.text
            assert reason in rejected.json()["error"]["fields"]["loras"]
