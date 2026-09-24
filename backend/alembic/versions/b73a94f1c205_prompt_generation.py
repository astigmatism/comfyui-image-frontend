"""Durable text jobs and image preparation; existing image records are unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "b73a94f1c205"
down_revision = "a12c39e781b4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prompt_generation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("workflow_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("instance_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("queue_seq", sa.Integer(), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        *[sa.Column(name, sa.JSON(), nullable=False) for name in ("request_json", "contract_json", "compiled_graph_json", "resolved_seeds_json")],
        sa.Column("compiled_graph_sha256", sa.String(64), nullable=False),
        sa.Column("comfyui_prompt_id", sa.String(255)),
        sa.Column("prompt", sa.Text()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_prompt_runs_queue", "prompt_generation_runs", ["status", "instance_id", "queue_seq"])
    op.create_table(
        "generation_preparations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", sa.String(36), sa.ForeignKey("workflow_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("prompt_run_id", sa.String(36), sa.ForeignKey("prompt_generation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("auto_cycle_id", sa.String(36), sa.ForeignKey("auto_generation_cycles.id", ondelete="SET NULL")),
        sa.Column("activity_run_id", sa.String(36), sa.ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assistant_run_id", sa.String(36), sa.ForeignKey("prompt_assistant_runs.id", ondelete="SET NULL")),
        sa.Column("generation_id", sa.String(36), sa.ForeignKey("generations.id", ondelete="SET NULL")),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("prompt", sa.Text()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_preparation_owner_group", "generation_preparations", ["owner_id", "group_id"])
    op.create_index("ix_preparation_status", "generation_preparations", ["status"])


def downgrade():
    op.drop_table("generation_preparations")
    op.drop_table("prompt_generation_runs")
