"""Durable Creative Direction for retained prompt reruns."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b28a6f1d903e"
down_revision: str | None = "a4e1c7b9d206"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_rerun_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collection_id", sa.String(36), sa.ForeignKey("collections.id", ondelete="SET NULL")),
        sa.Column("stopped", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rerun_owner_collection", "prompt_rerun_runs", ["owner_id", "collection_id"])
    with op.batch_alter_table("generation_preparations") as batch:
        batch.alter_column("prompt_run_id", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("rerun_id", sa.String(36)))
        batch.create_foreign_key("fk_preparation_rerun", "prompt_rerun_runs", ["rerun_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_preparation_rerun_position", ["rerun_id", "position"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM generation_preparations WHERE prompt_run_id IS NULL")):
        raise RuntimeError("Retained-prompt preparations exist; restore the pre-upgrade backup to downgrade.")
    with op.batch_alter_table("generation_preparations") as batch:
        batch.drop_index("ix_preparation_rerun_position")
        batch.drop_constraint("fk_preparation_rerun", type_="foreignkey")
        batch.drop_column("rerun_id")
        batch.alter_column("prompt_run_id", existing_type=sa.String(36), nullable=False)
    op.drop_table("prompt_rerun_runs")
