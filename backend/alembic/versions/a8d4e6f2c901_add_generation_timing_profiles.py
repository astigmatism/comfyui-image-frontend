"""add generation timing profiles

Revision ID: a8d4e6f2c901
Revises: 4f2a8c1d9e70
Create Date: 2026-07-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8d4e6f2c901"
down_revision: str | None = "4f2a8c1d9e70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.create_index(
            "ix_generations_timing_audit",
            ["status", "completed_at", "id"],
            unique=False,
        )
    op.create_table(
        "generation_timing_profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("feature_version", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("samples_json", sa.JSON(), nullable=False),
        sa.Column("median_seconds", sa.Float(), nullable=False),
        sa.Column("lower_seconds", sa.Float(), nullable=False),
        sa.Column("upper_seconds", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "feature_version",
            "scope",
            "scope_key",
            name="uq_generation_timing_profile_scope",
        ),
    )
    op.create_index(
        "ix_generation_timing_profiles_lookup",
        "generation_timing_profiles",
        ["feature_version", "scope", "scope_key"],
        unique=False,
    )
    op.create_table(
        "generation_timing_audit_state",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("feature_version", sa.Integer(), nullable=False),
        sa.Column("cursor_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_generation_id", sa.String(length=36), nullable=True),
        sa.Column("backfill_complete", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("generation_timing_audit_state")
    op.drop_index(
        "ix_generation_timing_profiles_lookup",
        table_name="generation_timing_profiles",
    )
    op.drop_table("generation_timing_profiles")
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.drop_index("ix_generations_timing_audit")
