"""add per-checkpoint control defaults

An account records the control values it knows a given checkpoint wants, keyed on the
checkpoint choice option ID so the note survives a checkpoint upgrade. It is its own
column, not a key inside settings_json: the panel autosaves settings_json on every edit,
and a default that a stray autosave could rewrite would be worse than no default.

Revision ID: 4c7a1d8b6e25
Revises: b5d1e8a3c092
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c7a1d8b6e25"
down_revision: str | None = "b5d1e8a3c092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "control_defaults_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("user_preferences", schema=None) as batch_op:
        batch_op.drop_column("control_defaults_json")
