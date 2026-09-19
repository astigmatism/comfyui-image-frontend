"""Server-owned automation and shared settings.

Revision ID: f7c1b4e8a209
Revises: 4b9d2e6f8a1c
"""
from alembic import op
import sqlalchemy as sa

revision = "f7c1b4e8a209"
down_revision = "4b9d2e6f8a1c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_preferences", sa.Column("settings_json", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("user_preferences", sa.Column("settings_initialized", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user_preferences", sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "auto_generations",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="off"),
        sa.Column("snapshot_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("workflow_profiles.id", ondelete="RESTRICT")),
        sa.Column("latest_prompt", sa.Text()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("message", sa.Text()),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auto_generation_cycles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("auto_generations.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("claim", sa.String(36)),
        sa.Column("prompt_run_id", sa.String(36), sa.ForeignKey("prompt_assistant_runs.id", ondelete="SET NULL")),
        sa.Column("prompt", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auto_cycles_owner", "auto_generation_cycles", ["user_id", "created_at"])
    op.create_index("ix_auto_cycles_state", "auto_generation_cycles", ["user_id", "state", "revision"])
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("auto_cycle_id", sa.String(36)))
        batch.create_foreign_key("fk_generation_auto_cycle", "auto_generation_cycles", ["auto_cycle_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_generations_auto_cycle_id", ["auto_cycle_id"])


def downgrade():
    with op.batch_alter_table("generations") as batch:
        batch.drop_index("ix_generations_auto_cycle_id")
        batch.drop_constraint("fk_generation_auto_cycle", type_="foreignkey")
        batch.drop_column("auto_cycle_id")
    op.drop_table("auto_generation_cycles")
    op.drop_table("auto_generations")
    with op.batch_alter_table("user_preferences") as batch:
        batch.drop_column("settings_json")
        batch.drop_column("settings_initialized")
        batch.drop_column("revision")
