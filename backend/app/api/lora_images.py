from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, text
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
from ..models import LoraImage
from ..services.assets import StoredImage
from ..services.lora_images import (
    control_bindings,
    image_items,
    image_version,
    logical_workflow_key,
)

router = APIRouter(prefix="/api/workflows", tags=["generation-sources"])


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
) -> tuple[Any, dict[str, str], dict[str, LoraImage]]:
    container = get_container(request)
    profile = container.registry.resolve_source(
        session, source_key=source_key, require_dependencies=False
    )
    bindings = control_bindings(profile, control_id)
    workflow_key = logical_workflow_key(profile)
    rows = {
        row.item_id: row
        for row in session.scalars(
            select(LoraImage).where(
                LoraImage.workflow_key == workflow_key,
                LoraImage.control_id == control_id,
            )
        )
    }
    for change in changes:
        binding = bindings.get(change.item_id)
        if binding is None:
            raise AppError("lora_image_invalid", "LoRA item is not published.", status_code=422)
        row = rows.get(change.item_id)
        current = image_version(
            container.settings.session_secret.get_secret_value(),
            workflow_key,
            control_id,
            change.item_id,
            row.revision if row else 0,
            binding,
        )
        if change.version != current:
            raise AppError(
                "lora_image_conflict",
                "A LoRA image changed while this editor was open. "
                "Reload images and review your changes.",
                status_code=409,
                details={"item_id": change.item_id},
            )
    return profile, bindings, rows


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
                    profile, bindings, rows = _check_versions(
                        session,
                        source_key=source_key,
                        control_id=control_id,
                        changes=changes,
                        request=request,
                    )
                    workflow_key = logical_workflow_key(profile)
                    for change in changes:
                        row = rows.get(change.item_id)
                        if row is None:
                            row = LoraImage(
                                workflow_key=workflow_key,
                                control_id=control_id,
                                item_id=change.item_id,
                                binding_hash=bindings[change.item_id],
                                revision=1,
                            )
                            session.add(row)
                        else:
                            if row.storage_path:
                                superseded_paths.append(row.storage_path)
                            row.revision += 1
                            row.binding_hash = bindings[change.item_id]
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
    container = get_container(request)
    profile = container.registry.resolve_source(
        session, source_key=source_key, require_dependencies=False
    )
    binding = control_bindings(profile, control_id).get(item_id)
    if binding is None:
        raise AppError("not_found", "LoRA image was not found.", status_code=404)
    row = session.get(LoraImage, (logical_workflow_key(profile), control_id, item_id))
    if (
        row is None
        or row.binding_hash != binding
        or not row.storage_path
        or v
        != image_version(
            container.settings.session_secret.get_secret_value(),
            row.workflow_key,
            control_id,
            item_id,
            row.revision,
            binding,
        )
    ):
        raise AppError("not_found", "LoRA image was not found.", status_code=404)
    path = container.assets.open(row.storage_path)
    session.close()
    return FileResponse(
        path,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
