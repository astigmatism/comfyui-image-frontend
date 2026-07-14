"""add published source identity and complete generation results

Revision ID: b84f2d6a91c3
Revises: 7c9b2d4e6f81
Create Date: 2026-07-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b84f2d6a91c3"
down_revision: str | None = "7c9b2d4e6f81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("instance_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("source_key", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("source_id", sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column("publication_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("publication_schema", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("manifest_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("published_at", sa.String(length=100), nullable=True))
        batch_op.add_column(
            sa.Column("warnings_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.add_column(
            sa.Column(
                "readiness",
                sa.String(length=40),
                nullable=False,
                server_default="ready",
            )
        )
        batch_op.create_index("ix_workflow_profiles_instance_id", ["instance_id"], unique=False)
        batch_op.create_index("ix_workflow_profiles_source_key", ["source_key"], unique=False)
        batch_op.create_index(
            "ix_workflow_profiles_publication_id", ["publication_id"], unique=False
        )

    with op.batch_alter_table("generations", schema=None) as batch_op:
        for name, default in (
            ("generation_source_json", "'{}'"),
            ("raw_history_json", "'{}'"),
            ("declared_outputs_json", "'{}'"),
            ("unmapped_outputs_json", "'{}'"),
            ("result_warnings_json", "'[]'"),
            ("result_errors_json", "'[]'"),
            ("comfyui_status_json", "'{}'"),
        ):
            batch_op.add_column(
                sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text(default))
            )


def downgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        for name in (
            "comfyui_status_json",
            "result_errors_json",
            "result_warnings_json",
            "unmapped_outputs_json",
            "declared_outputs_json",
            "raw_history_json",
            "generation_source_json",
        ):
            batch_op.drop_column(name)

    with op.batch_alter_table("workflow_profiles", schema=None) as batch_op:
        batch_op.drop_index("ix_workflow_profiles_publication_id")
        batch_op.drop_index("ix_workflow_profiles_source_key")
        batch_op.drop_index("ix_workflow_profiles_instance_id")
        for name in (
            "readiness",
            "warnings_json",
            "published_at",
            "manifest_sha256",
            "publication_schema",
            "publication_id",
            "source_id",
            "source_key",
            "instance_id",
        ):
            batch_op.drop_column(name)
