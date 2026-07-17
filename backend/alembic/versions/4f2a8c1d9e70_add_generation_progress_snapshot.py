"""add generation progress snapshot

Revision ID: 4f2a8c1d9e70
Revises: e52a8c1f4b90
Create Date: 2026-07-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4f2a8c1d9e70"
down_revision: str | None = "e52a8c1f4b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("progress_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.drop_column("progress_json")
