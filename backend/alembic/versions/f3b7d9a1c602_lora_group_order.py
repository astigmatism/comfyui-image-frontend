"""Remember the shared display order of LoRA groups."""

import sqlalchemy as sa
from alembic import op

revision = "f3b7d9a1c602"
down_revision = "e8c6a4b2d901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lora_grouping_revisions",
        sa.Column("group_order_json", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("lora_grouping_revisions", "group_order_json")
