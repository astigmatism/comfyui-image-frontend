"""Remove LoRA ranks: drop saved LoRA tiers and per-generation LoRA usage.

Revision ID: b2f4d8a6c931
Revises: 5e2c9a7d4b13
"""

import sqlalchemy as sa
from alembic import op

revision = "b2f4d8a6c931"
down_revision = "5e2c9a7d4b13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The rank feature is gone: nothing reads the saved LoRA tiers or the
    # per-generation LoRA usage any more, so both objects are dropped.
    op.drop_index("ix_generation_loras_identity", table_name="generation_loras")
    op.drop_table("generation_loras")
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("lora_tiers_json")


def downgrade() -> None:
    with op.batch_alter_table("user_preferences") as batch:
        batch.add_column(
            sa.Column("lora_tiers_json", sa.JSON(), nullable=False, server_default="{}")
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
    # Historical usage is not backfilled; it can be re-derived from each
    # generation's frozen contract and effective controls if ever needed.
