"""add per-collection preview toggle

Revision ID: c9e3b17d4f28
Revises: b1e7c4a92d60
Create Date: 2026-09-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e3b17d4f28"
down_revision: str | None = "b1e7c4a92d60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "previews_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.drop_column("collection_previews_enabled")


def downgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "collection_previews_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.drop_column("previews_enabled")
