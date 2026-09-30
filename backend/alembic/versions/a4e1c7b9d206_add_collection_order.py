"""add owner-chosen folder order within each parent

Revision ID: a4e1c7b9d206
Revises: e7b13c9a5d42
Create Date: 2026-09-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4e1c7b9d206"
down_revision: str | None = "e7b13c9a5d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("position", sa.Integer(), nullable=False, server_default="0")
        )

    # Existing folders keep exactly the order their owners already see: creation order
    # within each parent, numbered from zero so later reorders stay dense. Ordering by
    # owner and parent keeps every sibling group contiguous whatever the NULL collation.
    connection = op.get_bind()
    rows = (
        connection.execute(
            sa.text(
                "SELECT id, owner_id, parent_id FROM collections "
                "ORDER BY owner_id, parent_id, created_at, id"
            )
        )
        .mappings()
        .all()
    )
    group: tuple[str, str | None] | None = None
    position = 0
    for row in rows:
        current = (row["owner_id"], row["parent_id"])
        if current != group:
            group = current
            position = 0
        connection.execute(
            sa.text("UPDATE collections SET position = :position WHERE id = :id"),
            {"position": position, "id": row["id"]},
        )
        position += 1


def downgrade() -> None:
    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.drop_column("position")
