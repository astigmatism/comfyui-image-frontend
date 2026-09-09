"""add collections

Revision ID: b1e7c4a92d60
Revises: c5d7f1a8e392
Create Date: 2026-09-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1e7c4a92d60"
down_revision: str | None = "c5d7f1a8e392"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["collections.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collections_owner_parent",
        "collections",
        ["owner_id", "parent_id"],
        unique=False,
    )

    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("collection_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_generations_collection_id_collections",
            "collections",
            ["collection_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_generations_owner_collection_accepted",
            ["owner_id", "collection_id", "accepted_at", "id"],
            unique=False,
        )

    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "collection_previews_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.drop_column("collection_previews_enabled")

    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.drop_index("ix_generations_owner_collection_accepted")
        batch_op.drop_constraint(
            "fk_generations_collection_id_collections",
            type_="foreignkey",
        )
        batch_op.drop_column("collection_id")

    op.drop_index("ix_collections_owner_parent", table_name="collections")
    op.drop_table("collections")
