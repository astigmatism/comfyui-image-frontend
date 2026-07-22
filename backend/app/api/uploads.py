from __future__ import annotations

import asyncio
from pathlib import PurePath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..dependencies import (
    AuthContext,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..errors import AppError
from ..models import Artifact, Upload, UploadKind
from ..schemas import UploadResponse
from ..services.assets import AssetStore, StoredImage

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _insert_upload_metadata(
    session_factory: sessionmaker[Session],
    *,
    owner_id: str,
    kind: UploadKind,
    original_name: str,
    stored: StoredImage,
) -> UploadResponse:
    """Insert metadata with a session created and consumed entirely in this thread."""

    with session_factory() as session:
        upload = Upload(
            owner_id=owner_id,
            kind=kind,
            storage_path=stored.relative_path,
            original_name=original_name,
            mime_type=stored.mime_type,
            byte_size=stored.byte_size,
            width=stored.width,
            height=stored.height,
            sha256=stored.sha256,
        )
        session.add(upload)
        session.flush()
        response = UploadResponse(
            id=upload.id,
            kind=upload.kind.value,
            mime_type=upload.mime_type,
            byte_size=upload.byte_size,
            width=upload.width,
            height=upload.height,
            sha256=upload.sha256,
            preview_url=f"/api/uploads/{upload.id}/content",
        )
        session.commit()
        return response


async def _cleanup_unowned_upload(assets: AssetStore, stored: StoredImage) -> None:
    cleanup = asyncio.create_task(assets.delete_stored_async(stored))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await asyncio.gather(cleanup, return_exceptions=True)
        raise


async def _store_upload_metadata(
    *,
    session_factory: sessionmaker[Session],
    assets: AssetStore,
    owner_id: str,
    kind: UploadKind,
    original_name: str,
    stored: StoredImage,
) -> UploadResponse:
    insertion = asyncio.create_task(
        asyncio.to_thread(
            _insert_upload_metadata,
            session_factory,
            owner_id=owner_id,
            kind=kind,
            original_name=original_name,
            stored=stored,
        )
    )
    try:
        return await asyncio.shield(insertion)
    except asyncio.CancelledError:
        try:
            await insertion
        except BaseException:
            await _cleanup_unowned_upload(assets, stored)
        raise
    except BaseException:
        await _cleanup_unowned_upload(assets, stored)
        raise


async def _store_upload(
    *,
    kind: Literal["image", "mask"],
    file: UploadFile,
    request: Request,
    session: Session,
    context: AuthContext,
) -> UploadResponse:
    if file.content_type and not file.content_type.startswith("image/"):
        raise AppError("upload_invalid", "Only image uploads are accepted.", status_code=415)
    container = get_container(request)
    owner_id = context.user.id
    original_name = PurePath(file.filename or "upload").name[:255]
    # Authentication has already completed. Release its read transaction/connection while the
    # worker threads perform image normalization, durable writes, and the short metadata insert.
    # Metadata uses a new Session that is created and closed in its worker thread.
    session.close()
    stored = await container.assets.store_upload_async(file.file, kind=kind)
    return await _store_upload_metadata(
        session_factory=container.db.session_factory,
        assets=container.assets,
        owner_id=owner_id,
        kind=UploadKind(kind),
        original_name=original_name,
        stored=stored,
    )


async def _store_reference_upload(
    *,
    file: UploadFile,
    request: Request,
    session: Session,
    context: AuthContext,
) -> UploadResponse:
    if file.content_type and not file.content_type.startswith("image/"):
        raise AppError("upload_invalid", "Only image uploads are accepted.", status_code=415)
    container = get_container(request)
    owner_id = context.user.id
    original_name = PurePath(file.filename or "reference-image").name[:255]
    session.close()
    stored = await container.assets.store_reference_upload_async(file.file)
    return await _store_upload_metadata(
        session_factory=container.db.session_factory,
        assets=container.assets,
        owner_id=owner_id,
        kind=UploadKind.IMAGE,
        original_name=original_name,
        stored=stored,
    )


@router.post("/images", response_model=UploadResponse)
async def upload_image(
    request: Request,
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> UploadResponse:
    return await _store_upload(
        kind="image", file=file, request=request, session=session, context=context
    )


@router.post("/masks", response_model=UploadResponse)
async def upload_mask(
    request: Request,
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> UploadResponse:
    return await _store_upload(
        kind="mask", file=file, request=request, session=session, context=context
    )


@router.post("/reference-images", response_model=UploadResponse)
async def upload_reference_image(
    request: Request,
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> UploadResponse:
    return await _store_reference_upload(
        file=file, request=request, session=session, context=context
    )


@router.post("/reference-images/from-artifact/{artifact_id}", response_model=UploadResponse)
async def reference_image_from_artifact(
    artifact_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> UploadResponse:
    artifact = session.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.owner_id == context.user.id,
            Artifact.kind == "image",
        )
    )
    if artifact is None:
        raise AppError("not_found", "Gallery image was not found.", status_code=404)
    container = get_container(request)
    if artifact.byte_size > container.settings.upload_max_bytes:
        raise AppError(
            "upload_too_large",
            f"Gallery image exceeds the {container.settings.upload_max_bytes:,}-byte upload limit.",
        )
    owner_id = context.user.id
    storage_path = artifact.storage_path
    original_name = PurePath(artifact.source_filename or f"gallery-{artifact.id}").name[:255]
    session.close()
    content = await asyncio.to_thread(container.assets.read, storage_path)
    stored = await container.assets.store_reference_content_async(content)
    return await _store_upload_metadata(
        session_factory=container.db.session_factory,
        assets=container.assets,
        owner_id=owner_id,
        kind=UploadKind.IMAGE,
        original_name=original_name,
        stored=stored,
    )


@router.get("/{upload_id}/content")
def upload_content(
    upload_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_ready_user)],
) -> FileResponse:
    upload = session.scalar(
        select(Upload).where(Upload.id == upload_id, Upload.owner_id == context.user.id)
    )
    if upload is None:
        raise AppError("not_found", "Upload was not found.", status_code=404)
    path = get_container(request).assets.open(upload.storage_path)
    media_type = upload.mime_type
    session.close()
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )
