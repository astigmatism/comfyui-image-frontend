"""One library-wide choice of which checkpoint supplies LoRA display pictures.

Revision ID: 9c5b27e0a416
Revises: 3f9c2a7d5b14
"""

import sqlalchemy as sa
from alembic import op

revision = "9c5b27e0a416"
down_revision = "3f9c2a7d5b14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The choice belongs to the shared configuration because a LoRA's picture is shared: it is one
    # library-wide presentation preference, not a per-LoRA or per-account one, and it never takes
    # part in what gets generated, so no image is invalidated by it. It is nullable rather than
    # defaulted because NULL *is* a meaning -- "no preference", which is exactly how every existing
    # row behaved before this column existed -- and backfilling with a checkpoint identity would
    # silently pin those rows to whichever checkpoint happened to be first at migration time.
    # An empty string would need a translation step on every read and would collide with no such
    # value ever being produced by the checkpoint identity hash.
    with op.batch_alter_table("demo_reel_config") as batch:
        batch.add_column(sa.Column("thumbnail_checkpoint_id", sa.String(68), nullable=True))


def downgrade() -> None:
    # A downgrade simply forgets the preference: every LoRA picture returns to the highest-ranked
    # first-slot image, which is what it was derived from before this column existed. No image and
    # no file is touched here -- the pictures are re-derived by the reconciliation that runs on the
    # Demo Reel idle loop, from entries this migration never altered.
    with op.batch_alter_table("demo_reel_config") as batch:
        batch.drop_column("thumbnail_checkpoint_id")
