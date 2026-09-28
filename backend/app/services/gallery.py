from __future__ import annotations

import copy
import errno
import logging
import re
import shutil
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zipfile import ZIP_STORED, ZipFile

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..blocking import run_blocking
from ..config import Settings
from ..errors import AppError
from ..models import (
    TERMINAL_STATUSES,
    Artifact,
    AuditLog,
    Collection,
    CollectionFavorite,
    Favorite,
    Generation,
    GenerationUpload,
    PromptAssistantRun,
    utcnow,
    uuid_str,
)
from ..schemas import (
    GalleryDeleteItem,
    GalleryDeleteResult,
    GallerySelection,
    GallerySelectionGeneration,
    GallerySelectionScope,
    GalleryTransfer,
    GalleryTransferResult,
    GalleryViewItems,
)
from .collections import MAX_COLLECTION_DEPTH, CollectionService
from .generations import GenerationService

logger = logging.getLogger(__name__)

# SQLite binds one parameter per identifier, so every IN clause is chunked.
BATCH_SIZE = 500
DOWNLOAD_STAGING_PREFIX = "gallery-download-"
DOWNLOAD_STAGING_MAX_AGE_SECONDS = 3600
_ZIP_ENTRY_OVERHEAD_BYTES = 512
_ZIP_TRAILER_BYTES = 64 * 1024
_OUT_OF_SPACE_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT, errno.EFBIG})
_DOWNLOAD_LOG = {"operation": "gallery_download"}


def _batched(ids: Iterable[str], size: int = BATCH_SIZE) -> Iterator[list[str]]:
    batch: list[str] = []
    for item in ids:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _human_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in {"bytes", "KB"} else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("Unreachable byte formatting")


def sweep_download_staging(settings: Settings) -> int:
    """Remove archives abandoned by a disconnected client before the response finished."""

    staging = settings.staging_dir
    try:
        staging.mkdir(parents=True, exist_ok=True)
        candidates = list(staging.glob(f"{DOWNLOAD_STAGING_PREFIX}*.zip"))
    except OSError:
        logger.warning("gallery_download_staging_unavailable", extra={"operation": "startup"})
        return 0
    removed = 0
    cutoff = time.time() - DOWNLOAD_STAGING_MAX_AGE_SECONDS
    for candidate in candidates:
        try:
            if candidate.stat().st_mtime > cutoff:
                continue
            candidate.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


@dataclass
class Selection:
    generations: list[Generation]
    roots: list[Collection]
    levels: dict[str, list[list[str]]]

    @property
    def subtree_ids(self) -> set[str]:
        return {item for levels in self.levels.values() for level in levels for item in level}


