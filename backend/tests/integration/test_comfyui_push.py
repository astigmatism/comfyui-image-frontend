from __future__ import annotations

import json

from app.main import create_app
from app.models import Artifact, AuditLog
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import csrf
from tests.helpers import create_generation, login_ready_admin, provision_user, wait_for_status


def _push(client: TestClient, artifact_id: str, body: dict[str, object] | None = None):
    return client.post(
        f"/api/artifacts/{artifact_id}/push-to-comfyui",
        headers={"X-CSRF-Token": csrf(client)},
        json={} if body is None else body,
    )


def _finished_image(client: TestClient, prompt: str) -> dict[str, object]:
    generation = create_generation(client, prompt)
    complete = wait_for_status(client, str(generation["id"]), "succeeded")
    images = [item for item in complete["artifacts"] if item["kind"] == "image"]
    assert images, complete["artifacts"]
    return images[-1]


def test_a_gallery_image_lands_in_the_input_folder_of_the_service_that_made_it(
    settings_factory, fake_state
) -> None:
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client, username="push.artist")
        artifact = _finished_image(client, "push into comfy input")

        pushed = _push(client, str(artifact["id"]))

        assert pushed.status_code == 200, pushed.text
        body = pushed.json()
        assert body["artifact_id"] == artifact["id"]
        assert body["instance_id"] == "test-instance"
        assert body["instance_label"] == "Primary"
        assert body["subfolder"] == ""
        assert body["name"].endswith(".png")
        assert body["listed_in_input_node"] is False
        assert "input" in str(body["warning"])
        # The browser is told where inside ComfyUI the file is, never how to reach it.
        assert "http" not in json.dumps(body)
        assert fake_state.uploaded == [body["name"]]

        container = client.app.state.container
        with container.db.session_factory() as session:
            row = session.get(Artifact, artifact["id"])
            assert row is not None
            stored = container.assets.read(row.storage_path)
            audit = session.scalar(
                select(AuditLog).where(AuditLog.action == "comfyui_input_pushed")
            )
            assert audit is not None
            assert audit.metadata_json["name"] == body["name"]
            assert body["byte_size"] == row.byte_size
        assert fake_state.output_files[(body["name"], "", "input")] == stored


def test_a_second_copy_keeps_the_first_file_and_reports_the_name_comfyui_chose(
    settings_factory, fake_state
) -> None:
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client, username="push.collision")
        artifact = _finished_image(client, "push twice")

        first = _push(client, str(artifact["id"])).json()
        second = _push(client, str(artifact["id"])).json()

        assert second["name"] != first["name"]
        assert second["name"].startswith(first["name"].rsplit(".", 1)[0])
        assert sorted(fake_state.uploaded) == sorted([first["name"], second["name"]])


def test_the_prompt_service_is_not_offered_as_a_home_for_image_inputs(
    settings_factory, fake_services, fake_state
) -> None:
    del fake_state
    settings = settings_factory(
        enable_background_worker=True,
        comfyui_additional_instances=[
            {
                "id": "promptgen",
                "label": "CPU Prompt Service",
                "base_url": fake_services.base_url,
            }
        ],
        comfyui_text_instance_id="promptgen",
    )
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="push.promptgen")
        artifact = _finished_image(client, "not on the cpu box")

        rejected = _push(client, str(artifact["id"]), {"instance_id": "promptgen"})

        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "comfyui_input_instance_required"
        assert _push(client, str(artifact["id"])).status_code == 200


def test_another_owners_image_and_a_missing_image_are_both_absent(
    settings_factory, fake_state
) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client, username="push.owner")
        artifact = _finished_image(client, "someone elses image")
        login_ready_admin(client)

        assert _push(client, str(artifact["id"])).status_code == 404
        assert _push(client, "00000000-0000-4000-8000-000000000000").status_code == 404


def test_the_push_requires_request_verification(settings_factory, fake_state) -> None:
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client, username="push.csrf")
        artifact = _finished_image(client, "needs a csrf token")

        response = client.post(f"/api/artifacts/{artifact['id']}/push-to-comfyui", json={})

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_failed"
        assert fake_state.uploaded == []


def test_an_image_too_large_for_the_input_folder_is_refused_without_being_sent(
    settings_factory, fake_state
) -> None:
    with TestClient(
        create_app(settings_factory(enable_background_worker=True, comfyui_push_max_bytes=1024))
    ) as client:
        provision_user(client, username="push.oversized")
        artifact = _finished_image(client, "too big to send")
        container = client.app.state.container

        def _make_the_record_claim(size: int) -> None:
            with container.db.session_factory() as session:
                row = session.get(Artifact, artifact["id"])
                assert row is not None
                row.byte_size = size
                session.commit()

        # The stored record already says the image is too large: never read it, never send it.
        _make_the_record_claim(5000)
        refused = _push(client, str(artifact["id"]))
        assert refused.status_code == 413
        assert refused.json()["error"]["code"] == "comfyui_push_too_large"

        # A stale record understates the file: reading it is what reveals the truth.
        _make_the_record_claim(500)
        with container.db.session_factory() as session:
            row = session.get(Artifact, artifact["id"])
            assert row is not None
            stored_file = container.assets.open(row.storage_path)
        stored_file.write_bytes(stored_file.read_bytes() + b"\0" * 4096)
        after_read = _push(client, str(artifact["id"]))
        assert after_read.status_code == 413
        assert after_read.json()["error"]["code"] == "comfyui_push_too_large"
        assert fake_state.uploaded == []


def test_a_service_that_cannot_take_the_file_distinguishes_refusal_from_absence(
    settings_factory, fake_state
) -> None:
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client, username="push.refused")
        artifact = _finished_image(client, "the input folder is full")

        fake_state.upload_status = 415
        refused = _push(client, str(artifact["id"]))
        fake_state.upload_status = 500
        unavailable = _push(client, str(artifact["id"]))
        fake_state.upload_status = None
        fake_state.service_available = False
        offline = _push(client, str(artifact["id"]))

        assert refused.status_code == 502
        assert refused.json()["error"]["code"] == "comfyui_upload_failed"
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["code"] == "comfyui_unavailable"
        assert offline.status_code == 503
        assert offline.json()["error"]["code"] == "comfyui_unavailable"
        assert fake_state.uploaded == []
