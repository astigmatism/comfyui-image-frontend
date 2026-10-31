"""Every LoRA's display image is derived from that LoRA's Demo Reel first slot.

The rule is deliberately short. A favorited image of the reel's first slot supplies the picture,
and the earliest favorite keeps it when several are marked. Nothing there is favorited, so the
picture comes from the checkpoint an administrator named in **Demo Reel settings**; and when that
checkpoint has no image in the slot — it is not installed in that family's workflow, its image is
still waiting to be generated, or no checkpoint was named at all — the picture comes from the
image that slot lists first. Favorites are private while a LoRA's display image is shared, so a
favorite by any signed-in user supplies it. Nothing is uploaded any more, which makes the Demo
Reel the only writer of ``lora_library_images``; this module is that writer.

Within a slot, checkpoints are ordered as the reel gallery orders them: the rating account's saved
A then B checkpoint grades, falling back to checkpoint ID order once that account is gone. Naming
a checkpoint changes only which of those images is chosen, never what is generated.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..demo_reel_models import DemoReel, DemoReelConfig, DemoReelEntry
from ..models import (
    Artifact,
    Favorite,
    Generation,
    GenerationStatus,
    LoraLibraryImage,
    User,
    UserPreference,
    UserState,
)
from .assets import AssetStore, StoredImage

if TYPE_CHECKING:
    from ..container import AppContainer

logger = logging.getLogger(__name__)

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Source:
    """The reel image a LoRA's display image is, or should be, derived from."""

    generation_id: str
    artifact_id: str
    path: str

    @property
    def marker(self) -> tuple[str, str]:
        return self.generation_id, self.artifact_id


@dataclass(frozen=True)
class Option:
    """One first-slot image that could supply the picture, with everything the rule weighs."""

    source: Source
    rank: int
    checkpoint_id: str
    favorited_at: datetime | None
    priority: int

    @property
    def key(self) -> tuple[int, int, str]:
        """Ordering when nothing is favorited: the named checkpoint, then gallery order."""

        return self.priority, self.rank, self.checkpoint_id


@dataclass
class _Job:
    identity: str
    source: Source | None
    stored: StoredImage | None
    superseded: list[str]


def first_slot_id(config: DemoReelConfig) -> str | None:
    """The slot whose images supply LoRA display images, or None while nothing is set up."""

    slot = config.slots[0] if config.slots else None
    slot_id = slot.get("id") if isinstance(slot, dict) else None
    return str(slot_id) if slot_id else None


def rank_order(session: Session, config: DemoReelConfig, entries: list[DemoReelEntry]) -> list[str]:
    """Checkpoint display order, mirroring ``DemoReelService._matrix``."""

    preference = (
        session.get(UserPreference, config.rating_user_id) if config.rating_user_id else None
    )
    ratings: dict[str, Any] = preference.checkpoint_tiers_json if preference else {}
    owner = session.get(User, config.rating_user_id) if config.rating_user_id else None
    if owner is not None and owner.state == UserState.ACTIVE:
        return list(dict.fromkeys([*ratings.get("A", []), *ratings.get("B", [])]))
    return sorted({str(entry.checkpoint_id) for entry in entries})


def rank_of(order: list[str], checkpoint_id: str) -> int:
    try:
        return order.index(checkpoint_id)
    except ValueError:
        # A checkpoint outside the rating account's A/B grades is not in the gallery.
        return len(order)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def favorite_times(session: Session, generation_ids: list[str]) -> dict[str, datetime]:
    """Each generation's earliest favorite, whichever account made it."""

    if not generation_ids:
        return {}
    rows = session.execute(
        select(Favorite.generation_id, func.min(Favorite.created_at))
        .where(Favorite.generation_id.in_(generation_ids))
        .group_by(Favorite.generation_id)
    ).all()
    return {str(generation_id): aware(created) for generation_id, created in rows}


def _artifact_file(session: Session, generation: Generation, assets: Any) -> Artifact | None:
    """The generation's final image, when its stored file can still be read."""

    artifact = (
        session.get(Artifact, generation.canonical_artifact_id)
        if generation.canonical_artifact_id
        else None
    )
    if artifact is None or artifact.generation_id != generation.id:
        return None
    relative = artifact.thumbnail_path or artifact.storage_path
    if not relative:
        return None
    try:
        path = assets.open(relative)
    except Exception:
        return None
    return artifact if path.is_file() else None


