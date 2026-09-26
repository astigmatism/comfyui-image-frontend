"""Add shared workflow LoRA thumbnails with per-item revisions."""

import sqlalchemy as sa
from alembic import op

revision = "5e1b9c7d4a20"
down_revision = "ea93b51d708c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lora_images",
        sa.Column("workflow_key", sa.String(64), primary_key=True),
        sa.Column("control_id", sa.String(64), primary_key=True),
        sa.Column("item_id", sa.String(64), primary_key=True),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(500), unique=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("lora_images")
