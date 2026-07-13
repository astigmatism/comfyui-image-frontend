from __future__ import annotations

from pathlib import PurePath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..dependencies import AuthContext, get_container, get_db, require_ready_csrf, require_ready_user
from ..errors import AppError
from ..models import Upload, UploadKind
from ..schemas import UploadResponse

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


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
    stored = container.assets.store_upload(file.file, kind=kind)
    upload = Upload(
        owner_id=context.user.id,
        kind=UploadKind(kind),
        storage_path=stored.relative_path,
        original_name=PurePath(file.filename or "upload").name[:255],
        mime_type=stored.mime_type,
        byte_size=stored.byte_size,
        width=stored.width,
        height=stored.height,
        sha256=stored.sha256,
    )
    session.add(upload)
    session.commit()
    return UploadResponse(
        id=upload.id,
        kind=upload.kind.value,
        mime_type=upload.mime_type,
        width=upload.width,
        height=upload.height,
        sha256=upload.sha256,
        preview_url=f"/api/uploads/{upload.id}/content",
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
    return FileResponse(
        path,
        media_type=upload.mime_type,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )
