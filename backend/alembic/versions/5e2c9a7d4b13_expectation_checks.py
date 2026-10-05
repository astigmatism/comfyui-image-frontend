"""Creative Direction expectations verified with vision.

Revision ID: 5e2c9a7d4b13
Revises: 946b609a5db1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e2c9a7d4b13"
down_revision: str | None = "946b609a5db1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expectation_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("collection_id", sa.String(36), sa.ForeignKey("collections.id", ondelete="SET NULL")),
        sa.Column("best_attempt", sa.Integer()),
        sa.Column("final_prompt", sa.Text()),
        sa.Column(
            "final_composition_id",
            sa.String(36),
            sa.ForeignKey("prompt_assistant_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("queued_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_expectation_checks_owner_status", "expectation_checks", ["owner_id", "status"])
    op.create_table(
        "expectation_check_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "check_id",
            sa.String(36),
            sa.ForeignKey("expectation_checks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("prompt", sa.Text()),
        sa.Column(
            "composition_id",
            sa.String(36),
            sa.ForeignKey("prompt_assistant_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("generation_id", sa.String(36), sa.ForeignKey("generations.id", ondelete="SET NULL")),
        sa.Column("evaluation_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("score", sa.Integer()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("check_id", "number", name="uq_expectation_attempt_number"),
    )
    op.create_index(
        "ix_expectation_attempts_generation", "expectation_check_attempts", ["generation_id"]
    )


def downgrade() -> None:
    # Checks are auxiliary provenance; their probe images remain ordinary generations.
    op.drop_index("ix_expectation_attempts_generation", table_name="expectation_check_attempts")
    op.drop_table("expectation_check_attempts")
    op.drop_index("ix_expectation_checks_owner_status", table_name="expectation_checks")
    op.drop_table("expectation_checks")
