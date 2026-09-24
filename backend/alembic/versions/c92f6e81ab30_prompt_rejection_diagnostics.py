"""Retain bounded prompt rejection diagnostics without changing historical images."""
from alembic import op
import sqlalchemy as sa

revision = "c92f6e81ab30"
down_revision = "b73a94f1c205"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "prompt_generation_runs",
        sa.Column("internal_diagnostics_json", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade():
    with op.batch_alter_table("prompt_generation_runs") as batch:
        batch.drop_column("internal_diagnostics_json")
