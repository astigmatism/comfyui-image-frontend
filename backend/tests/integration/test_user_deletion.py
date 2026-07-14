from __future__ import annotations

from app.main import create_app
from app.models import (
    Artifact,
    Generation,
    GenerationEvent,
    GenerationUpload,
    PromptAssistantRun,
    Upload,
    User,
    UserPreference,
)
from app.models import (
    Session as UserSession,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.conftest import change_password, create_user, csrf, login
from tests.fake_services import make_png
from tests.helpers import (
    ADMIN_PASSWORD,
    ADMIN_TEMP,
    USER_PASSWORD,
    USER_TEMP,
    generation_payload,
    restore_cookie,
    wait_for_generation,
)


def test_administrator_user_deletion_cascades_without_disclosing_content(
    settings_factory, fake_state
) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        login(client, "admin", ADMIN_TEMP)
        change_password(client, ADMIN_PASSWORD)
        target = create_user(client, "delete.user", USER_TEMP)
        admin_cookie = client.cookies.get(settings.session_cookie_name)
        assert admin_cookie

        client.cookies.clear()
        login(client, "delete.user", USER_TEMP)
        change_password(client, USER_PASSWORD)
        user_cookie = client.cookies.get(settings.session_cookie_name)
        assert user_cookie
        upload_response = client.post(
            "/api/uploads/images",
            headers={"X-CSRF-Token": csrf(client)},
            files={"file": ("private.png", make_png("private"), "image/png")},
        )
        assert upload_response.status_code == 200
        upload = upload_response.json()
        compose = client.post(
            "/api/prompt-assistant/compose",
            headers={"X-CSRF-Token": csrf(client)},
            json={
                "mode": "refine",
                "prompt": "slow private image",
                "creative_direction": "dramatic light",
            },
        )
        assert compose.status_code == 200
        payload = generation_payload(
            client,
            compose.json()["prompt"],
            seed=18,
            source_upload_id=upload["id"],
        )
        payload["prompt_assistant_run_id"] = compose.json()["composition_id"]
        accepted = client.post(
            "/api/generations",
            headers={"X-CSRF-Token": csrf(client)},
            json=payload,
        )
        assert accepted.status_code == 201
        generation_id = accepted.json()["id"]
        wait_for_generation(
            client,
            generation_id,
            lambda item: item["status"] == "running" and item["artifact_count"] >= 1,
        )

        container = client.app.state.container
        with container.db.session_factory() as session:
            artifact_paths = [
                path
                for item in session.scalars(
                    select(Artifact).where(Artifact.owner_id == target["id"])
                )
                for path in (item.storage_path, item.thumbnail_path)
                if path
            ]
            upload_paths = [
                item.storage_path
                for item in session.scalars(select(Upload).where(Upload.owner_id == target["id"]))
            ]
        disk_paths = [settings.data_dir / path for path in artifact_paths + upload_paths]
        assert disk_paths and all(path.exists() for path in disk_paths)

        restore_cookie(client, admin_cookie, name=settings.session_cookie_name)
        response = client.delete(
            f"/api/admin/users/{target['id']}",
            headers={"X-CSRF-Token": csrf(client)},
        )
        assert response.status_code == 204
        assert response.content in {b"", b"null"}

        with container.db.session_factory() as session:
            assert session.get(User, target["id"]) is None
            for model, owner_field in (
                (Generation, Generation.owner_id),
                (Artifact, Artifact.owner_id),
                (Upload, Upload.owner_id),
                (PromptAssistantRun, PromptAssistantRun.owner_id),
                (GenerationEvent, GenerationEvent.owner_id),
            ):
                count = session.scalar(
                    select(func.count()).select_from(model).where(owner_field == target["id"])
                )
                assert count == 0
            assert session.get(UserPreference, target["id"]) is None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(UserSession)
                    .where(UserSession.user_id == target["id"])
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GenerationUpload)
                    .join(Generation, Generation.id == GenerationUpload.generation_id)
                    .where(Generation.owner_id == target["id"])
                )
                == 0
            )
        assert all(not path.exists() for path in disk_paths)

        restore_cookie(client, user_cookie, name=settings.session_cookie_name)
        assert client.get("/api/auth/session").json()["authenticated"] is False
