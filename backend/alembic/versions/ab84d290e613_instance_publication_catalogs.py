"""Isolate publication discovery health and diagnostics per instance."""

import sqlalchemy as sa
from alembic import op

revision = "ab84d290e613"
down_revision = "c92f6e81ab30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_catalog_health",
        sa.Column("instance_id", sa.String(64), primary_key=True),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("workflow_diagnostics", sa.Column("instance_id", sa.String(64)))
    op.create_index("ix_workflow_diagnostics_instance_id", "workflow_diagnostics", ["instance_id"])
    op.execute(sa.text("""
        INSERT INTO workflow_catalog_health
            (instance_id, available, capabilities_json, message, checked_at)
        SELECT json_extract(capabilities_json, '$.instance_id'), available,
               capabilities_json, message, checked_at
        FROM service_health
        WHERE service = 'comfyui'
          AND json_extract(capabilities_json, '$.instance_id') IS NOT NULL
    """))
    op.execute(sa.text("""
        UPDATE workflow_diagnostics
        SET instance_id = (
            SELECT json_extract(capabilities_json, '$.instance_id')
            FROM service_health WHERE service = 'comfyui'
        )
    """))


def downgrade() -> None:
    op.drop_index("ix_workflow_diagnostics_instance_id", table_name="workflow_diagnostics")
    op.drop_column("workflow_diagnostics", "instance_id")
    op.drop_table("workflow_catalog_health")
