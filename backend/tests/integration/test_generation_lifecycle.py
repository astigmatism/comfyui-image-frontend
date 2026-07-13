from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import csrf
from tests.helpers import (
    create_generation,
    generation_payload,
    provision_user,
    wait_for_generation,
    wait_for_status,
)

TERMINAL = {
    "succeeded",
    "cancelled_with_artifacts",
    "cancelled_without_artifacts",
    "failed_with_artifacts",
    "failed_without_artifacts",
    "interrupted",
}


def test_progressive_success_multiple_outputs_and_exact_recall(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client)
        accepted = create_generation(client, "multi gallery image", seed="random")
        generation_id = accepted["id"]
        assert accepted["status"] == "queued"

        progressive = wait_for_generation(
            client,
            generation_id,
            lambda item: item["artifact_count"] >= 1 and item["status"] not in TERMINAL,
        )
        assert progressive["canonical_artifact_id"] is None
        assert progressive["best_available_artifact_id"] is not None

        complete = wait_for_status(client, generation_id, "succeeded")
        assert complete["artifact_count"] == 3
        assert complete["final_artifact_count"] == 2
        assert complete["canonical_artifact_id"] is not None
        assert complete["best_available_artifact_id"] == complete["canonical_artifact_id"]
        final_artifacts = [item for item in complete["artifacts"] if item["canonical"]]
        assert len(final_artifacts) == 2
        assert all(item["state"] == "final" for item in final_artifacts)
        assert [item["batch_index"] for item in final_artifacts] == [0, 1]

        gallery = client.get("/api/generations").json()["items"]
        assert [item["id"] for item in gallery].count(generation_id) == 1
        assert gallery[0]["display_artifact"]["state"] == "final"

        detail = client.get(f"/api/generations/{generation_id}").json()
        seed = detail["resolved_seeds"]["generation.seed"]
        assert isinstance(seed, int)
        assert detail["final_prompt"] == "multi gallery image"
        recall = client.get(f"/api/generations/{generation_id}/recall")
        assert recall.status_code == 200
        recalled = recall.json()
        assert recalled["available"] is True
        assert recalled["controls"]["generation.seed"] == seed
        assert recalled["controls"]["prompt.text"] == "multi gallery image"
        assert recalled["identity"]["workflow_id"] == "fake-progressive-v1"

        artifact = final_artifacts[0]
        content = client.get(artifact["content_url"])
        thumbnail = client.get(artifact["thumbnail_url"])
        assert content.status_code == 200 and content.headers["content-type"] == "image/png"
        assert thumbnail.status_code == 200 and thumbnail.headers["content-type"] == "image/webp"


def test_cancel_after_checkpoint_and_failure_keep_best_available_and_recall(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client)
        cancellable = create_generation(client, "slow cancellation sample", seed=101)
        checkpoint = wait_for_generation(
            client,
            cancellable["id"],
            lambda item: item["artifact_count"] >= 1 and item["status"] == "running",
        )
        assert checkpoint["canonical_artifact_id"] is None
        cancel = client.post(
            f"/api/generations/{cancellable['id']}/cancel",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancel_requested"
        cancelled = wait_for_status(client, cancellable["id"], "cancelled_with_artifacts")
        assert cancelled["canonical_artifact_id"] is None
        assert cancelled["best_available_artifact_id"] is not None
        best = next(item for item in cancelled["artifacts"] if item["best_available"])
        assert best["state"] == "best_available"
        assert client.get(f"/api/generations/{cancellable['id']}/recall").json()["available"] is True

        failed_attempt = create_generation(client, "please fail after checkpoint", seed=202)
        failed = wait_for_status(client, failed_attempt["id"], "failed_with_artifacts")
        assert failed["canonical_artifact_id"] is None
        assert failed["best_available_artifact_id"] is not None
        assert failed["error_code"] == "execution_failed"
        assert client.get(f"/api/generations/{failed_attempt['id']}/recall").json()["available"] is True

        page = client.get("/api/generations").json()["items"]
        statuses = {item["id"]: item["status"] for item in page}
        assert statuses[cancellable["id"]] == "cancelled_with_artifacts"
        assert statuses[failed_attempt["id"]] == "failed_with_artifacts"


def test_validation_rejection_creates_no_record_and_rapid_submissions_are_distinct(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(settings)) as client:
        provision_user(client)
        invalid = generation_payload(client, "bad size", seed=1)
        invalid["controls"]["size.resolution"] = {"width": 4096, "height": 512}
        response = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=invalid,
        )
        assert response.status_code == 422
        assert client.get("/api/generations").json()["items"] == []

        records = [create_generation(client, f"queued {index}", seed=index) for index in range(5)]
        assert len({item["id"] for item in records}) == 5
        page = client.get("/api/generations?limit=60").json()["items"]
        assert len(page) == 5
        assert all(item["status"] == "queued" for item in page)


