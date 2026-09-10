from __future__ import annotations

import builtins
from collections import deque

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import Artifact, AuditLog, Collection, Generation
from ..schemas import Collection as CollectionResponse
from ..schemas import CollectionCreate, CollectionPreview, CollectionUpdate
from .generations import GenerationService

MAX_COLLECTION_DEPTH = 5


class CollectionService:
    def __init__(self, generations: GenerationService) -> None:
        self.generations = generations

    @staticmethod
    def get_owned(session: Session, owner_id: str, collection_id: str) -> Collection:
        collection = session.scalar(
            select(Collection).where(
                Collection.id == collection_id,
                Collection.owner_id == owner_id,
            )
        )
        if collection is None:
            raise AppError("not_found", "Collection was not found.", status_code=404)
        return collection

    def list(self, session: Session, *, owner_id: str) -> builtins.list[CollectionResponse]:
        collections = list(
            session.scalars(
                select(Collection)
                .where(Collection.owner_id == owner_id)
                .order_by(Collection.created_at, Collection.id)
            )
        )
        collection_ids = [collection.id for collection in collections]
        if not collection_ids:
            return []

        generation_counts = {
            str(collection_id): int(count)
            for collection_id, count in session.execute(
                select(Generation.collection_id, func.count())
                .where(
                    Generation.owner_id == owner_id,
                    Generation.collection_id.in_(collection_ids),
                    Generation.pending_delete.is_(False),
                )
                .group_by(Generation.collection_id)
            )
        }
        previews = self._collection_previews(
            session,
            owner_id=owner_id,
            collection_ids=collection_ids,
        )
        return [
            CollectionResponse(
                id=collection.id,
                parent_id=collection.parent_id,
                name=collection.name,
                created_at=collection.created_at,
                updated_at=collection.updated_at,
                generation_count=generation_counts.get(collection.id, 0),
                previews_enabled=collection.previews_enabled,
                previews=previews.get(collection.id, []),
            )
            for collection in collections
        ]

    @staticmethod
    def _collection_previews(
        session: Session,
        *,
        owner_id: str,
        collection_ids: builtins.list[str],
    ) -> dict[str, builtins.list[CollectionPreview]]:
        ranked = (
            select(
                Generation.collection_id.label("collection_id"),
                Generation.id.label("generation_id"),
                Artifact.id.label("artifact_id"),
                func.row_number()
                .over(
                    partition_by=Generation.collection_id,
                    order_by=(
                        Generation.accepted_at.desc(),
                        Generation.id.desc(),
                        Artifact.available_at.desc(),
                        Artifact.sequence.desc(),
                        Artifact.batch_index,
                        Artifact.id.desc(),
                    ),
                )
                .label("preview_rank"),
            )
            .join(Artifact, Artifact.generation_id == Generation.id)
            .where(
                Generation.owner_id == owner_id,
                Artifact.owner_id == owner_id,
                Generation.collection_id.in_(collection_ids),
                Generation.pending_delete.is_(False),
                Artifact.kind == "image",
                Artifact.thumbnail_path.is_not(None),
            )
            .subquery()
        )
        result: dict[str, builtins.list[CollectionPreview]] = {}
        rows = session.execute(
            select(ranked)
            .where(ranked.c.preview_rank <= 4)
            .order_by(ranked.c.collection_id, ranked.c.preview_rank)
        )
        for row in rows:
            collection_id = str(row.collection_id)
            result.setdefault(collection_id, []).append(
                CollectionPreview(
                    generation_id=str(row.generation_id),
                    artifact_id=str(row.artifact_id),
                    thumbnail_url=f"/api/artifacts/{row.artifact_id}/thumbnail",
                )
            )
        return result

    def create(
        self,
        session: Session,
        *,
        owner_id: str,
        payload: CollectionCreate,
    ) -> CollectionResponse:
        if payload.parent_id is not None:
            parent = self.get_owned(session, owner_id, payload.parent_id)
            if self._level(session, owner_id=owner_id, collection=parent) >= MAX_COLLECTION_DEPTH:
                self._raise_depth()
        collection = Collection(
            owner_id=owner_id,
            parent_id=payload.parent_id,
            name=payload.name,
        )
        session.add(collection)
        session.commit()
        return self._response(session, owner_id=owner_id, collection_id=collection.id)

    def update(
        self,
        session: Session,
        *,
        owner_id: str,
        collection_id: str,
        payload: CollectionUpdate,
    ) -> CollectionResponse:
        collection = self.get_owned(session, owner_id, collection_id)
        if payload.name is not None:
            collection.name = payload.name
        if payload.previews_enabled is not None:
            collection.previews_enabled = payload.previews_enabled

        if "parent_id" in payload.model_fields_set:
            new_parent_id = payload.parent_id
            levels = self._subtree_levels(session, owner_id=owner_id, root_id=collection.id)
            descendants = {item_id for level in levels[1:] for item_id in level}
            if new_parent_id == collection.id or new_parent_id in descendants:
                raise AppError(
                    "collection_cycle",
                    "A collection cannot be moved inside itself or one of its descendants.",
                    status_code=409,
                )
            new_level = 1
            if new_parent_id is not None:
                parent = self.get_owned(session, owner_id, new_parent_id)
                new_level = self._level(session, owner_id=owner_id, collection=parent) + 1
            deepest_relative_level = len(levels) - 1
            if new_level + deepest_relative_level > MAX_COLLECTION_DEPTH:
                self._raise_depth()
            collection.parent_id = new_parent_id

        session.commit()
        return self._response(session, owner_id=owner_id, collection_id=collection.id)

    async def delete(
        self,
        session: Session,
        *,
        owner_id: str,
        collection_id: str,
    ) -> bool:
        root = self.get_owned(session, owner_id, collection_id)
        levels = self._subtree_levels(session, owner_id=owner_id, root_id=root.id)
        subtree_ids = [item_id for level in levels for item_id in level]
        collection_metadata = {
            item.id: (item.name, item.parent_id)
            for item in session.scalars(
                select(Collection).where(
                    Collection.owner_id == owner_id,
                    Collection.id.in_(subtree_ids),
                )
            )
        }
        generations = list(
            session.scalars(
                select(Generation).where(
                    Generation.owner_id == owner_id,
                    Generation.collection_id.in_(subtree_ids),
                )
            )
        )
        deleted_immediately = True
        for generation in generations:
            if not await self.generations.request_delete(session, generation):
                deleted_immediately = False

        for item_id in subtree_ids:
            name, parent_id = collection_metadata[item_id]
            session.add(
                AuditLog(
                    actor_user_id=owner_id,
                    target_type="collection",
                    target_id=item_id,
                    action="collection_deleted",
                    metadata_json={"name": name, "parent_id": parent_id},
                )
            )
        for level in reversed(levels):
            session.execute(
                delete(Collection).where(
                    Collection.owner_id == owner_id,
                    Collection.id.in_(level),
                )
            )
            session.flush()
        session.commit()
        return deleted_immediately

    def _response(
        self,
        session: Session,
        *,
        owner_id: str,
        collection_id: str,
    ) -> CollectionResponse:
        return next(
            item for item in self.list(session, owner_id=owner_id) if item.id == collection_id
        )

    def _level(self, session: Session, *, owner_id: str, collection: Collection) -> int:
        level = 1
        current = collection
        seen = {current.id}
        while current.parent_id is not None:
            current = self.get_owned(session, owner_id, current.parent_id)
            if current.id in seen:
                raise AppError(
                    "collection_cycle",
                    "A collection cannot be moved inside itself or one of its descendants.",
                    status_code=409,
                )
            seen.add(current.id)
            level += 1
            if level > MAX_COLLECTION_DEPTH:
                self._raise_depth()
        return level

    @staticmethod
    def _subtree_levels(
        session: Session,
        *,
        owner_id: str,
        root_id: str,
    ) -> builtins.list[builtins.list[str]]:
        levels: builtins.list[builtins.list[str]] = [[root_id]]
        frontier = deque([root_id])
        while frontier:
            parent_ids = list(frontier)
            frontier.clear()
            child_ids = list(
                session.scalars(
                    select(Collection.id).where(
                        Collection.owner_id == owner_id,
                        Collection.parent_id.in_(parent_ids),
                    )
                )
            )
            if not child_ids:
                break
            levels.append(child_ids)
            frontier.extend(child_ids)
            if len(levels) > MAX_COLLECTION_DEPTH:
                CollectionService._raise_depth()
        return levels

    @staticmethod
    def _raise_depth() -> None:
        raise AppError(
            "collection_depth",
            "Collections cannot be nested more than 5 levels deep.",
            status_code=409,
        )
