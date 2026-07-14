from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ..errors import AppError
from ..models import (
    ACTIVE_STATUSES,
    Artifact,
    AuditLog,
    Generation,
    GenerationStatus,
    GenerationUpload,
    Upload,
    User,
    UserRole,
    UserState,
)
from .assets import AssetStore
from .auth import AuthService
from .comfyui import ComfyUIAdapter


class UserDeletionService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        auth: AuthService,
        comfyui: ComfyUIAdapter,
        assets: AssetStore,
    ) -> None:
        self.session_factory = session_factory
        self.auth = auth
        self.comfyui = comfyui
        self.assets = assets

    async def delete_user(self, *, actor_id: str, target_id: str) -> None:
        prompt_ids: list[str] = []
        with self.session_factory() as session:
            actor = session.get(User, actor_id)
            target = session.get(User, target_id)
            if actor is None or actor.role != UserRole.ADMIN:
                raise AppError("forbidden", "Administrator access is required.", status_code=403)
            if target is None:
                raise AppError("not_found", "User was not found.", status_code=404)
            if target.role != UserRole.USER or target.is_bootstrap:
                raise AppError(
                    "forbidden", "The bootstrap administrator cannot be deleted.", status_code=403
                )
            target.state = UserState.DELETING
            self.auth.revoke_user_sessions(session, target)
            generations = list(
                session.scalars(select(Generation).where(Generation.owner_id == target.id))
            )
            for generation in generations:
                if generation.status == GenerationStatus.QUEUED:
                    generation.status = GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS
                    generation.completed_at = datetime.now(UTC)
                elif generation.status in ACTIVE_STATUSES:
                    generation.status = GenerationStatus.CANCEL_REQUESTED
                    generation.cancel_requested_at = datetime.now(UTC)
                    if generation.comfyui_prompt_id:
                        prompt_ids.append(generation.comfyui_prompt_id)
            session.commit()
        for prompt_id in prompt_ids:
            with suppress(Exception):
                await self.comfyui.cancel(prompt_id, running=True)

        for _ in range(20):
            with self.session_factory() as session:
                remaining = list(
                    session.scalars(
                        select(Generation).where(
                            Generation.owner_id == target_id,
                            Generation.status.in_(list(ACTIVE_STATUSES)),
                        )
                    )
                )
            if not remaining:
                break
            await asyncio.sleep(0.25)

        with self.session_factory() as session:
            target = session.get(User, target_id)
            if target is None:
                return
            remaining = list(
                session.scalars(
                    select(Generation).where(
                        Generation.owner_id == target_id,
                        Generation.status.in_(list(ACTIVE_STATUSES)),
                    )
                )
            )
            for generation in remaining:
                # A history/queue reconciliation attempt already occurred through cancellation and
                # the queue worker. If no terminal state arrived, preserve explicit uncertainty.
                generation.status = GenerationStatus.INTERRUPTED
                generation.error_code = "execution_interrupted"
                generation.error_message = "Execution was interrupted during account deletion."
                generation.completed_at = datetime.now(UTC)
            session.flush()
            paths = [
                path
                for artifact in session.scalars(
                    select(Artifact).where(Artifact.owner_id == target_id)
                )
                for path in (artifact.storage_path, artifact.thumbnail_path)
                if path
            ]
            paths.extend(
                upload.storage_path
                for upload in session.scalars(select(Upload).where(Upload.owner_id == target_id))
            )
            # Uploads are owned by the user and use ON DELETE CASCADE, while
            # generation_uploads deliberately RESTRICT direct upload deletion.
            # Remove the ownership links first so SQLite can cascade both sides
            # of the account cleanup without an immediate RESTRICT violation.
            generation_ids = select(Generation.id).where(Generation.owner_id == target_id)
            session.execute(
                delete(GenerationUpload).where(GenerationUpload.generation_id.in_(generation_ids))
            )
            session.delete(target)
            session.add(
                AuditLog(
                    actor_user_id=actor_id,
                    target_type="user",
                    target_id=target_id,
                    action="user_deleted",
                    metadata_json={},
                )
            )
            session.commit()
        self.assets.delete_paths(paths)
