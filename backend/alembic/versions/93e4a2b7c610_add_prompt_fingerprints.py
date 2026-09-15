"""Add compact prompt fingerprints for consecutive gallery groups."""

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "93e4a2b7c610"
down_revision = "82bc14d6e9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generations", sa.Column("prompt_fingerprint", sa.String(64), nullable=True))
    connection = op.get_bind()
    last_id = ""
    while rows := connection.execute(
        sa.text("SELECT id, final_prompt FROM generations WHERE id > :id ORDER BY id LIMIT 500"),
        {"id": last_id},
    ).all():
        connection.execute(
            sa.text("UPDATE generations SET prompt_fingerprint = :fingerprint WHERE id = :id"),
            [{"id": row.id, "fingerprint": hashlib.sha256(row.final_prompt.encode("utf-8")).hexdigest()} for row in rows],
        )
        last_id = rows[-1].id
    with op.batch_alter_table("generations") as batch:
        batch.alter_column("prompt_fingerprint", existing_type=sa.String(64), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.drop_column("prompt_fingerprint")