class GalleryService:
    def __init__(self, generations: GenerationService, collections: CollectionService) -> None:
        self.generations = generations
        self.collections = collections
        self.assets = generations.assets

    def view_items(
        self, session: Session, owner_id: str, scope: GallerySelectionScope
    ) -> GalleryViewItems:
        if scope.collection_id is not None:
            self.collections.get_owned(session, owner_id, scope.collection_id)
        favorite = (
            select(Favorite.id)
            .where(Favorite.owner_id == owner_id, Favorite.generation_id == Generation.id)
            .exists()
        )
        image_count = (
            select(func.count(Artifact.id))
            .where(
                Artifact.owner_id == owner_id,
                Artifact.generation_id == Generation.id,
                Artifact.kind == "image",
            )
            .scalar_subquery()
        )
        query = select(
            Generation.id,
            Generation.collection_id,
            Generation.status,
            image_count.label("image_count"),
            favorite.label("is_favorite"),
        ).where(
            Generation.owner_id == owner_id,
            Generation.collection_id == scope.collection_id,
            Generation.pending_delete.is_(False),
        )
        folders = select(Collection.id).where(
            Collection.owner_id == owner_id, Collection.parent_id == scope.collection_id
        )
        if scope.favorites_only:
            query = query.where(favorite)
            folders = folders.where(
                select(CollectionFavorite.id)
                .where(
                    CollectionFavorite.owner_id == owner_id,
                    CollectionFavorite.collection_id == Collection.id,
                )
                .exists()
            )
        return GalleryViewItems(
            generations=[
                GallerySelectionGeneration.model_validate(row) for row in session.execute(query)
            ],
            collection_ids=list(session.scalars(folders)),
        )

    def _selected_items(
        self, session: Session, owner_id: str, payload: GallerySelection
    ) -> tuple[list[Generation], list[Collection]]:
        # Validate the entire selection before mutation, in bounded SQL batches.
        def owned_items[Item: (Generation, Collection)](
            model: type[Item], ids: list[str]
        ) -> list[Item]:
            result: list[Item] = []
            for batch in _batched(ids):
                rows = list(
                    session.scalars(
                        select(model).where(model.owner_id == owner_id, model.id.in_(batch))
                    )
                )
                if len(rows) != len(batch):
                    raise AppError("not_found", "A selected item was not found.", status_code=404)
                result.extend(rows)
            return result

        generations = owned_items(Generation, payload.generation_ids)
        collections = owned_items(Collection, payload.collection_ids)
        if payload.scope is not None:
            scope = payload.scope
            if scope.collection_id is not None:
                self.collections.get_owned(session, owner_id, scope.collection_id)
            changed = any(
                item.collection_id != scope.collection_id or item.pending_delete
                for item in generations
            ) or any(item.parent_id != scope.collection_id for item in collections)
            if scope.favorites_only:
                for model, column, ids in (
                    (Favorite, Favorite.generation_id, payload.generation_ids),
                    (CollectionFavorite, CollectionFavorite.collection_id, payload.collection_ids),
                ):
                    for batch in _batched(ids):
                        favorites = set(
                            session.scalars(
                                select(column).where(model.owner_id == owner_id, column.in_(batch))
                            )
                        )
                        changed = changed or not set(batch) <= favorites
            if changed:
                raise AppError(
                    "selection_changed",
                    "Selected items changed location or filter membership. "
                    "Clear the selection and select again.",
                    status_code=409,
                )
        return generations, collections

    def selection(self, session: Session, owner_id: str, payload: GallerySelection) -> Selection:
        # Resolve every explicitly selected ID before making any change. A selected
        # folder subsumes descendants, including separately selected favorite cards.
        generations, collections = self._selected_items(session, owner_id, payload)
        levels = {
            item.id: self.collections._subtree_levels(session, owner_id=owner_id, root_id=item.id)
            for item in collections
        }
        descendants = {item for tree in levels.values() for level in tree[1:] for item in level}
        roots = [item for item in collections if item.id not in descendants]
        trees = {item.id: levels[item.id] for item in roots}
        covered = {item for tree in trees.values() for level in tree for item in level}
        return Selection(
            generations=[item for item in generations if item.collection_id not in covered],
            roots=roots,
            levels=trees,
        )

    def favorite(
        self, session: Session, *, owner_id: str, payload: GallerySelection
    ) -> GallerySelection:
        # Favorites bookmark the explicitly selected cards, including a child
        # selected alongside its parent. They do not recurse into folder contents.
        self._selected_items(session, owner_id, payload)
        for attempt in range(2):
            additions: list[Favorite | CollectionFavorite] = []
            for model, column, ids in (
                (Favorite, Favorite.generation_id, payload.generation_ids),
                (CollectionFavorite, CollectionFavorite.collection_id, payload.collection_ids),
            ):
                existing = {
                    item
                    for batch in _batched(ids)
                    for item in session.scalars(
                        select(column).where(model.owner_id == owner_id, column.in_(batch))
                    )
                }
                additions.extend(
                    model(owner_id=owner_id, **{column.key: item})
                    for item in ids
                    if item not in existing
                )
            session.add_all(additions)
            try:
                session.commit()
                return payload
            except IntegrityError:
                session.rollback()
                if attempt:
                    raise
        raise AssertionError("Unreachable favorite retry")

    def download(self, session: Session, *, owner_id: str, payload: GallerySelection) -> Path:
        # Resolve everything the archive needs, release the database connection, and
        # only then write to disk: assembly can take many seconds for large folders.
        entries, total_bytes = self._download_manifest(session, owner_id, payload)
        staging = self.assets.settings.staging_dir
        self._require_download_capacity(staging, entries, total_bytes)
        session.close()
        return self._write_download_archive(staging, entries)

    def _download_manifest(
        self, session: Session, owner_id: str, payload: GallerySelection
    ) -> tuple[list[tuple[str, str]], int]:
        chosen = self.selection(session, owner_id, payload)
        contents = [
            item
            for batch in _batched(chosen.subtree_ids)
            for item in session.scalars(
                select(Generation).where(
                    Generation.owner_id == owner_id,
                    Generation.collection_id.in_(batch),
                )
            )
        ]
        sources = {item.id: item for item in [*chosen.generations, *contents]}
        artifacts = [
            item
            for batch in _batched(sources)
            for item in session.scalars(
                select(Artifact)
                .where(
                    Artifact.owner_id == owner_id,
                    Artifact.generation_id.in_(batch),
                    Artifact.kind == "image",
                )
                .order_by(
                    Artifact.generation_id, Artifact.sequence, Artifact.batch_index, Artifact.id
                )
            )
        ]
        if not artifacts:
            raise AppError(
                "download_empty",
                "No images are available to download in this selection.",
                status_code=409,
            )
        folders = {
            item.id: item
            for batch in _batched(chosen.subtree_ids)
            for item in session.scalars(
                select(Collection).where(Collection.owner_id == owner_id, Collection.id.in_(batch))
            )
        }
        folder_paths: dict[str, Path] = {}
        for root in chosen.roots:
            for level in chosen.levels[root.id]:
                for item_id in level:
                    folder = folders[item_id]
                    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", folder.name)[:80].strip(" .")
                    folder_paths[item_id] = folder_paths.get(folder.parent_id or "", Path()) / (
                        f"{name or 'Collection'}-{folder.id}"
                    )
        entries: list[tuple[str, str]] = []
        total_bytes = 0
        for artifact in artifacts:
            generation = sources[artifact.generation_id]
            directory = folder_paths.get(generation.collection_id or "", Path())
            suffix = Path(artifact.storage_path).suffix
            archive_name = (
                directory / f"generation-{generation.id}" / f"image-{artifact.id}{suffix}"
            )
            entries.append((artifact.storage_path, archive_name.as_posix()))
            total_bytes += max(0, int(artifact.byte_size or 0))
        return entries, total_bytes

    def _require_download_capacity(
        self, staging: Path, entries: list[tuple[str, str]], total_bytes: int
    ) -> None:
        settings = self.assets.settings
        # ZIP_STORED copies every image verbatim, so the archive is the sum of its
        # images plus a small per-entry header.
        required = total_bytes + len(entries) * _ZIP_ENTRY_OVERHEAD_BYTES + _ZIP_TRAILER_BYTES
        if required > settings.download_max_bytes:
            raise AppError(
                "download_too_large",
                f"This selection would produce about {_human_bytes(required)}, more than the "
                f"{_human_bytes(settings.download_max_bytes)} download limit. "
                "Download fewer items, or one folder at a time.",
                status_code=507,
                details={"required_bytes": required, "limit_bytes": settings.download_max_bytes},
            )
        staging.mkdir(parents=True, exist_ok=True)
        available = shutil.disk_usage(staging).free - settings.download_free_space_margin_bytes
        if required > available:
            raise AppError(
                "download_too_large",
                f"This selection needs about {_human_bytes(required)} of temporary space and "
                f"only {_human_bytes(max(0, available))} is free. "
                "Download fewer items, or one folder at a time.",
                status_code=507,
                details={"required_bytes": required, "available_bytes": max(0, available)},
            )

    def _write_download_archive(self, staging: Path, entries: list[tuple[str, str]]) -> Path:
        staging.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            prefix=DOWNLOAD_STAGING_PREFIX, suffix=".zip", dir=staging, delete=False
        ) as temp:
            path = Path(temp.name)
        try:
            # Image formats are already compressed. Build on disk so large folder
            # downloads do not require holding their contents in server memory.
            written = skipped = 0
            with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
                for storage_path, archive_name in entries:
                    try:
                        source = self.assets.open(storage_path)
                    except AppError:
                        # A single pruned or missing file must not fail the whole archive.
                        skipped += 1
                        continue
                    archive.write(source, arcname=archive_name)
                    written += 1
            if skipped:
                logger.warning(
                    "gallery_download_skipped_missing_artifacts=%d", skipped, extra=_DOWNLOAD_LOG
                )
            if not written:
                raise AppError(
                    "download_empty",
                    "No images are available to download in this selection.",
                    status_code=409,
                )
            return path
        except OSError as error:
            path.unlink(missing_ok=True)
            if error.errno in _OUT_OF_SPACE_ERRNOS:
                raise AppError(
                    "download_failed",
                    "The appliance ran out of temporary space while preparing this download. "
                    "Download fewer items, or one folder at a time.",
                    status_code=507,
                ) from error
            logger.exception("gallery_download_archive_failed", extra=_DOWNLOAD_LOG)
            raise AppError(
                "download_failed",
                "The download could not be prepared. Try again, or select fewer items.",
                status_code=500,
            ) from error
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def transfer(
        self, session: Session, *, owner_id: str, payload: GalleryTransfer
    ) -> GalleryTransferResult:
        chosen = self.selection(session, owner_id, payload)
        destination = (
            self.collections.get_owned(session, owner_id, payload.collection_id)
            if payload.collection_id is not None
            else None
        )
        if destination and destination.id in chosen.subtree_ids:
            raise AppError(
                "collection_cycle",
                "Choose a destination outside the selected collections and their contents.",
                status_code=409,
            )
        destination_level = (
            self.collections._level(session, owner_id=owner_id, collection=destination)
            if destination
            else 0
        )
        for levels in chosen.levels.values():
            if destination_level + len(levels) > MAX_COLLECTION_DEPTH:
                self.collections._raise_depth()

        if payload.operation == "move":
            for generation in chosen.generations:
                generation.collection_id = payload.collection_id
            for collection in chosen.roots:
                collection.parent_id = payload.collection_id
            session.add(
                AuditLog(
                    actor_user_id=owner_id,
                    target_type="gallery",
                    target_id=uuid_str(),
                    action="gallery_moved",
                    metadata_json={
                        "generation_ids": [item.id for item in chosen.generations],
                        "collection_ids": [item.id for item in chosen.roots],
                        "destination_id": payload.collection_id,
                    },
                )
            )
            session.commit()
            return GalleryTransferResult(
                operation="move",
                generation_ids=[item.id for item in chosen.generations],
                collection_ids=[item.id for item in chosen.roots],
            )

        contents = [
            item
            for batch in _batched(chosen.subtree_ids)
            for item in session.scalars(
                select(Generation).where(
                    Generation.owner_id == owner_id,
                    Generation.collection_id.in_(batch),
                )
            )
        ]
        sources = [*chosen.generations, *contents]
        if any(item.status not in TERMINAL_STATUSES or item.pending_delete for item in sources):
            raise AppError(
                "copy_generation_active",
                "Wait for active generations to finish before copying them or their collections.",
                status_code=409,
            )
        # Copy has a single database commit. Failed file copies remove only the new
        # files; originals and their thumbnails are never shared with a duplicate.
        created_paths: list[str] = []
        collection_map: dict[str, str] = {}
        generation_ids: list[str] = []
        try:
            for root in chosen.roots:
                for level in chosen.levels[root.id]:
                    for source_id in level:
                        source = self.collections.get_owned(session, owner_id, source_id)
                        clone = Collection(
                            id=uuid_str(),
                            owner_id=owner_id,
                            parent_id=(
                                payload.collection_id
                                if source_id == root.id
                                else collection_map[source.parent_id or ""]
                            ),
                            name=source.name,
                            previews_enabled=source.previews_enabled,
                        )
                        session.add(clone)
                        session.flush()
                        collection_map[source_id] = clone.id
                        if session.scalar(
                            select(CollectionFavorite.id).where(
                                CollectionFavorite.owner_id == owner_id,
                                CollectionFavorite.collection_id == source_id,
                            )
                        ):
                            session.add(
                                CollectionFavorite(owner_id=owner_id, collection_id=clone.id)
                            )
            for source_generation in sources:
                clone_id = self._copy_generation(
                    session,
                    source_generation,
                    collection_map.get(
                        source_generation.collection_id or "", payload.collection_id
                    ),
                    created_paths,
                )
                generation_ids.append(clone_id)
            session.commit()
        except BaseException:
            session.rollback()
            self.assets.delete_paths(created_paths)
            raise
        return GalleryTransferResult(
            operation="copy",
            generation_ids=generation_ids,
            collection_ids=[collection_map[item.id] for item in chosen.roots],
        )

    def _copy_generation(
        self,
        session: Session,
        source: Generation,
        collection_id: str | None,
        created_paths: list[str],
    ) -> str:
        values = {
            column.key: copy.deepcopy(getattr(source, column.key))
            for column in Generation.__table__.columns
        }
        values.update(
            id=uuid_str(),
            collection_id=collection_id,
            correlation_id=uuid_str(),
            comfyui_client_id=uuid_str(),
            comfyui_prompt_id=None,
            execution_timing_json=None,
            timing_batch_id=None,
            accepted_at=utcnow(),
            updated_at=utcnow(),
            dispatched_at=None,
            started_at=None,
            completed_at=utcnow(),
            cancel_requested_at=None,
            pending_delete=False,
            internal_diagnostics_json={
                "copied_from_generation_id": source.id,
                "comfyui_source_cleanup_complete": True,
            },
        )
        clone = Generation(**values)
        session.add(clone)
        session.flush()
        artifacts = list(
            session.scalars(select(Artifact).where(Artifact.generation_id == source.id))
        )
        artifact_ids = {item.id: uuid_str() for item in artifacts}
        copied_artifacts: list[tuple[Artifact, Artifact]] = []
        for artifact in artifacts:
            stored = self.assets.store_artifact(
                self.assets.read(artifact.storage_path), generation_id=clone.id, kind=artifact.kind
            )
            created_paths.extend(
                path for path in (stored.relative_path, stored.thumbnail_path) if path
            )
            artifact_values = {
                column.key: copy.deepcopy(getattr(artifact, column.key))
                for column in Artifact.__table__.columns
            }
            artifact_values.update(
                id=artifact_ids[artifact.id],
                generation_id=clone.id,
                storage_path=stored.relative_path,
                thumbnail_path=stored.thumbnail_path,
                parent_artifact_id=None,
                source_filename=None,
                source_subfolder=None,
                source_type=None,
            )
            copied = Artifact(**artifact_values)
            session.add(copied)
            copied_artifacts.append((artifact, copied))
        session.flush()
        for original, copied in copied_artifacts:
            copied.parent_artifact_id = artifact_ids.get(original.parent_artifact_id or "")
        clone.canonical_artifact_id = artifact_ids.get(source.canonical_artifact_id or "")
        clone.best_available_artifact_id = artifact_ids.get(source.best_available_artifact_id or "")
        clone.declared_outputs_json = _remap_artifacts(source.declared_outputs_json, artifact_ids)
        clone.unmapped_outputs_json = _remap_artifacts(source.unmapped_outputs_json, artifact_ids)
        for upload in session.scalars(
            select(GenerationUpload).where(GenerationUpload.generation_id == source.id)
        ):
            # Input uploads already use reference-counted lifetime management.
            session.add(
                GenerationUpload(
                    generation_id=clone.id,
                    upload_id=upload.upload_id,
                    control_id=upload.control_id,
                    sha256=upload.sha256,
                )
            )
        for run in session.scalars(
            select(PromptAssistantRun).where(PromptAssistantRun.generation_id == source.id)
        ):
            run_values = {
                column.key: copy.deepcopy(getattr(run, column.key))
                for column in PromptAssistantRun.__table__.columns
            }
            run_values.update(id=uuid_str(), generation_id=clone.id)
            session.add(PromptAssistantRun(**run_values))
        if session.scalar(
            select(Favorite.id).where(
                Favorite.owner_id == source.owner_id, Favorite.generation_id == source.id
            )
        ):
            session.add(Favorite(owner_id=source.owner_id, generation_id=clone.id))
        session.add(
            AuditLog(
                actor_user_id=source.owner_id,
                target_type="generation",
                target_id=clone.id,
                action="generation_copied",
                metadata_json={"source_generation_id": source.id},
            )
        )
        return clone.id

    async def delete(self, *, owner_id: str, payload: GallerySelection) -> GalleryDeleteResult:

        def load_targets() -> list[tuple[str, str]]:
            with self.generations.session_factory() as fresh:
                chosen = self.selection(fresh, owner_id, payload)
                return [("generation", item.id) for item in chosen.generations] + [
                    ("collection", item.id) for item in chosen.roots
                ]

        targets = await run_blocking(load_targets)
        results: list[GalleryDeleteItem] = []
        for kind, item_id in targets:
            try:
                if kind == "generation":
                    deleted = await self.generations.delete_owned(owner_id, item_id)
                else:
                    deleted = await self.collections.delete(
                        owner_id=owner_id, collection_id=item_id
                    )
                results.append(
                    GalleryDeleteItem(
                        kind=kind, id=item_id, status="deleted" if deleted else "pending"
                    )
                )
            except AppError as error:
                results.append(
                    GalleryDeleteItem(kind=kind, id=item_id, status="failed", message=error.message)
                )
        return GalleryDeleteResult(items=results)


def _remap_artifacts(value: Any, artifact_ids: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _remap_artifacts(item, artifact_ids) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_artifacts(item, artifact_ids) for item in value]
    if isinstance(value, str):
        if value in artifact_ids:
            return artifact_ids[value]
        for original, replacement in artifact_ids.items():
            if value.startswith(f"/api/artifacts/{original}/"):
                return value.replace(original, replacement, 1)
    return copy.deepcopy(value)
