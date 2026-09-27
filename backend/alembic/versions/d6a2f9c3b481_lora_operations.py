"""Record durable LoRA management operations."""

import sqlalchemy as sa
from alembic import op

revision = "d6a2f9c3b481"
down_revision = "5e1b9c7d4a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lora_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(1024), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expected_revision_json", sa.JSON(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("internal_json", sa.JSON(), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("result_json", sa.JSON()),
        sa.Column("blockers_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_lora_operation_actor_key"),
    )
    op.create_index("ix_lora_operations_source_status", "lora_operations", ["source_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_lora_operations_source_status", table_name="lora_operations")
    op.drop_table("lora_operations")
