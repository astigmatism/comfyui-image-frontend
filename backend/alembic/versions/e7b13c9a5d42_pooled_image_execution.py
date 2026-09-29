"""pooled image execution: late-bound ComfyUI worker assignment

Revision ID: e7b13c9a5d42
Revises: d6a2f9c3b481
Create Date: 2026-09-29 00:00:00.000000

Image generations are accepted without a runtime and the dispatcher records the
winning image worker when it claims the job, so any idle pool member can take
the next queued image. Existing rows keep their recorded runtime untouched.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b13c9a5d42"
down_revision: str | None = "d6a2f9c3b481"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.alter_column(
            "comfyui_instance_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch_op.alter_column(
            "comfyui_instance_label",
            existing_type=sa.String(length=120),
            nullable=True,
        )
    with op.batch_alter_table("workflow_profiles", schema=None) as batch_op:
        batch_op.create_index(
            "ix_workflow_profiles_instance_source",
            ["instance_id", "is_current", "source_id"],
            unique=False,
        )


def downgrade() -> None:
    unassigned = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM generations WHERE comfyui_instance_id IS NULL")
    )
    if unassigned:
        raise RuntimeError(
            "Refusing to downgrade while "
            f"{unassigned} generation(s) await an image worker. Let the queue drain "
            "(or cancel those generations) so every row records its runtime."
        )
    with op.batch_alter_table("workflow_profiles", schema=None) as batch_op:
        batch_op.drop_index("ix_workflow_profiles_instance_source")
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.alter_column(
            "comfyui_instance_label",
            existing_type=sa.String(length=120),
            nullable=False,
        )
        batch_op.alter_column(
            "comfyui_instance_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
