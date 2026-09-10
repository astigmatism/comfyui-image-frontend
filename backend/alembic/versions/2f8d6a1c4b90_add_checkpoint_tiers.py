"""add checkpoint tier preferences

Revision ID: 2f8d6a1c4b90
Revises: c9e3b17d4f28
Create Date: 2026-09-09 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2f8d6a1c4b90"
down_revision: str | None = "c9e3b17d4f28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "checkpoint_tiers_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.drop_column("checkpoint_tiers_json")
