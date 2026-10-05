from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from alembic import command
from alembic.config import Config
from app.models import (
    Artifact,
    Collection,
    CollectionFavorite,
    Favorite,
    Generation,
    GenerationPreparation,
    GenerationRun,
    GenerationStatus,
    GenerationTimingAuditState,
    GenerationTimingProfile,
    PromptAssistantRun,
    PromptGenerationRun,
    User,
    UserPreference,
    WorkflowProfile,
)
from sqlalchemy import MetaData, create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

LEGACY_REVISION = "7c9b2d4e6f81"
HEAD_REVISION = "b2f4d8a6c931"
LEGACY_USER_ID = "00000000-0000-4000-8000-000000000001"
LEGACY_PROFILE_ID = "00000000-0000-4000-8000-000000000002"
LEGACY_GENERATION_ID = "00000000-0000-4000-8000-000000000003"
LEGACY_ARTIFACT_ID = "00000000-0000-4000-8000-000000000004"
LEGACY_FAVORITE_ID = "00000000-0000-4000-8000-000000000005"
LEGACY_PROMPT_RUN_ID = "00000000-0000-4000-8000-000000000007"


def _config(database_path: Path) -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def test_checkpoint_ranks_reset_once_and_historical_identity_is_backfilled(tmp_path):
    import json

    from app.domain.checkpoint_identity import checkpoint_identity_v1

    path = tmp_path / "checkpoint-ranks.db"
    config = _config(path)
    command.upgrade(config, LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{path}")
    _insert_populated_legacy_rows(engine)
    engine.dispose()
    command.upgrade(config, "b28a6f1d903e")
    engine = create_engine(f"sqlite:///{path}")
    declaration = {
        "id": "checkpoint",
        "type": "choice",
        "semantic_role": "model",
        "bindings": [{"node_id": "42", "input": "value"}],
    }
    graph = {
        "42": {
            "inputs": {
                "value": "alias",
                "options_json": json.dumps(
                    [
                        {"value": "alias", "binding": "models/private.safetensors"},
                    ]
                ),
            }
        }
    }
    with engine.begin() as connection:
        metadata = MetaData()
        metadata.reflect(bind=connection, only=["generations", "user_preferences"])
        connection.execute(
            metadata.tables["generations"]
            .update()
            .values(
                resolved_contract_json={"inputs": [declaration]},
                effective_controls_json={"checkpoint": "alias"},
                compiled_graph_json=graph,
            )
        )
        connection.execute(
            metadata.tables["user_preferences"]
            .update()
            .values(
                checkpoint_tiers_json={"old-workflow": {"checkpoint": {"top_picks": ["alias"]}}},
                revision=8,
            )
        )
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    expected_id = checkpoint_identity_v1(declaration, "alias", graph, "another-workflow")
    with Session(engine) as session:
        generation = session.get(Generation, LEGACY_GENERATION_ID)
        assert generation.checkpoint_id == expected_id
        assert generation.compiled_graph_json == graph
        assert session.get(Artifact, LEGACY_ARTIFACT_ID) is not None
        preference = session.get(UserPreference, LEGACY_USER_ID)
        assert preference.checkpoint_tiers_json == {}
        assert preference.revision == 9
        preference.checkpoint_tiers_json = {"A": [expected_id]}
        session.commit()
    command.upgrade(config, "head")
    with Session(engine) as session:
        assert session.get(UserPreference, LEGACY_USER_ID).checkpoint_tiers_json == {
            "A": [expected_id],
        }
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_shared_lora_library_backfills_identities_thumbnails_and_usage(tmp_path):
    import json

    from app.domain.lora_identity import lora_identity_v1
    from app.models import LoraLibraryImage

    path = tmp_path / "lora-library.db"
    config = _config(path)
    command.upgrade(config, LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{path}")
    _insert_populated_legacy_rows(engine)
    engine.dispose()
    command.upgrade(config, "c73e2a9140bd")
    engine = create_engine(f"sqlite:///{path}")
    catalog = [
        {"id": "lora_a", "label": "Alpha", "filename": "cif-managed/a.safetensors"},
        {"id": "lora_b", "label": "Beta", "filename": "cif-managed/b.safetensors"},
    ]
    declaration = {
        "id": "loras",
        "type": "lora_stack",
        "items": [{"id": "lora_a", "label": "Alpha"}, {"id": "lora_b", "label": "Beta"}],
        "bindings": [{"node_id": "906", "input": "value"}],
    }
    graph = {"906": {"class_type": "CIFLoraStack", "inputs": {"catalog_json": json.dumps(catalog)}}}
    binding = sha256(b"cif-managed/a.safetensors").hexdigest()
    with engine.begin() as connection:
        metadata = MetaData()
        metadata.reflect(bind=connection, only=["generations", "workflow_profiles", "lora_images"])
        connection.execute(
            metadata.tables["workflow_profiles"].update().values(source_api_json=graph)
        )
        connection.execute(
            metadata.tables["generations"]
            .update()
            .values(
                resolved_contract_json={"inputs": [declaration]},
                effective_controls_json={
                    "loras": [{"id": "lora_b", "strength": 0.8}, {"id": "lora_a", "strength": 0}]
                },
                compiled_graph_json=graph,
            )
        )
        images = metadata.tables["lora_images"]
        for workflow_key, path_name, updated in (
            ("advanced", "lora-images/old.webp", datetime(2026, 9, 1, tzinfo=UTC)),
            ("minimal", "lora-images/new.webp", datetime(2026, 10, 1, tzinfo=UTC)),
        ):
            connection.execute(
                images.insert().values(
                    workflow_key=workflow_key,
                    control_id="loras",
                    item_id="lora_a",
                    binding_hash=binding,
                    revision=3,
                    storage_path=path_name,
                    updated_at=updated,
                )
            )
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as session:
        alpha = lora_identity_v1("cif-managed/a.safetensors")
        image = session.get(LoraLibraryImage, alpha)
        assert image is not None and image.storage_path == "lora-images/new.webp"
        assert image.revision == 3
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
    command.downgrade(config, "c73e2a9140bd")
    engine = create_engine(f"sqlite:///{path}")
    tables = inspect(engine).get_table_names()
    assert "lora_library_images" not in tables and "generation_loras" not in tables
    assert "lora_images" in tables
    engine.dispose()


def test_retained_prompt_migration_preserves_existing_preparations(tmp_path):
    path = tmp_path / "retained-prompt-upgrade.db"
    config = _config(path)
    command.upgrade(config, LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{path}")
    _insert_populated_legacy_rows(engine)
    engine.dispose()
    command.upgrade(config, "a4e1c7b9d206")
    engine = create_engine(f"sqlite:///{path}")
    original = {
        "generation": {"parameters": {"seed": "123"}},
        "prompt_generation": {"source_key": "text"},
    }
    with Session(engine) as session:
        text_run = PromptGenerationRun(
            owner_id=LEGACY_USER_ID,
            profile_id=LEGACY_PROFILE_ID,
            instance_id="text",
            queue_seq=1,
            status="succeeded",
            prompt="saved text",
            request_json={},
            contract_json={},
            compiled_graph_json={},
            compiled_graph_sha256="a" * 64,
        )
        activity = GenerationRun(owner_id=LEGACY_USER_ID, total_count=1)
        session.add_all([text_run, activity])
        session.flush()
        text_id = text_run.id
        metadata = MetaData()
        metadata.reflect(bind=session.connection(), only=["generation_preparations"])
        session.execute(
            metadata.tables["generation_preparations"].insert(),
            {
                "id": "legacy-preparation",
                "group_id": "legacy-group",
                "owner_id": LEGACY_USER_ID,
                "profile_id": LEGACY_PROFILE_ID,
                "prompt_run_id": text_id,
                "activity_run_id": activity.id,
                "position": 0,
                "status": "ready",
                "prompt": "saved text",
                "request_json": original,
                "created_at": datetime.now(UTC),
            },
        )
        session.commit()
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as session:
        prepared = session.get(GenerationPreparation, "legacy-preparation")
        assert prepared.prompt_run_id == text_id
        assert prepared.rerun_id is None
        assert prepared.prompt == "saved text"
        assert prepared.request_json == original
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
    command.downgrade(config, "a4e1c7b9d206")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT prompt FROM generation_preparations")).scalar_one()
            == "saved text"
        )
    engine.dispose()


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
                "prompt_assistant_runs",
            ),
        )
        users = metadata.tables["users"]
        user_preferences = metadata.tables["user_preferences"]
        profiles = metadata.tables["workflow_profiles"]
        generations = metadata.tables["generations"]
        artifacts = metadata.tables["artifacts"]
        favorites = metadata.tables["favorites"]
        prompt_assistant_runs = metadata.tables["prompt_assistant_runs"]

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
        connection.execute(
            prompt_assistant_runs.insert(),
            {
                "id": LEGACY_PROMPT_RUN_ID,
                "owner_id": LEGACY_USER_ID,
                "generation_id": LEGACY_GENERATION_ID,
                "mode": "refine",
                "prompt_before": "legacy prompt",
                "creative_direction": "legacy direction",
                "model_name": "legacy-model",
                "template_version": "legacy-v1",
                "ollama_output": "legacy composed prompt",
                "raw_response_json": {"response": "legacy composed prompt"},
                "error_code": None,
                "error_message": None,
                "duration_ms": 100,
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
        prompt_run = session.get(PromptAssistantRun, LEGACY_PROMPT_RUN_ID)

        assert user is not None and user.username == "legacy.owner"
        assert preference is not None
        assert preference.gallery_scale == 73
        assert preference.source_ratings_json == {}
        assert preference.source_colors_json == {}
        assert preference.checkpoint_tiers_json == {}
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
        assert generation.prompt_fingerprint == sha256(b"legacy prompt").hexdigest()
        assert generation.effective_controls_json == {"prompt": "legacy prompt", "seed": 42}
        assert generation.generation_source_json == {}
        assert generation.raw_history_json == {}
        assert generation.declared_outputs_json == {}
        assert generation.unmapped_outputs_json == {}
        assert generation.result_warnings_json == []
        assert generation.result_errors_json == []
        assert generation.comfyui_status_json == {}
        assert generation.progress_json is None
        assert generation.comfyui_instance_id == "default"
        assert generation.comfyui_instance_label == "default"
        assert generation.collection_id is None
        assert session.scalar(select(func.count()).select_from(Collection)) == 0
        assert session.scalar(select(func.count()).select_from(CollectionFavorite)) == 0
        assert session.scalar(select(func.count()).select_from(GenerationTimingProfile)) == 0
        assert session.scalar(select(func.count()).select_from(GenerationTimingAuditState)) == 0

        assert artifact is not None
        assert artifact.generation_id == generation.id
        assert artifact.owner_id == user.id
        assert artifact.canonical is True
        assert favorite is not None
        assert favorite.generation_id == generation.id
        assert favorite.owner_id == user.id
        assert prompt_run is not None
        assert prompt_run.thinking_enabled is True
        assert prompt_run.instructions is None
        assert prompt_run.ollama_output == "legacy composed prompt"

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
            "prompt_assistant_runs",
        ),
    )
    users = metadata.tables["users"]
    user_preferences = metadata.tables["user_preferences"]
    profiles = metadata.tables["workflow_profiles"]
    generations = metadata.tables["generations"]
    artifacts = metadata.tables["artifacts"]
    favorites = metadata.tables["favorites"]
    prompt_assistant_runs = metadata.tables["prompt_assistant_runs"]
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
        assert (
            connection.execute(
                select(prompt_assistant_runs.c.mode).where(
                    prompt_assistant_runs.c.id == LEGACY_PROMPT_RUN_ID
                )
            ).scalar_one()
            == "refine"
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
        "comfyui_instance_health",
        "artifacts",
        "workflow_profiles",
        "favorites",
        "collections",
        "collection_favorites",
        "lora_operations",
        "expectation_checks",
        "expectation_check_attempts",
    }.issubset(set(inspect(engine).get_table_names()))
    assert "source_ratings_json" in {
        column["name"] for column in inspect(engine).get_columns("user_preferences")
    }
    assert "source_colors_json" in {
        column["name"] for column in inspect(engine).get_columns("user_preferences")
    }
    assert "checkpoint_tiers_json" in {
        column["name"] for column in inspect(engine).get_columns("user_preferences")
    }
    assert "collection_previews_enabled" not in {
        column["name"] for column in inspect(engine).get_columns("user_preferences")
    }
    assert "previews_enabled" in {
        column["name"] for column in inspect(engine).get_columns("collections")
    }
    assert "position" in {column["name"] for column in inspect(engine).get_columns("collections")}
    assert "collection_id" in {
        column["name"] for column in inspect(engine).get_columns("generations")
    }
    assert "thinking_enabled" in {
        column["name"] for column in inspect(engine).get_columns("prompt_assistant_runs")
    }
    assert "ix_generations_timing_audit" in {
        index["name"] for index in inspect(engine).get_indexes("generations")
    }
    assert "ix_generations_instance_queue" in {
        index["name"] for index in inspect(engine).get_indexes("generations")
    }
    assert "ix_generations_owner_collection_accepted" in {
        index["name"] for index in inspect(engine).get_indexes("generations")
    }
    assert "ix_collections_owner_parent" in {
        index["name"] for index in inspect(engine).get_indexes("collections")
    }
    assert {
        "actor_id",
        "idempotency_key",
        "request_digest",
        "source_key",
        "source_id",
        "action",
        "status",
        "expected_revision_json",
        "request_json",
        "internal_json",
    }.issubset({column["name"] for column in inspect(engine).get_columns("lora_operations")})
    assert "ix_lora_operations_source_status" in {
        index["name"] for index in inspect(engine).get_indexes("lora_operations")
    }
    assert ["actor_id", "idempotency_key"] in [
        constraint["column_names"]
        for constraint in inspect(engine).get_unique_constraints("lora_operations")
    ]
    # The journal must outlive an administrator account removed during an operation.
    assert inspect(engine).get_foreign_keys("lora_operations") == []

    favorites_indexes = {
        index["name"]: index["column_names"]
        for index in inspect(engine).get_indexes("collection_favorites")
    }
    assert favorites_indexes["ix_collection_favorites_owner_created"] == [
        "owner_id",
        "created_at",
        "id",
    ]
    assert inspect(engine).get_unique_constraints("collection_favorites")[0]["column_names"] == [
        "owner_id",
        "collection_id",
    ]
    assert {
        (fk["referred_table"], fk["options"]["ondelete"])
        for fk in inspect(engine).get_foreign_keys("collection_favorites")
    } == {("users", "CASCADE"), ("collections", "CASCADE")}

    command.downgrade(config, "base")
    assert "users" not in inspect(engine).get_table_names()

    command.upgrade(config, "head")
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == HEAD_REVISION
    engine.dispose()


