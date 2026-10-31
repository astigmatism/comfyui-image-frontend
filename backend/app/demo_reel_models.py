"""Durable shared Demo Reel configuration and one current attempt per combination."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow, uuid_str


class DemoReelConfig(Base):
    __tablename__ = "demo_reel_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    rating_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    thumbnail_checkpoint_id: Mapped[str | None] = mapped_column(String(68))
    slots: Mapped[list[Any]] = mapped_column(JSON, default=list)
    recipes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    strengths: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoReel(Base):
    __tablename__ = "demo_reels"
    __table_args__ = (UniqueConstraint("library_key", "lora_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    library_key: Mapped[str] = mapped_column(String(64))
    lora_identity: Mapped[str] = mapped_column(String(68))
    collection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("collections.id", ondelete="CASCADE"), unique=True
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)


class DemoReelEntry(Base):
    __tablename__ = "demo_reel_entries"
    __table_args__ = (UniqueConstraint("reel_id", "slot_id", "checkpoint_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    reel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("demo_reels.id", ondelete="CASCADE"), index=True
    )
    slot_id: Mapped[str] = mapped_column(String(36))
    checkpoint_id: Mapped[str] = mapped_column(String(68))
    signature: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default="missing")
    generation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("generations.id", ondelete="SET NULL"), unique=True
    )
    failures: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class DemoReelTransfer(Base):
    """Receipts prevent an ambiguous copy/move response from duplicating personal copies."""

    __tablename__ = "demo_reel_transfers"
    actor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(36), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    generation_ids: Mapped[list[Any]] = mapped_column(JSON)
