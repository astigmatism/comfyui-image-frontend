"""add collection favorites

Revision ID: 6e4b9c2a7d15
Revises: 2f8d6a1c4b90
Create Date: 2026-09-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6e4b9c2a7d15"
down_revision: str | None = "2f8d6a1c4b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collection_favorites",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("collection_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "collection_id", name="uq_collection_favorite_owner_collection"
        ),
    )
    with op.batch_alter_table("collection_favorites", schema=None) as batch_op:
        batch_op.create_index("ix_collection_favorites_collection", ["collection_id"], unique=False)
        batch_op.create_index(
            "ix_collection_favorites_owner_created", ["owner_id", "created_at", "id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("collection_favorites", schema=None) as batch_op:
        batch_op.drop_index("ix_collection_favorites_owner_created")
        batch_op.drop_index("ix_collection_favorites_collection")
    op.drop_table("collection_favorites")
