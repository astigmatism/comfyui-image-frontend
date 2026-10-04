from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from ..blocking import run_blocking
from ..dependencies import (
    AuthContext,
    database_handler,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..errors import AppError
from ..file_response import StoredFileResponse as FileResponse
from ..models import LoraLibraryImage
from ..services.assets import StoredImage
from ..services.lora_images import (
    control_identities,
    image_items,
    image_version,
    library_rows,
)

router = APIRouter(prefix="/api/workflows", tags=["generation-sources"])
library_router = APIRouter(prefix="/api/lora-library", tags=["generation-sources"])


@dataclass(frozen=True)
class ImageChange:
    item_id: str
    version: str
    action: Literal["set", "remove"]
    file_key: str | None = None


def _parse_changes(form: FormData) -> tuple[list[ImageChange], dict[str, UploadFile]]:
    values = form.getlist("changes")
    if len(values) != 1 or not isinstance(values[0], str):
        raise AppError("lora_image_invalid", "Supply one changes JSON field.", status_code=422)
    try:
        raw = json.loads(values[0])
    except ValueError as exc:
        raise AppError(
            "lora_image_invalid", "Changes must be valid JSON.", status_code=422
        ) from exc
    if not isinstance(raw, list) or len(raw) > 100:
        raise AppError("lora_image_invalid", "Supply at most 100 image changes.", status_code=422)
    changes: list[ImageChange] = []
    seen_items: set[str] = set()
    file_keys: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise AppError("lora_image_invalid", "Each change must be an object.", status_code=422)
        action = item.get("action")
        expected = (
            {"id", "version", "action", "file_key"}
            if action == "set"
            else {"id", "version", "action"}
        )
        if set(item) != expected or action not in ("set", "remove"):
            raise AppError(
                "lora_image_invalid", "Image change has invalid fields.", status_code=422
            )
        item_id, version = item["id"], item["version"]
        if (
            not isinstance(item_id, str)
            or not item_id
            or item_id in seen_items
            or not isinstance(version, str)
            or len(version) != 64
            or any(character not in "0123456789abcdef" for character in version)
        ):
            raise AppError(
                "lora_image_invalid", "Image change ID or version is invalid.", status_code=422
            )
        seen_items.add(item_id)
        file_key = item.get("file_key") if action == "set" else None
        if action == "set":
            if (
                not isinstance(file_key, str)
                or not file_key.startswith("image_")
                or len(file_key) > 32
                or file_key in file_keys
            ):
                raise AppError("lora_image_invalid", "Image file key is invalid.", status_code=422)
            file_keys.add(file_key)
        changes.append(ImageChange(item_id, version, action, file_key))
    files: dict[str, UploadFile] = {}
    for key, value in form.multi_items():
        if key == "changes":
            continue
        if key not in file_keys or key in files or not isinstance(value, UploadFile):
            raise AppError("lora_image_invalid", "Unexpected image upload field.", status_code=422)
        if value.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise AppError(
                "upload_invalid",
                "LoRA images must be static PNG, JPEG, or WebP files.",
                status_code=415,
            )
        files[key] = value
    if set(files) != file_keys:
        raise AppError("lora_image_invalid", "An image file is missing.", status_code=422)
    return changes, files


def _check_versions(
    session: Session,
    *,
    source_key: str,
    control_id: str,
    changes: list[ImageChange],
    request: Request,
) -> tuple[Any, dict[str, str], dict[str, LoraLibraryImage]]:
    container = get_container(request)
    profile = container.registry.resolve_source(
        session, source_key=source_key, require_dependencies=False
    )
    identities = control_identities(profile, control_id)
    if len({identities.get(change.item_id) for change in changes}) != len(changes):
        raise AppError("lora_image_invalid", "Each LoRA image is changed once.", status_code=422)
    rows = library_rows(session, identities.values())
    for change in changes:
        identity = identities.get(change.item_id)
        if identity is None:
            raise AppError("lora_image_invalid", "LoRA item is not published.", status_code=422)
        row = rows.get(identity)
        current = image_version(
            container.settings.session_secret.get_secret_value(),
            identity,
            row.revision if row else 0,
        )
        if change.version != current:
            raise AppError(
                "lora_image_conflict",
                "A LoRA image changed while this editor was open. "
                "Reload images and review your changes.",
                status_code=409,
                details={"item_id": change.item_id},
            )
    return profile, identities, rows


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


@router.post("/{source_key}/lora-images/{control_id}")
async def update_lora_images(
    source_key: str,
    control_id: str,
    request: Request,
    _: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> dict[str, list[dict[str, str | None]]]:
    container = get_container(request)
    async with request.form(max_files=100, max_fields=1, max_part_size=1_048_576) as form:
        changes, files = _parse_changes(form)

        def preflight() -> None:
            with container.db.session_factory() as session:
                _check_versions(
                    session,
                    source_key=source_key,
                    control_id=control_id,
                    changes=changes,
                    request=request,
                )

        await run_blocking(preflight)
        staged: dict[str, StoredImage] = {}
        superseded_paths: list[str] = []
        committed = False
        try:
            for change in changes:
                if change.action == "set" and change.file_key:
                    staged[change.item_id] = await container.assets.store_lora_thumbnail_async(
                        files[change.file_key].file
                    )

            def commit() -> dict[str, list[dict[str, str | None]]]:
                with container.db.session_factory() as session:
                    # SQLite's write lock covers every version check and the whole batch.
                    session.execute(text("BEGIN IMMEDIATE"))
                    profile, identities, rows = _check_versions(
                        session,
                        source_key=source_key,
                        control_id=control_id,
                        changes=changes,
                        request=request,
                    )
                    for change in changes:
                        identity = identities[change.item_id]
                        row = rows.get(identity)
                        if row is None:
                            row = LoraLibraryImage(lora_identity=identity, revision=1)
                            session.add(row)
                        else:
                            if row.storage_path:
                                superseded_paths.append(row.storage_path)
                            row.revision += 1
                        row.storage_path = (
                            staged[change.item_id].relative_path if change.action == "set" else None
                        )
                    session.flush()
                    result = image_items(
                        session,
                        profile=profile,
                        control_id=control_id,
                        secret=container.settings.session_secret.get_secret_value(),
                    )
                    session.commit()
                    return result

            operation = asyncio.create_task(run_blocking(commit))
            try:
                result = await asyncio.shield(operation)
            except asyncio.CancelledError:
                # A cancelled HTTP request cannot roll back a thread that already committed.
                try:
                    await operation
                    committed = True
                    await asyncio.shield(
                        asyncio.to_thread(container.assets.delete_paths, superseded_paths)
                    )
                except BaseException:
                    if not committed:
                        await asyncio.shield(_delete_staged(container.assets, staged))
                raise
            committed = True
            await asyncio.to_thread(container.assets.delete_paths, superseded_paths)
            return result
        except BaseException:
            if not committed:
                await asyncio.shield(_delete_staged(container.assets, staged))
            raise


async def _delete_staged(assets: Any, staged: dict[str, StoredImage]) -> None:
    await asyncio.to_thread(
        assets.delete_paths,
        [stored.relative_path for stored in staged.values()],
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
