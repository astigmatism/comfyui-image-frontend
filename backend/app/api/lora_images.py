"""Read side of the shared LoRA display images.

Nothing is uploaded here any more: each image is derived from the LoRA's Demo Reel by
``services/lora_thumbnails.py``, which owns ``lora_library_images``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import (
    AuthContext,
    database_handler,
    get_container,
    get_db,
    require_ready_user,
)
from ..errors import AppError
from ..file_response import StoredFileResponse as FileResponse
from ..models import LoraLibraryImage
from ..services.lora_images import (
    control_identities,
    image_items,
    image_version,
)

router = APIRouter(prefix="/api/workflows", tags=["generation-sources"])
library_router = APIRouter(prefix="/api/lora-library", tags=["generation-sources"])


@router.get("/{source_key}/lora-images/{control_id}")
@database_handler
def get_lora_images(
    source_key: str,
    control_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> dict[str, list[dict[str, str | None]]]:
    container = get_container(request)
    profile = container.registry.resolve_source(
        session, source_key=source_key, require_dependencies=False
    )
    return image_items(
        session,
        profile=profile,
        control_id=control_id,
        secret=container.settings.session_secret.get_secret_value(),
    )


def _image_response(row: LoraLibraryImage | None, v: str, request: Request) -> FileResponse:
    container = get_container(request)
    if (
        row is None
        or not row.storage_path
        or v
        != image_version(
            container.settings.session_secret.get_secret_value(), row.lora_identity, row.revision
        )
    ):
        raise AppError("not_found", "LoRA image was not found.", status_code=404)
    return FileResponse(
        container.assets.open(row.storage_path),
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@library_router.get("/images/{lora_identity}/content")
@database_handler
def lora_library_image_content(
    lora_identity: str,
    v: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> FileResponse:
    row = session.get(LoraLibraryImage, lora_identity)
    session.close()
    return _image_response(row, v, request)


@router.get("/{source_key}/lora-images/{control_id}/{item_id}/content")
@database_handler
def lora_image_content(
    source_key: str,
    control_id: str,
    item_id: str,
    v: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> FileResponse:
    """Workflow-scoped URL kept for already open editors; it serves the library image."""

    profile = get_container(request).registry.resolve_source(
        session, source_key=source_key, require_dependencies=False
    )
    identity = control_identities(profile, control_id).get(item_id)
    row = session.get(LoraLibraryImage, identity) if identity else None
    session.close()
    return _image_response(row, v, request)
