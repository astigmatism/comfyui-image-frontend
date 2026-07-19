from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from app.models import (
    Artifact,
    Favorite,
    Generation,
    GenerationStatus,
    GenerationTimingAuditState,
    GenerationTimingProfile,
    User,
    UserPreference,
    WorkflowProfile,
)
from sqlalchemy import MetaData, create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

LEGACY_REVISION = "7c9b2d4e6f81"
HEAD_REVISION = "a8d4e6f2c901"
LEGACY_USER_ID = "00000000-0000-4000-8000-000000000001"
LEGACY_PROFILE_ID = "00000000-0000-4000-8000-000000000002"
LEGACY_GENERATION_ID = "00000000-0000-4000-8000-000000000003"
LEGACY_ARTIFACT_ID = "00000000-0000-4000-8000-000000000004"
LEGACY_FAVORITE_ID = "00000000-0000-4000-8000-000000000005"


def _config(database_path: Path) -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def _insert_populated_legacy_rows(engine: Engine) -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        metadata = MetaData()
        metadata.reflect(
            bind=connection,
            only=(
                "users",
                "user_preferences",
                "workflow_profiles",
                "generations",
                "artifacts",
                "favorites",
            ),
        )
        users = metadata.tables["users"]
        user_preferences = metadata.tables["user_preferences"]
        profiles = metadata.tables["workflow_profiles"]
        generations = metadata.tables["generations"]
        artifacts = metadata.tables["artifacts"]
        favorites = metadata.tables["favorites"]

        connection.execute(
            users.insert(),
            {
                "id": LEGACY_USER_ID,
                "username": "legacy.owner",
                "username_normalized": "legacy.owner",
                "password_hash": "legacy-password-hash",
                "role": "USER",
                "state": "ACTIVE",
                "must_change_password": False,
                "is_bootstrap": False,
                "session_epoch": 3,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            user_preferences.insert(),
            {
                "user_id": LEGACY_USER_ID,
                "gallery_scale": 73,
                "updated_at": now,
            },
        )
        connection.execute(
            profiles.insert(),
            {
                "id": LEGACY_PROFILE_ID,
                "identity_key": "legacy-workflow:1",
                "basename": "Legacy Workflow",
                "workflow_id": "legacy-workflow",
                "display_name": "Legacy Workflow",
                "workflow_version": "1",
                "contract_schema_version": "legacy-contract/v1",
                "adapter_version": "1.0.0",
                "ui_graph_sha256": "a" * 64,
                "api_graph_sha256": "b" * 64,
                "contract_sha256": "c" * 64,
                "source_ui_json": {"nodes": [{"id": 1, "type": "LegacyText"}]},
                "source_api_json": {
                    "1": {"class_type": "LegacyText", "inputs": {"value": "legacy prompt"}}
                },
                "manifest_json": {"schema": "legacy-contract/v1"},
                "resolved_contract_json": {
                    "controls": [{"id": "prompt", "type": "text", "required": True}]
                },
                "runtime_snapshot_json": {"object_info": {"LegacyText": {}}},
                "state": "VALID",
                "is_current": True,
                "validated_at": now,
                "last_seen_at": now,
            },
        )
        connection.execute(
            generations.insert(),
            {
                "id": LEGACY_GENERATION_ID,
                "owner_id": LEGACY_USER_ID,
                "status": "SUCCEEDED",
                "queue_seq": 7,
                "correlation_id": "00000000-0000-4000-8000-000000000006",
                "comfyui_client_id": "legacy-client",
                "comfyui_prompt_id": "legacy-native-prompt",
                "workflow_profile_id": LEGACY_PROFILE_ID,
                "workflow_id": "legacy-workflow",
                "workflow_display_name": "Legacy Workflow",
                "workflow_version": "1",
                "contract_schema_version": "legacy-contract/v1",
                "adapter_version": "1.0.0",
                "ui_graph_sha256": "a" * 64,
                "api_graph_sha256": "b" * 64,
                "contract_sha256": "c" * 64,
                "resolved_contract_json": {
                    "controls": [{"id": "prompt", "type": "text", "required": True}]
                },
                "requested_controls_json": {"prompt": "legacy prompt"},
                "effective_controls_json": {"prompt": "legacy prompt", "seed": 42},
                "resolved_seeds_json": {"seed": "42"},
                "selected_preset": None,
                "requested_outputs_json": ["final_image"],
                "final_prompt": "legacy prompt",
                "compiled_graph_json": {
                    "1": {"class_type": "LegacyText", "inputs": {"value": "legacy prompt"}}
                },
                "compiled_graph_sha256": "d" * 64,
                "submitted_graph_json": {
                    "1": {"class_type": "LegacyText", "inputs": {"value": "legacy prompt"}}
                },
                "submitted_graph_sha256": "d" * 64,
                "current_stage_id": "complete",
                "current_stage_label": "Complete",
                "current_stage_sequence": 100,
                "best_available_artifact_id": LEGACY_ARTIFACT_ID,
                "canonical_artifact_id": LEGACY_ARTIFACT_ID,
                "artifact_count": 1,
                "final_artifact_count": 1,
                "error_code": None,
                "error_message": None,
                "internal_diagnostics_json": {"legacy": True},
                "cancel_requested_at": None,
                "pending_delete": False,
                "accepted_at": now,
                "dispatched_at": now,
                "started_at": now,
                "completed_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            artifacts.insert(),
            {
                "id": LEGACY_ARTIFACT_ID,
                "generation_id": LEGACY_GENERATION_ID,
                "owner_id": LEGACY_USER_ID,
                "output_id": "final_image",
                "role": "final",
                "kind": "image",
                "state": "FINAL",
                "sequence": 100,
                "batch_index": 0,
                "parent_artifact_id": None,
                "storage_path": "generations/legacy/final.png",
                "thumbnail_path": "generations/legacy/final.webp",
                "mime_type": "image/png",
                "byte_size": 128,
                "width": 64,
                "height": 64,
                "sha256": "e" * 64,
                "source_node_id": "7",
                "source_filename": "final.png",
                "source_subfolder": "legacy",
                "source_type": "output",
                "usable_on_cancel": True,
                "usable_on_failure": True,
                "canonical": True,
                "best_available": True,
                "emitted_at": now,
                "available_at": now,
            },
        )
        connection.execute(
            favorites.insert(),
            {
                "id": LEGACY_FAVORITE_ID,
                "owner_id": LEGACY_USER_ID,
                "generation_id": LEGACY_GENERATION_ID,
                "created_at": now,
            },
        )


def _assert_populated_head_rows(engine: Engine) -> None:
    with Session(engine) as session:
        user = session.get(User, LEGACY_USER_ID)
        preference = session.get(UserPreference, LEGACY_USER_ID)
        profile = session.get(WorkflowProfile, LEGACY_PROFILE_ID)
        generation = session.get(Generation, LEGACY_GENERATION_ID)
        artifact = session.get(Artifact, LEGACY_ARTIFACT_ID)
        favorite = session.get(Favorite, LEGACY_FAVORITE_ID)

        assert user is not None and user.username == "legacy.owner"
        assert preference is not None
        assert preference.gallery_scale == 73
        assert preference.source_ratings_json == {}
        assert profile is not None
        assert profile.instance_id is None
        assert profile.source_key is None
        assert profile.source_id is None
        assert profile.publication_id is None
        assert profile.publication_schema is None
        assert profile.manifest_sha256 is None
        assert profile.published_at is None
        assert profile.warnings_json == []
        assert profile.readiness == "ready"
        assert profile.source_ui_json["nodes"][0]["type"] == "LegacyText"

        assert generation is not None
        assert generation.status == GenerationStatus.SUCCEEDED
        assert generation.workflow_profile_id == profile.id
        assert generation.owner_id == user.id
        assert generation.final_prompt == "legacy prompt"
        assert generation.effective_controls_json == {"prompt": "legacy prompt", "seed": 42}
        assert generation.generation_source_json == {}
        assert generation.raw_history_json == {}
        assert generation.declared_outputs_json == {}
        assert generation.unmapped_outputs_json == {}
        assert generation.result_warnings_json == []
        assert generation.result_errors_json == []
        assert generation.comfyui_status_json == {}
        assert generation.progress_json is None
        assert session.scalar(select(func.count()).select_from(GenerationTimingProfile)) == 0
        assert session.scalar(select(func.count()).select_from(GenerationTimingAuditState)) == 0

        assert artifact is not None
        assert artifact.generation_id == generation.id
        assert artifact.owner_id == user.id
        assert artifact.canonical is True
        assert favorite is not None
        assert favorite.generation_id == generation.id
        assert favorite.owner_id == user.id

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def _assert_populated_legacy_rows(engine: Engine) -> None:
    metadata = MetaData()
    metadata.reflect(
        bind=engine,
        only=(
            "users",
            "user_preferences",
            "workflow_profiles",
            "generations",
            "artifacts",
            "favorites",
        ),
    )
    users = metadata.tables["users"]
    user_preferences = metadata.tables["user_preferences"]
    profiles = metadata.tables["workflow_profiles"]
    generations = metadata.tables["generations"]
    artifacts = metadata.tables["artifacts"]
    favorites = metadata.tables["favorites"]
    statement = (
        select(
            users.c.username,
            profiles.c.display_name,
            generations.c.final_prompt,
            artifacts.c.storage_path,
            favorites.c.id.label("favorite_id"),
        )
        .select_from(
            generations.join(users, generations.c.owner_id == users.c.id)
            .join(profiles, generations.c.workflow_profile_id == profiles.c.id)
            .join(artifacts, artifacts.c.generation_id == generations.c.id)
            .join(favorites, favorites.c.generation_id == generations.c.id)
        )
        .where(generations.c.id == LEGACY_GENERATION_ID)
    )
    with engine.connect() as connection:
        row = connection.execute(statement).mappings().one()
        assert dict(row) == {
            "username": "legacy.owner",
            "display_name": "Legacy Workflow",
            "final_prompt": "legacy prompt",
            "storage_path": "generations/legacy/final.png",
            "favorite_id": LEGACY_FAVORITE_ID,
        }
        assert (
            connection.execute(
                select(user_preferences.c.gallery_scale).where(
                    user_preferences.c.user_id == LEGACY_USER_ID
                )
            ).scalar_one()
            == 73
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_migration_up_down_up_cycle(settings_factory) -> None:
    settings = settings_factory()
    assert settings.database_path is not None
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    config = _config(settings.database_path)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{settings.database_path}")
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == HEAD_REVISION
    assert {
        "users",
        "user_preferences",
        "generations",
        "generation_timing_audit_state",
        "generation_timing_profiles",
        "artifacts",
        "workflow_profiles",
        "favorites",
    }.issubset(set(inspect(engine).get_table_names()))
    assert "source_ratings_json" in {
        column["name"] for column in inspect(engine).get_columns("user_preferences")
    }
    assert "ix_generations_timing_audit" in {
        index["name"] for index in inspect(engine).get_indexes("generations")
    }

    command.downgrade(config, "base")
    assert "users" not in inspect(engine).get_table_names()

    command.upgrade(config, "head")
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == HEAD_REVISION
    engine.dispose()


def test_populated_legacy_database_survives_publication_migration_round_trip(
    settings_factory,
) -> None:
    settings = settings_factory()
    assert settings.database_path is not None
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    config = _config(settings.database_path)

    command.upgrade(config, LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{settings.database_path}")
    assert "generation_source_json" not in {
        column["name"] for column in inspect(engine).get_columns("generations")
    }
    _insert_populated_legacy_rows(engine)
    _assert_populated_legacy_rows(engine)
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{settings.database_path}")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            HEAD_REVISION
        )
    _assert_populated_head_rows(engine)
    engine.dispose()

    command.downgrade(config, LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{settings.database_path}")
    assert "generation_source_json" not in {
        column["name"] for column in inspect(engine).get_columns("generations")
    }
    assert "generation_timing_profiles" not in inspect(engine).get_table_names()
    assert "generation_timing_audit_state" not in inspect(engine).get_table_names()
    _assert_populated_legacy_rows(engine)
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{settings.database_path}")
    _assert_populated_head_rows(engine)
    engine.dispose()
