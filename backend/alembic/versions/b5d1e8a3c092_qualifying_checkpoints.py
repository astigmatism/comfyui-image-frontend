"""An administrator chooses which graded checkpoints the Demo Reel develops.

Revision ID: b5d1e8a3c092
Revises: 9c5b27e0a416
"""

import sqlalchemy as sa
from alembic import op

revision = "b5d1e8a3c092"
down_revision = "9c5b27e0a416"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Until this column exists every checkpoint the rating account ranks A or B is developed for
    # every prompt slot, which is a lot of images and not a number anyone chose. The list belongs
    # to the shared configuration for the same reason the picture choice does: a Demo Reel is one
    # shared gallery, so which of its checkpoints exist at all cannot be a per-account preference.
    # NULL rather than a backfilled list, because NULL *is* the behaviour every existing row
    # already has -- develop every graded checkpoint, including one graded next week -- and any
    # list computed at migration time would freeze whatever the grading account happened to rank
    # that moment, silently turning automatic behaviour into a choice nobody made.
    # A JSON list rather than a rows-per-checkpoint table because it is read whole with the rest
    # of the configuration, never queried on its own, and replaced in one write by the settings
    # form that owns it.
    with op.batch_alter_table("demo_reel_config") as batch:
        batch.add_column(sa.Column("qualifying_checkpoint_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    # Forgetting the preference returns every installation to developing every graded checkpoint,
    # which is what it did before this column existed. Nothing is destroyed by the downgrade
    # itself, but the images the narrower list had stopped developing do not come back on their
    # own: the next reconciliation asks for them again and the idle loop generates them.
    with op.batch_alter_table("demo_reel_config") as batch:
        batch.drop_column("qualifying_checkpoint_ids")
