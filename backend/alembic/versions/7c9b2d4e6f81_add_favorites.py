"""add favorites

Revision ID: 7c9b2d4e6f81
Revises: 19a5fe877349
Create Date: 2026-07-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c9b2d4e6f81"
down_revision: str | None = "19a5fe877349"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "favorites",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("generation_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["generation_id"], ["generations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "generation_id", name="uq_favorite_owner_generation"
        ),
    )
    with op.batch_alter_table("favorites", schema=None) as batch_op:
        batch_op.create_index("ix_favorites_generation", ["generation_id"], unique=False)
        batch_op.create_index(
            "ix_favorites_owner_created", ["owner_id", "created_at", "id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("favorites", schema=None) as batch_op:
        batch_op.drop_index("ix_favorites_owner_created")
        batch_op.drop_index("ix_favorites_generation")
    op.drop_table("favorites")
