"""Shared LoRA library: library operations, one thumbnail per LoRA, LoRA ranks."""

import hashlib
import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from app.domain.lora_identity import generation_lora_usage_v1, lora_identity_v1

revision = "946b609a5db1"
down_revision = "c73e2a9140bd"
branch_labels = None
depends_on = None


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def upgrade() -> None:
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(
            sa.Column("lora_tiers_json", sa.JSON(), nullable=False, server_default="{}")
        )
    with op.batch_alter_table("lora_operations") as batch:
        batch.add_column(sa.Column("scope", sa.String(16), nullable=False, server_default="source"))
    op.create_table(
        "lora_operation_targets",
        sa.Column(
            "operation_id",
            sa.String(36),
            sa.ForeignKey("lora_operations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_id", sa.String(1024), primary_key=True),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("expected_revision_json", sa.JSON(), nullable=False),
        sa.Column("candidate_revision_json", sa.JSON()),
    )
    op.create_index("ix_lora_operation_targets_source", "lora_operation_targets", ["source_id"])
    op.create_table(
        "lora_library_images",
        sa.Column("lora_identity", sa.String(68), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(500), unique=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "generation_loras",
        sa.Column(
            "generation_id",
            sa.String(36),
            sa.ForeignKey("generations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("lora_identity", sa.String(68), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_generation_loras_identity", "generation_loras", ["lora_identity", "generation_id"]
    )
    connection = op.get_bind()
    metadata = sa.MetaData()
    profiles = sa.Table("workflow_profiles", metadata, autoload_with=connection)
    legacy = sa.Table("lora_images", metadata, autoload_with=connection)
    library = sa.Table("lora_library_images", metadata, autoload_with=connection)
    generations = sa.Table("generations", metadata, autoload_with=connection)
    usage_table = sa.Table("generation_loras", metadata, autoload_with=connection)

    # Legacy thumbnails are keyed by sha256(private filename). Every stored publication
    # graph can name that file, which yields the shared identity of the same weight.
    filenames: dict[str, str] = {}
    last_id = ""
    while True:
        rows = connection.execute(
            sa.select(profiles.c.id, profiles.c.source_api_json)
            .where(profiles.c.id > last_id)
            .order_by(profiles.c.id)
            .limit(50)
        ).all()
        if not rows:
            break
        for row in rows:
            graph = _json(row.source_api_json)
            for node in graph.values() if isinstance(graph, dict) else []:
                if not isinstance(node, dict) or node.get("class_type") != "CIFLoraStack":
                    continue
                catalog = _json((node.get("inputs") or {}).get("catalog_json"))
                for item in catalog if isinstance(catalog, list) else []:
                    filename = item.get("filename") if isinstance(item, dict) else None
                    if isinstance(filename, str) and filename:
                        filenames[hashlib.sha256(filename.encode()).hexdigest()] = filename
        last_id = rows[-1].id
    winners: dict[str, tuple[datetime, str, int]] = {}
    for row in connection.execute(
        sa.select(
            legacy.c.binding_hash, legacy.c.storage_path, legacy.c.revision, legacy.c.updated_at
        ).where(legacy.c.storage_path.is_not(None))
    ):
        identity = lora_identity_v1(filenames.get(row.binding_hash))
        if identity is None:
            continue
        updated = row.updated_at or datetime.min
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        current = winners.get(identity)
        # The newest image wins; older duplicates stay only in the legacy table.
        if current is None or updated > current[0]:
            winners[identity] = (updated, row.storage_path, max(int(row.revision or 1), 1))
    for identity, (updated, storage_path, image_revision) in winners.items():
        connection.execute(
            library.insert().values(
                lora_identity=identity,
                revision=image_revision,
                storage_path=storage_path,
                updated_at=updated,
            )
        )

    last_id = ""
    while True:
        rows = connection.execute(
            sa.select(
                generations.c.id,
                generations.c.resolved_contract_json,
                generations.c.effective_controls_json,
                generations.c.compiled_graph_json,
            )
            .where(generations.c.id > last_id)
            .order_by(generations.c.id)
            .limit(100)
        ).all()
        if not rows:
            break
        for row in rows:
            for usage in generation_lora_usage_v1(
                _json(row.resolved_contract_json),
                _json(row.effective_controls_json),
                _json(row.compiled_graph_json),
            ):
                connection.execute(
                    usage_table.insert().values(
                        generation_id=row.id,
                        position=usage.position,
                        lora_identity=usage.lora_identity,
                        label=usage.label,
                        strength=usage.strength,
                    )
                )
        last_id = rows[-1].id


def downgrade() -> None:
    op.drop_index("ix_generation_loras_identity", table_name="generation_loras")
    op.drop_table("generation_loras")
    op.drop_table("lora_library_images")
    op.drop_index("ix_lora_operation_targets_source", table_name="lora_operation_targets")
    op.drop_table("lora_operation_targets")
    with op.batch_alter_table("lora_operations") as batch:
        batch.drop_column("scope")
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("lora_tiers_json")