def source_image(
    session: Session, *, config: DemoReelConfig, reels: list[DemoReel], assets: Any
) -> Option | None:
    """Resolve the reel image behind one LoRA's display image.

    The winner is the earliest favorited image of the reels' first slot, and otherwise the image
    of the checkpoint named in the configuration, falling back to the first image that slot lists.
    """

    slot_id = first_slot_id(config)
    if not slot_id or not reels:
        return None
    entries = list(
        session.scalars(
            select(DemoReelEntry).where(
                DemoReelEntry.reel_id.in_([reel.id for reel in reels]),
                DemoReelEntry.slot_id == slot_id,
                DemoReelEntry.state == "ready",
                DemoReelEntry.generation_id.is_not(None),
            )
        )
    )
    if not entries:
        return None
    orders = {
        str(reel.id): rank_order(session, config, [e for e in entries if e.reel_id == reel.id])
        for reel in reels
    }
    generations = {
        str(generation.id): generation
        for generation in session.scalars(
            select(Generation).where(
                Generation.id.in_([str(entry.generation_id) for entry in entries])
            )
        )
    }
    favorites = favorite_times(session, sorted(generations))
    options: list[Option] = []
    for entry in entries:
        generation = generations.get(str(entry.generation_id))
        if (
            generation is None
            or generation.status != GenerationStatus.SUCCEEDED
            or generation.pending_delete
        ):
            continue
        artifact = _artifact_file(session, generation, assets)
        if artifact is None:
            continue
        options.append(
            Option(
                source=Source(
                    generation_id=str(generation.id),
                    artifact_id=str(artifact.id),
                    path=artifact.thumbnail_path or artifact.storage_path,
                ),
                rank=rank_of(orders.get(str(entry.reel_id), []), str(entry.checkpoint_id)),
                checkpoint_id=str(entry.checkpoint_id),
                favorited_at=favorites.get(str(generation.id)),
                priority=0 if str(entry.checkpoint_id) == config.thumbnail_checkpoint_id else 1,
            )
        )
    if not options:
        return None
    favorited = sorted(
        (option for option in options if option.favorited_at is not None),
        key=lambda option: (option.favorited_at or EPOCH, option.rank, option.checkpoint_id),
    )
    return (favorited or sorted(options, key=lambda option: option.key))[0]


def _accepted(row: LoraLibraryImage | None, source: Source) -> bool:
    """Whether the stored image already is the one this LoRA's reels agree on.

    One LoRA identity can be listed by more than one library, and each of those reels has its own
    first slot. The image is merged across them before it is compared, so the shared picture is
    checked against exactly one image: comparing it against every reel's own choice would let a
    sweep rewrite the same file on every pass to satisfy the other reel.
    """

    if row is None or not row.storage_path:
        return False
    return (row.derived_generation_id, row.derived_artifact_id) == source.marker


def _outstanding(
    planned: dict[tuple[str, str], tuple[LoraLibraryImage | None, Option | None]],
) -> dict[str, tuple[LoraLibraryImage | None, Option | None]]:
    """The work left to do, merged from each reel's view onto the shared per-identity row."""

    grouped: dict[str, list[tuple[LoraLibraryImage | None, Option | None]]] = {}
    for (_, identity), entry in sorted(planned.items()):
        grouped.setdefault(identity, []).append(entry)
    changes: dict[str, tuple[LoraLibraryImage | None, Option | None]] = {}
    for identity, entries in grouped.items():
        row = next((stored for stored, _ in entries if stored is not None), None)
        options = [option for _, option in entries if option is not None]
        if options:
            best = min(options, key=lambda option: option.key)
            if not _accepted(row, best.source):
                changes[identity] = (row, best)
        elif row is not None and (row.storage_path or row.derived_generation_id):
            changes[identity] = (row, None)
    return changes


