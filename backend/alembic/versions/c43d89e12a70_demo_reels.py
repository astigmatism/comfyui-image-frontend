"""Shared Demo Reels and protected internal storage owner."""

import sqlalchemy as sa
from alembic import op

revision = "c43d89e12a70"
down_revision = "f3b7d9a1c602"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_system", sa.Boolean(), nullable=False, server_default="0"))
    op.create_table(
        "demo_reel_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("rating_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("slots", sa.JSON(), nullable=False),
        sa.Column("recipes", sa.JSON(), nullable=False),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "demo_reels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("library_key", sa.String(64), nullable=False),
        sa.Column("lora_identity", sa.String(68), nullable=False),
        sa.Column("collection_id", sa.String(36), sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.UniqueConstraint("library_key", "lora_identity"),
    )
    op.create_table(
        "demo_reel_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reel_id", sa.String(36), sa.ForeignKey("demo_reels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot_id", sa.String(36), nullable=False),
        sa.Column("checkpoint_id", sa.String(68), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("generation_id", sa.String(36), sa.ForeignKey("generations.id", ondelete="SET NULL"), unique=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("reel_id", "slot_id", "checkpoint_id"),
    )
    op.create_index("ix_demo_reel_entries_reel_id", "demo_reel_entries", ["reel_id"])
    op.create_table(
        "demo_reel_transfers",
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.String(36), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("generation_ids", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM users WHERE is_system = 1")):
        raise RuntimeError("Demo Reel data requires backup recovery before downgrade.")
    op.drop_table("demo_reel_transfers")
    op.drop_table("demo_reel_entries")
    op.drop_table("demo_reels")
    op.drop_table("demo_reel_config")
    op.drop_column("users", "is_system")
