"""Durable account-scoped generation submission receipts."""

from alembic import op
import sqlalchemy as sa

revision = "a12c39e781b4"
down_revision = "f7c1b4e8a209"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "generation_submissions",
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(36), primary_key=True),
        sa.Column("endpoint", sa.String(16), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("outcomes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("generation_submissions")
