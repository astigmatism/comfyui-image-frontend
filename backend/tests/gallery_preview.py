"""Isolated, disposable gallery preview using the production app and fake runtimes.

Run with scripts/gallery-preview.sh. No household ComfyUI services are contacted.
"""

from __future__ import annotations

import copy
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import uvicorn
from app.config import Settings
from app.main import create_app
from app.models import (
    Artifact,
    ArtifactState,
    Collection,
    ComfyUIInstanceHealth,
    Favorite,
    Generation,
    GenerationStatus,
    User,
    UserPreference,
    utcnow,
)
from app.schemas import GenerationCreate
from fastapi import FastAPI
from sqlalchemy import select

from tests.fake_services import LiveFakeServer, make_png
from tests.publication_fixtures import build_publication_bundle


def main() -> None:
    primary = LiveFakeServer().start()
    secondary = LiveFakeServer().start()
    primary.state.workflow_files.update(build_publication_bundle("moody").files)
    settings = Settings(
        app_title="ImageGen V2",
        data_dir=Path(os.getenv("CIF_DATA_DIR", "/data")),
        database_path=Path(os.getenv("CIF_DATABASE_PATH", "/data/app.db")),
        session_secret=secrets.token_urlsafe(48),
        bootstrap_admin_username="preview",
        bootstrap_admin_temporary_password="GalleryPreview123!",
        comfyui_instances=[
            {"id": "primary", "label": "Primary", "base_url": primary.base_url},
            {"id": "secondary", "label": "Secondary", "base_url": secondary.base_url},
        ],
        comfyui_default_instance_id="primary",
        ollama_base_url=primary.base_url,
        frontend_dist=Path("/app/frontend/dist"),
        dispatch_poll_seconds=0.1,
        external_health_interval_seconds=5,
        log_level="WARNING",
    )
    app = create_app(settings)
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def preview_lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with lifespan(application):
            container = app.state.container
            await container.registry.refresh()
            with container.db.session_factory() as session:
                user = session.scalar(select(User).where(User.username == "preview"))
                assert user is not None
                user.must_change_password = False
                session.commit()
                if not session.scalar(select(Generation.id).limit(1)):
                    seed_gallery(container, session, user)
            yield

    app.router.lifespan_context = preview_lifespan
    try:
        # Docker publishes this port on host loopback only.
        uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None, timeout_graceful_shutdown=5)  # noqa: S104
    finally:
        primary.stop()
        secondary.stop()


def seed_gallery(container, session, user) -> None:
    profile = next(
        item
        for item in container.registry.list_current(session)
        if "Moody Krea" in item.display_name
    )
    session.merge(ComfyUIInstanceHealth(instance_id="primary", available=True))
    collections = [
        Collection(owner_id=user.id, name=name)
        for name in ["Landscapes", "Portrait studies", "Archive"]
    ]
    session.add_all(collections)
    session.flush()
    nested = Collection(owner_id=user.id, name="Keepers", parent_id=collections[0].id)
    session.add(nested)
    session.merge(UserPreference(user_id=user.id, gallery_scale=20))
    session.flush()
    samples = sorted(Path("/preview-assets").glob("*.jpg"))
    for index in range(16):
        filed = (
            collections[0].id
            if index in (0, 1, 2)
            else collections[1].id
            if index in (3, 4)
            else nested.id
            if index == 5
            else None
        )
        generation, _ = container.generations._prepare_accept(
            session,
            user=user,
            request=GenerationCreate(
                profile_id=profile.id,
                collection_id=filed,
                parameters={
                    "prompt": f"Landscape study {index + 1}, cinematic light, natural detail",
                    "seed": str(4200 + index),
                    "width": 1024,
                    "height": 1024,
                    "enable_seedvr2_upscale": False,
                    "checkpoint": "v5_bf16",
                },
            ),
        )
        image = (
            samples[index % len(samples)].read_bytes()
            if samples
            else make_png(f"Sample {index + 1}", width=768, height=768)
        )
        artifact_list = []
        for batch_index in range(3 if index == 14 else 1):
            stored = container.assets.store_artifact(image, generation_id=generation.id)
            artifact = Artifact(
                generation_id=generation.id,
                owner_id=user.id,
                output_id="final",
                role="final",
                kind="image",
                state=ArtifactState.FINAL,
                storage_path=stored.relative_path,
                thumbnail_path=stored.thumbnail_path,
                mime_type=stored.mime_type,
                byte_size=stored.byte_size,
                width=stored.width,
                height=stored.height,
                sha256=stored.sha256,
                canonical=batch_index == 0,
                best_available=batch_index == 0,
                batch_index=batch_index,
            )
            session.add(artifact)
            session.flush()
            artifact_list.append(artifact)
        generation.status = GenerationStatus.SUCCEEDED
        generation.accepted_at = utcnow() - timedelta(minutes=18 - index)
        generation.started_at = generation.accepted_at + timedelta(seconds=1)
        generation.completed_at = generation.started_at + timedelta(seconds=24 + index)
        generation.canonical_artifact_id = artifact_list[0].id
        generation.best_available_artifact_id = artifact_list[0].id
        generation.artifact_count = len(artifact_list)
        generation.final_artifact_count = len(artifact_list)
        generation.declared_outputs_json = {
            "final": [
                container.generations.artifact_summary(item).model_dump(mode="json")
                for item in artifact_list
            ]
        }
        generation.internal_diagnostics_json = {"comfyui_source_cleanup_complete": True}
        generation.effective_controls_json = copy.deepcopy(generation.effective_controls_json)
        if index in (0, 12, 14):
            session.add(Favorite(owner_id=user.id, generation_id=generation.id))
    session.commit()


if __name__ == "__main__":
    main()
