"""Per-LoRA Demo Reel opt-out: remember which reels an administrator switched off.

Revision ID: 3f9c2a7d5b14
Revises: 7d1a4c8e5b32
"""

import sqlalchemy as sa
from alembic import op

revision = "3f9c2a7d5b14"
down_revision = "7d1a4c8e5b32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The flag belongs to the reel rather than to the shared configuration: it is one LoRA's
    # exclusion, and it disappears with the reel row when that LoRA leaves the library, so a
    # re-added LoRA is back in the Demo Reel. No row is rewritten, and no image is touched
    # here -- switching a LoRA off erases its images at the time an administrator asks.
    with op.batch_alter_table("demo_reels") as batch:
        batch.add_column(
            sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    # Every LoRA is back in the Demo Reel: a downgrade has nowhere to record an exclusion, and
    # the images of a LoRA that was off are already gone, so its reel simply starts again.
    with op.batch_alter_table("demo_reels") as batch:
        batch.drop_column("disabled")