def test_generation_run_migration_adopts_active_legacy_work(settings_factory) -> None:
    settings = settings_factory()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    config = _config(settings.database_path)
    command.upgrade(config, LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{settings.database_path}")
    _insert_populated_legacy_rows(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE generations SET status = 'RUNNING', completed_at = NULL"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT total_count FROM generation_runs")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT generation_id FROM generation_run_members")
            ).scalar_one()
            == LEGACY_GENERATION_ID
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    command.downgrade(config, "6e4b9c2a7d15")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT id FROM generations")).scalar_one()
            == LEGACY_GENERATION_ID
        )
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


def test_prompt_migration_and_runner_recovery_preserve_an_isolated_database_copy(tmp_path):
    import importlib.util
    import sqlite3
    import tarfile

    original = tmp_path / "original.db"
    command.upgrade(_config(original), LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{original}")
    _insert_populated_legacy_rows(engine)
    engine.dispose()
    command.upgrade(_config(original), "a12c39e781b4")
    root = tmp_path / "deployment"
    data = root / "data"
    data.mkdir(parents=True)
    candidate = data / "app.db"
    with sqlite3.connect(original) as source, sqlite3.connect(candidate) as target:
        source.backup(target)
    assets = data / "assets"
    assets.mkdir()
    (assets / "keep.txt").write_text("retained artifact")
    archive = tmp_path / "before.tar"
    with tarfile.open(archive, "w") as stream:
        stream.add(data, arcname=".")
    command.upgrade(_config(candidate), "head")
    engine = create_engine(f"sqlite:///{candidate}")
    _assert_populated_head_rows(engine)
    assert {"prompt_generation_runs", "generation_preparations"}.issubset(
        inspect(engine).get_table_names()
    )
    assert "internal_diagnostics_json" in {
        column["name"] for column in inspect(engine).get_columns("prompt_generation_runs")
    }
    engine.dispose()
    # Exercise the installed runner's existing restore transaction, not a downgrade.
    runner_path = Path(__file__).resolve().parents[3] / "scripts" / "production-update.py"
    spec = importlib.util.spec_from_file_location("prompt_migration_recovery", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    recovery = tmp_path / "recovery"
    recovery.mkdir()
    runner.restore_database(root, archive, recovery)
    with sqlite3.connect(candidate) as restored:
        assert (
            restored.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "a12c39e781b4"
        )
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT id FROM generations").fetchone()[0] == LEGACY_GENERATION_ID
    with sqlite3.connect(recovery / "database.after-cutover" / "app.db") as failed:
        assert (
            failed.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
        )
    assert (assets / "keep.txt").read_text() == "retained artifact"


def test_instance_catalog_migration_preserves_cached_health_and_scopes_diagnostics(tmp_path):
    import json

    path = tmp_path / "catalogs.db"
    config = _config(path)
    command.upgrade(config, "c92f6e81ab30")
    engine = create_engine(f"sqlite:///{path}")
    capabilities = {
        "instance_id": "primary",
        "catalog_state": "cached_offline",
        "cached_sources": 2,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO service_health "
                "(service, available, capabilities_json, message, checked_at) "
                "VALUES ('comfyui', 0, :capabilities, 'offline', :now)"
            ),
            {"capabilities": json.dumps(capabilities), "now": datetime.now(UTC)},
        )
        connection.execute(
            text(
                "INSERT INTO workflow_diagnostics "
                "(basename, accepted, code, message, details_json, checked_at) "
                "VALUES ('*', 0, 'server_unreachable', 'offline', '{}', :now)"
            ),
            {"now": datetime.now(UTC)},
        )
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT instance_id, capabilities_json FROM workflow_catalog_health")
        ).one()
        assert row.instance_id == "primary"
        assert json.loads(row.capabilities_json) == capabilities
        assert (
            connection.execute(text("SELECT instance_id FROM workflow_diagnostics")).scalar_one()
            == "primary"
        )
    engine.dispose()
    command.downgrade(config, "c92f6e81ab30")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT instance_id FROM workflow_catalog_health")).scalar_one()
            == "primary"
        )
    engine.dispose()


