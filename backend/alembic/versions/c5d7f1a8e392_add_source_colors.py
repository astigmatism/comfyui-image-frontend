"""add source colors

Revision ID: c5d7f1a8e392
Revises: f3a91c7d2b64
Create Date: 2026-08-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d7f1a8e392"
down_revision: str | None = "f3a91c7d2b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_colors_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.drop_column("source_colors_json")
