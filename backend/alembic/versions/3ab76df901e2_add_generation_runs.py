"""Persist combined generation runs and their original totals."""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "3ab76df901e2"
down_revision = "6e4b9c2a7d15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_generations_owner_activity",
        "generations",
        ["owner_id", "status", "collection_id", "pending_delete"],
    )
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        *[
            sa.Column(name, sa.Integer(), nullable=False, server_default="0")
            for name in (
                "total_count",
                "submission_failed_count",
                "deleted_succeeded_count",
                "deleted_failed_count",
                "deleted_cancelled_count",
            )
        ],
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_generation_runs_owner_created", "generation_runs", ["owner_id", "created_at", "id"]
    )
    op.create_table(
        "generation_run_members",
        sa.Column(
            "generation_id",
            sa.String(36),
            sa.ForeignKey("generations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_generation_run_members_run_id", "generation_run_members", ["run_id"])

    # Already queued/running work becomes one recoverable run per owner. Historical
    # completed cards do not contribute to the first progress denominator.
    connection = op.get_bind()
    owners = (
        connection.execute(
            sa.text(
                "SELECT owner_id, count(*) AS count FROM generations "
                "WHERE status IN ('QUEUED','DISPATCHING','RUNNING','CANCEL_REQUESTED') GROUP BY owner_id"
            )
        )
        .mappings()
        .all()
    )
    for owner in owners:
        values = {
            "id": str(uuid4()),
            "owner_id": owner["owner_id"],
            "total": owner["count"],
            "now": datetime.now(UTC),
        }
        connection.execute(
            sa.text(
                "INSERT INTO generation_runs (id, owner_id, total_count, created_at, updated_at) "
                "VALUES (:id, :owner_id, :total, :now, :now)"
            ),
            values,
        )
        connection.execute(
            sa.text(
                "INSERT INTO generation_run_members (generation_id, run_id) "
                "SELECT id, :id FROM generations WHERE owner_id = :owner_id "
                "AND status IN ('QUEUED','DISPATCHING','RUNNING','CANCEL_REQUESTED')"
            ),
            values,
        )


def downgrade() -> None:
    op.drop_table("generation_run_members")
    op.drop_table("generation_runs")
    op.drop_index("ix_generations_owner_activity", table_name="generations")
