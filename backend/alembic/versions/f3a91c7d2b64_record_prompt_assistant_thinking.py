"""record prompt assistant thinking mode

Revision ID: f3a91c7d2b64
Revises: d72f6a8c9e10
Create Date: 2026-08-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a91c7d2b64"
down_revision: str | None = "d72f6a8c9e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("prompt_assistant_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "thinking_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("prompt_assistant_runs", schema=None) as batch_op:
        batch_op.drop_column("thinking_enabled")
