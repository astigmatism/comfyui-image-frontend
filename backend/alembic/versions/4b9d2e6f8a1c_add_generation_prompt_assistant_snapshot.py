"""Snapshot the assistant inputs on every accepted generation for recall."""

import sqlalchemy as sa
from alembic import op

revision = "4b9d2e6f8a1c"
down_revision = "93e4a2b7c610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generations",
        sa.Column("prompt_assistant_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generations", "prompt_assistant_json")