def test_verified_timing_migration_invalidates_old_estimates_without_deleting_history(tmp_path):
    import json

    path = tmp_path / "timing-upgrade.sqlite3"
    config = _config(path)
    command.upgrade(config, LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{path}")
    _insert_populated_legacy_rows(engine)
    engine.dispose()
    command.upgrade(config, "ab84d290e613")
    engine = create_engine(f"sqlite:///{path}")
    old_progress = {
        "kind": "node",
        "label": "Sampler",
        "fraction": 0.5,
        "eta": {"remaining_seconds": 43774},
    }
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE generations SET progress_json = :progress WHERE id = :id"),
            {"progress": json.dumps(old_progress), "id": LEGACY_GENERATION_ID},
        )
        before = connection.execute(
            text(
                "SELECT final_prompt, raw_history_json, compiled_graph_json "
                "FROM generations WHERE id = :id"
            ),
            {"id": LEGACY_GENERATION_ID},
        ).one()
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        after = connection.execute(
            text(
                "SELECT final_prompt, raw_history_json, compiled_graph_json "
                "FROM generations WHERE id = :id"
            ),
            {"id": LEGACY_GENERATION_ID},
        ).one()
        assert after == before
        progress = json.loads(
            connection.execute(
                text("SELECT progress_json FROM generations WHERE id = :id"),
                {"id": LEGACY_GENERATION_ID},
            ).scalar_one()
        )
        assert progress == {k: v for k, v in old_progress.items() if k != "eta"}
        assert (
            connection.execute(
                text("SELECT execution_timing_json FROM generations WHERE id = :id"),
                {"id": LEGACY_GENERATION_ID},
            ).scalar_one()
            is None
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_collection_order_migration_numbers_existing_folders_per_parent(tmp_path):
    path = tmp_path / "collection-order-upgrade.sqlite3"
    config = _config(path)
    command.upgrade(config, LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{path}")
    _insert_populated_legacy_rows(engine)
    engine.dispose()
    command.upgrade(config, "e7b13c9a5d42")

    # Two sibling groups filed out of creation order, so the backfill cannot simply
    # renumber the table as one list.
    folders = [
        ("root-c", None, datetime(2026, 7, 13, 12, 3, tzinfo=UTC)),
        ("root-a", None, datetime(2026, 7, 13, 12, 1, tzinfo=UTC)),
        ("root-b", None, datetime(2026, 7, 13, 12, 2, tzinfo=UTC)),
        ("child-b", "root-a", datetime(2026, 7, 13, 12, 5, tzinfo=UTC)),
        ("child-a", "root-a", datetime(2026, 7, 13, 12, 4, tzinfo=UTC)),
    ]
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for folder_id, parent_id, created_at in folders:
            connection.execute(
                text(
                    "INSERT INTO collections "
                    "(id, owner_id, parent_id, name, previews_enabled, created_at, updated_at) "
                    "VALUES (:id, :owner, :parent, :name, 1, :created_at, :created_at)"
                ),
                {
                    "id": folder_id,
                    "owner": LEGACY_USER_ID,
                    "parent": parent_id,
                    "name": folder_id,
                    "created_at": created_at,
                },
            )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, parent_id, position FROM collections ORDER BY parent_id, position")
        ).all()
        # Each parent's children keep the creation order their owner already saw.
        assert [(row.id, row.position) for row in rows if row.parent_id is None] == [
            ("root-a", 0),
            ("root-b", 1),
            ("root-c", 2),
        ]
        assert [(row.id, row.position) for row in rows if row.parent_id == "root-a"] == [
            ("child-a", 0),
            ("child-b", 1),
        ]
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()

    command.downgrade(config, "e7b13c9a5d42")
    engine = create_engine(f"sqlite:///{path}")
    assert "position" not in {
        column["name"] for column in inspect(engine).get_columns("collections")
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM collections")).scalar_one() == 5
    engine.dispose()
