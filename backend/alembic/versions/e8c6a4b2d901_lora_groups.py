"""Shared model-family LoRA groups and single group membership."""

import sqlalchemy as sa
from alembic import op

revision = "e8c6a4b2d901"
down_revision = "b2f4d8a6c931"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lora_grouping_revisions",
        sa.Column("library_key", sa.String(64), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "lora_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("library_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("name_normalized", sa.String(240), nullable=False),
        sa.UniqueConstraint("library_key", "name_normalized", name="uq_lora_group_name"),
        sa.UniqueConstraint("library_key", "id", name="uq_lora_group_library_id"),
    )
    op.create_table(
        "lora_group_memberships",
        sa.Column("library_key", sa.String(64), primary_key=True),
        sa.Column("lora_identity", sa.String(68), primary_key=True),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["library_key", "group_id"],
            ["lora_groups.library_key", "lora_groups.id"],
            ondelete="CASCADE",
        ),
    )
    # No membership means Uncategorized, including every legacy and newly published LoRA.


def downgrade() -> None:
    op.drop_table("lora_group_memberships")
    op.drop_table("lora_groups")
    op.drop_table("lora_grouping_revisions")
