"""Shared checkpoint identities; reset the old workflow-local tiers once."""

import sqlalchemy as sa
from alembic import op

from app.domain.checkpoint_identity import generation_checkpoint_identity_v1

revision = "c73e2a9140bd"
down_revision = "b28a6f1d903e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("checkpoint_id", sa.String(68), nullable=True))
    connection = op.get_bind()
    generations = sa.Table("generations", sa.MetaData(), autoload_with=connection)
    # Bounded batches keep older libraries from loading every frozen graph at once.
    last_id = ""
    while True:
        rows = (
            connection.execute(
                sa.select(
                    generations.c.id,
                    generations.c.resolved_contract_json,
                    generations.c.effective_controls_json,
                    generations.c.compiled_graph_json,
                    generations.c.generation_source_json,
                    generations.c.workflow_id,
                )
                .where(generations.c.id > last_id)
                .order_by(generations.c.id)
                .limit(100)
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        for row in rows:
            source = row["generation_source_json"] or {}
            identity = generation_checkpoint_identity_v1(
                row["resolved_contract_json"],
                row["effective_controls_json"],
                row["compiled_graph_json"],
                source.get("source_key") or row["workflow_id"],
            )
            if identity:
                connection.execute(
                    generations.update()
                    .where(generations.c.id == row["id"])
                    .values(checkpoint_id=identity)
                )
        last_id = rows[-1]["id"]
    connection.execute(
        sa.text("UPDATE user_preferences SET checkpoint_tiers_json = '{}', revision = revision + 1")
    )


def downgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.drop_column("checkpoint_id")
    op.execute(
        sa.text("UPDATE user_preferences SET checkpoint_tiers_json = '{}', revision = revision + 1")
    )
