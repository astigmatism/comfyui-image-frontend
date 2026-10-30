"""Demo Reel-derived LoRA thumbnails: record the image each shared thumbnail came from.

Revision ID: 7d1a4c8e5b32
Revises: c43d89e12a70
"""

import sqlalchemy as sa
from alembic import op

revision = "7d1a4c8e5b32"
down_revision = "c43d89e12a70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Thumbs are derived from each LoRA's Demo Reel from here on. The marker columns
    # hold the reel image a stored file was made from; a row that predates derivation
    # has no marker, which is what tells the first reconciliation pass that its
    # hand-uploaded file is obsolete, so it can delete the file and replace the row.
    with op.batch_alter_table("lora_library_images") as batch:
        batch.add_column(sa.Column("derived_generation_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("derived_artifact_id", sa.String(36), nullable=True))


def downgrade() -> None:
    # The derived files stay readable: a downgrade returns to hand-managed thumbnails,
    # which simply show the last derived image until somebody replaces it.
    with op.batch_alter_table("lora_library_images") as batch:
        batch.drop_column("derived_artifact_id")
        batch.drop_column("derived_generation_id")