def plan(
    session: Session,
    *,
    config: DemoReelConfig,
    assets: Any,
    identities: set[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], tuple[LoraLibraryImage | None, Option | None]]:
    """Each reel's stored display image beside the image it should now show.

    Keys are ``(library_key, lora_identity)``, one entry per reel, although the picture
    itself is shared per LoRA identity and merged by :func:`refresh`. ``identities``
    narrows the work to the LoRAs named by their ``(library_key, lora_identity)`` and
    always considers every reel of those LoRAs, because their rows are the same row.

    A stored row that no reel claims is included with ``None``, so a hand-uploaded file
    left over from before derivation, or one whose LoRA has left the library, loses its
    file instead of lingering unexplained.
    """

    if first_slot_id(config) is None and identities is None:
        return {}
    wanted = {identity for _, identity in identities} if identities is not None else None
    reels: dict[tuple[str, str], list[DemoReel]] = {}
    for reel in session.scalars(select(DemoReel).order_by(DemoReel.library_key)):
        if wanted is not None and reel.lora_identity not in wanted:
            continue
        reels.setdefault((reel.library_key, reel.lora_identity), []).append(reel)
    rows = {row.lora_identity: row for row in session.scalars(select(LoraLibraryImage))}
    if identities is None:
        # Rows no reel claims still hold files, so they are planned against no source.
        claimed = {identity for _, identity in reels}
        for identity in sorted(rows):
            if identity not in claimed and (
                rows[identity].storage_path or rows[identity].derived_generation_id
            ):
                reels[("", identity)] = []
    return {
        key: (
            rows.get(key[1]),
            source_image(session, config=config, reels=group, assets=assets),
        )
        for key, group in reels.items()
    }


def _encode(assets: AssetStore, source: Source) -> StoredImage:
    """Re-encode the small shared WebP from the reel image's own stored file."""

    with open(assets.open(source.path), "rb") as handle:
        return assets.store_lora_thumbnail(io.BytesIO(handle.read()))


def refresh(container: AppContainer, *, identities: set[tuple[str, str]] | None = None) -> int:
    """Bring LoRA display images in line with their Demo Reels.

    Cheap to call often: a LoRA whose reel still points at the stored image is left
    completely alone, so the periodic sweep only encodes files that really changed.
    A saved thumbnail-checkpoint preference is picked up exactly the same way, one small
    file per LoRA, with nothing in the reel regenerated or invalidated.
    Returns the number of LoRAs whose display image was replaced or cleared.
    """

    with container.db.session_factory() as session:
        config = session.get(DemoReelConfig, 1)
        if config is None:
            return 0
        planned = plan(session, config=config, assets=container.assets, identities=identities)
        changes = _outstanding(planned)
    if not changes:
        return 0

    jobs: list[_Job] = []
    for identity, (row, option) in sorted(changes.items()):
        source = option.source if option is not None else None
        superseded = [row.storage_path] if row is not None and row.storage_path else []
        stored: StoredImage | None = None
        if source is not None:
            try:
                stored = _encode(container.assets, source)
            except Exception:
                # A reel image that cannot be read leaves the current display image in
                # place; the next sweep retries once the source or its file returns.
                logger.warning("lora_thumbnail_source_unreadable", extra={"id": identity})
                continue
        jobs.append(_Job(identity=identity, source=source, stored=stored, superseded=superseded))
    if not jobs:
        return 0

    written: list[str] = []
    try:
        with container.db.session_factory() as session:
            # One write transaction covers the whole batch, as a thumbnail upload used to.
            session.execute(text("BEGIN IMMEDIATE"))
            for job in jobs:
                row = session.get(LoraLibraryImage, job.identity)
                if row is None:
                    row = LoraLibraryImage(lora_identity=job.identity, revision=0)
                    session.add(row)
                row.storage_path = job.stored.relative_path if job.stored else None
                row.derived_generation_id = job.source.generation_id if job.source else None
                row.derived_artifact_id = job.source.artifact_id if job.source else None
                row.revision += 1
            session.commit()
        written = [job.stored.relative_path for job in jobs if job.stored]
    except BaseException:
        container.assets.delete_paths([job.stored.relative_path for job in jobs if job.stored])
        raise
    container.assets.delete_paths(
        [path for job in jobs for path in job.superseded if path not in written]
    )
    return len(jobs)


def sweep(container: AppContainer) -> int:
    """Reconcile every LoRA display image; called by the Demo Reel idle loop."""

    return refresh(container)
