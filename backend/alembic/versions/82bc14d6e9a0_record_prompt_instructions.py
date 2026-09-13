"""Record the instructions used for a successful prompt composition."""

from alembic import op
import sqlalchemy as sa

revision = "82bc14d6e9a0"
down_revision = "3ab76df901e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompt_assistant_runs", sa.Column("instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("prompt_assistant_runs", "instructions")