def test_generation_deletion_removes_application_files_only(settings_factory, fake_state) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client)
        composition = client.post(
            "/api/prompt-assistant/compose",
            headers={"X-CSRF-Token": csrf(client)},
            json={
                "mode": "refine",
                "prompt": "deletable image",
                "creative_direction": "soft studio light",
            },
        )
        assert composition.status_code == 200, composition.text
        payload = generation_payload(
            client,
            composition.json()["prompt"],
            seed=303,
        )
        payload["prompt_assistant_run_id"] = composition.json()["composition_id"]
        accepted = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=payload,
        )
        assert accepted.status_code == 201, accepted.text
        generation = accepted.json()
        complete = wait_for_status(client, generation["id"], "succeeded")
        paths: list[Path] = []
        container = client.app.state.container
        with container.db.session_factory() as session:
            from app.models import Artifact, PromptAssistantRun
            from sqlalchemy import select

            artifacts = list(
                session.scalars(select(Artifact).where(Artifact.generation_id == generation["id"]))
            )
            for artifact in artifacts:
                paths.append(settings.data_dir / artifact.storage_path)
                if artifact.thumbnail_path:
                    paths.append(settings.data_dir / artifact.thumbnail_path)
            assert session.scalar(
                select(PromptAssistantRun).where(
                    PromptAssistantRun.generation_id == generation["id"]
                )
            ) is not None
        assert paths and all(path.exists() for path in paths)
        submitted_before = len(fake_state.submitted)
        response = client.delete(
            f"/api/generations/{generation['id']}",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert response.status_code == 204
        assert client.get(f"/api/generations/{generation['id']}").status_code == 404
        assert all(not path.exists() for path in paths)
        assert len(fake_state.submitted) == submitted_before
        with container.db.session_factory() as session:
            from app.models import PromptAssistantRun
            from sqlalchemy import select

            assert session.scalar(
                select(PromptAssistantRun).where(
                    PromptAssistantRun.id == composition.json()["composition_id"]
                )
            ) is None


def test_artifact_persistence_failure_is_not_reported_as_success(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="archive.failure")
        generation = create_generation(client, "slow archive unavailable", seed=909)
        checkpoint = wait_for_generation(
            client,
            generation["id"],
            lambda item: item["status"] == "running" and item["artifact_count"] >= 1,
        )
        assert checkpoint["canonical_artifact_id"] is None
        fake_state.fail_retrieval = True
        failed = wait_for_status(client, generation["id"], "failed_with_artifacts", timeout=10)
        assert failed["error_code"] == "artifact_persistence_failed"
        assert failed["canonical_artifact_id"] is None
        assert failed["best_available_artifact_id"] is not None
        assert any(item["type"] == "artifact.persistence_failed" for item in failed["events"])


def test_deleting_queued_and_running_generations_reconciles_before_cleanup(
    settings_factory, fake_state
) -> None:
    import time

    queued_settings = settings_factory(enable_background_worker=False)
    with TestClient(create_app(queued_settings)) as client:
        provision_user(client, username="queued.delete")
        queued = create_generation(client, "delete before dispatch", seed=811)
        deleted = client.delete(
            f"/api/generations/{queued['id']}",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert deleted.status_code == 204
        assert client.get(f"/api/generations/{queued['id']}").status_code == 404
        assert fake_state.submitted == []

    running_settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(running_settings)) as client:
        provision_user(client, username="running.delete")
        running = create_generation(client, "slow delete while running", seed=812)
        wait_for_generation(
            client,
            running["id"],
            lambda item: item["status"] == "running" and item["artifact_count"] >= 1,
        )
        requested = client.delete(
            f"/api/generations/{running['id']}",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert requested.status_code == 202
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if client.get(f"/api/generations/{running['id']}").status_code == 404:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("running generation was not deleted after cancellation reconciliation")
