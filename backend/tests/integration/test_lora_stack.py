from __future__ import annotations

import copy
import json

from app.main import create_app
from app.models import Generation
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.helpers import provision_user, wait_for_status
from tests.integration.test_checkpoint_batch_eta import _moody_payload
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
