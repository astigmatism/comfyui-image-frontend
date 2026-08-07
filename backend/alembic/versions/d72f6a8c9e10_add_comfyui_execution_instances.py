"""add ComfyUI execution instances

Revision ID: d72f6a8c9e10
Revises: a8d4e6f2c901
Create Date: 2026-08-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d72f6a8c9e10"
down_revision: str | None = "a8d4e6f2c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comfyui_instance_health",
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("instance_id"),
    )
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "comfyui_instance_id",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text("'default'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "comfyui_instance_label",
                sa.String(length=120),
                nullable=False,
                server_default=sa.text("'default'"),
            )
        )

    op.execute(
        sa.text(
            """
            UPDATE generations
            SET comfyui_instance_id = COALESCE(
                NULLIF(json_extract(generation_source_json, '$.instance_id'), ''),
                (
                    SELECT NULLIF(workflow_profiles.instance_id, '')
                    FROM workflow_profiles
                    WHERE workflow_profiles.id = generations.workflow_profile_id
                ),
                'default'
            )
            """
        )
    )
    op.execute(
        sa.text(
            "UPDATE generations SET comfyui_instance_label = comfyui_instance_id"
        )
    )

    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.alter_column(
            "comfyui_instance_id",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "comfyui_instance_label",
            existing_type=sa.String(length=120),
            nullable=False,
            server_default=None,
        )
        batch_op.create_index(
            "ix_generations_instance_queue",
            ["comfyui_instance_id", "status", "queue_seq"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.drop_index("ix_generations_instance_queue")
        batch_op.drop_column("comfyui_instance_label")
        batch_op.drop_column("comfyui_instance_id")
    op.drop_table("comfyui_instance_health")
