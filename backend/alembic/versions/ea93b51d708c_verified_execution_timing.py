"""Verified execution durations and submission batch identity."""

from alembic import op
import sqlalchemy as sa

revision = "ea93b51d708c"
down_revision = "ab84d290e613"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("generations", sa.Column("timing_batch_id", sa.String(36)))
    op.add_column("generations", sa.Column("execution_timing_json", sa.JSON()))
    op.create_index("ix_generations_timing_batch_id", "generations", ["timing_batch_id"])
    # Historical auto cycles and preparation groups have unambiguous batch identity.
    op.execute(
        "UPDATE generations SET timing_batch_id = auto_cycle_id WHERE auto_cycle_id IS NOT NULL"
    )
    op.execute(
        "UPDATE generations SET timing_batch_id = (SELECT group_id FROM generation_preparations p WHERE p.generation_id = generations.id LIMIT 1) WHERE timing_batch_id IS NULL"
    )
    op.execute(
        "UPDATE generations SET progress_json = json_remove(progress_json, '$.eta') WHERE progress_json IS NOT NULL"
    )
    op.execute("DELETE FROM generation_timing_profiles")
    op.execute("DELETE FROM generation_timing_audit_state")


def downgrade():
    op.drop_index("ix_generations_timing_batch_id", table_name="generations")
    op.drop_column("generations", "execution_timing_json")
    op.drop_column("generations", "timing_batch_id")
    op.execute("DELETE FROM generation_timing_profiles")
    op.execute("DELETE FROM generation_timing_audit_state")
