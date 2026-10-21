"""Explicit shared-reel capabilities; ordinary personal APIs remain owner-scoped."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..demo_reel_models import DemoReel, DemoReelEntry, DemoReelTransfer
from ..demo_reel_schemas import DemoReelAction, DemoReelUpdate
from ..dependencies import (
    AuthContext,
    database_handler,
    get_container,
    get_db,
    require_admin,
    require_ready_csrf,
    require_ready_user,
)
from ..errors import AppError
from ..file_response import StoredFileResponse
from ..models import Artifact, Favorite, UserRole
from ..schemas import GallerySelection
from ..services.demo_reel_activity import SYSTEM_OWNER_ID
from ..services.demo_reels import digest
from ..services.gallery import GalleryService
from ..services.prompt_rerun import preview
from ..services.user_state import lock_user_state

router = APIRouter(prefix="/api", tags=["demo-reels"])


def shared_urls(value: Any, base: str) -> Any:
    if isinstance(value, list):
        return [shared_urls(item, base) for item in value]
    if isinstance(value, dict):
        return {
            key: item.replace("/api/artifacts/", f"{base}/artifacts/")
            if key in {"content_url", "thumbnail_url"} and isinstance(item, str)
            else shared_urls(item, base)
            for key, item in value.items()
        }
    return value


def base_path(library_key: str, identity: str) -> str:
    return f"/api/demo-reels/{library_key}/{identity}"


@router.get("/admin/demo-reels")
@database_handler
def settings(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_admin)],
) -> dict[str, Any]:
    return get_container(request).demo_reels.settings(session)


@router.put("/admin/demo-reels")
@database_handler
def save_settings(
    payload: DemoReelUpdate,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> dict[str, Any]:
    if context.user.role != UserRole.ADMIN:
        raise AppError("forbidden", "Administrator access is required.", status_code=403)
    return get_container(request).demo_reels.save(session, context.user.id, payload)


@router.post("/activity", status_code=204)
async def activity(_: Annotated[AuthContext, Depends(require_ready_csrf)]) -> None:
    # The dependency records activity before yielding, including immediate preemption.
    return None


@router.get("/demo-reels/{library_key}/{identity}")
@database_handler
def gallery(
    library_key: str,
    identity: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=60)] = 24,
) -> dict[str, Any]:
    result = get_container(request).demo_reels.view(
        session, context.user.id, library_key, identity, offset=offset, limit=limit
    )
    count = len(result["entries"])
    result["entries"] = result["entries"][offset : offset + limit]
    result["next_offset"] = offset + limit if offset + limit < count else None
    return cast(dict[str, Any], shared_urls(result, base_path(library_key, identity)))


@router.get("/demo-reels/{library_key}/{identity}/generations/{generation_id}")
@router.get("/demo-reels/{library_key}/{identity}/generations/{generation_id}/{operation}")
@database_handler
def generation_detail(
    library_key: str,
    identity: str,
    generation_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_user)],
    operation: str = "details",
) -> dict[str, Any]:
    container = get_container(request)
    generation = container.demo_reels.readable(session, library_key, identity, generation_id)
    if operation == "recall":
        result = container.generations.recall(session, generation).model_dump(mode="json")
    elif operation == "details":
        result = container.generations.detail(session, generation).model_dump(mode="json")
    else:
        raise AppError("not_found", "Action was not found.", status_code=404)
    result["demo_reel"] = {"can_manage": context.user.role == UserRole.ADMIN}
    return cast(dict[str, Any], shared_urls(result, base_path(library_key, identity)))


@router.get("/demo-reels/{library_key}/{identity}/artifacts/{artifact_id}/{kind}")
@database_handler
def artifact_content(
    library_key: str,
    identity: str,
    artifact_id: str,
    kind: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> StoredFileResponse:
    lock_user_state(session)
    container = get_container(request)
    artifact = session.get(Artifact, artifact_id)
    if artifact is None or kind not in {"content", "thumbnail"}:
        raise AppError("not_found", "Image was not found.", status_code=404)
    container.demo_reels.readable(session, library_key, identity, artifact.generation_id)
    relative = artifact.thumbnail_path if kind == "thumbnail" else artifact.storage_path
    if not relative:
        raise AppError("not_found", "Image was not found.", status_code=404)
    path = container.assets.open(relative)
    media_type = "image/webp" if kind == "thumbnail" else artifact.mime_type
    session.close()
    return StoredFileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/demo-reels/{library_key}/{identity}/actions")
@database_handler
def action(
    library_key: str,
    identity: str,
    payload: DemoReelAction,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    context: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> dict[str, Any]:
    lock_user_state(session)
    container = get_container(request)
    service = container.demo_reels
    admin_actions = {"delete", "move", "regenerate", "retry", "pause", "resume"}
    if payload.action in admin_actions and context.user.role != UserRole.ADMIN:
        raise AppError("forbidden", "Administrator access is required.", status_code=403)
    if payload.action in {"pause", "resume"}:
        config = service.config(session)
        if payload.revision != config.revision:
            raise AppError(
                "demo_reel_conflict",
                "Demo Reel settings changed elsewhere. Refresh before trying again.",
                status_code=409,
            )
        config.paused = payload.action == "pause"
        config.revision += 1
        session.commit()
        return {"generation_ids": []}
    ids = list(dict.fromkeys(payload.generation_ids))
    receipt_digest = digest([library_key, identity, payload.model_dump(mode="json")])
    if payload.action in {"copy", "move"}:
        if payload.key is None:
            raise AppError(
                "idempotency_key_required", "A transfer ID is required.", status_code=422
            )
        receipt = session.get(DemoReelTransfer, (context.user.id, str(payload.key)))
        if receipt:
            if receipt.digest != receipt_digest:
                raise AppError(
                    "idempotency_conflict", "Transfer ID was already used.", status_code=409
                )
            return {"generation_ids": receipt.generation_ids}
        container.generations._collection_for_owner(session, context.user.id, payload.collection_id)
    if payload.action == "retry":
        reel = session.scalar(
            select(DemoReel).where(
                DemoReel.library_key == library_key, DemoReel.lora_identity == identity
            )
        )
        if reel is None:
            raise AppError("not_found", "Demo Reel was not found.", status_code=404)
        for entry in session.scalars(
            select(DemoReelEntry).where(
                DemoReelEntry.reel_id == reel.id, DemoReelEntry.state.in_(["failed", "blocked"])
            )
        ):
            entry.failures, entry.next_retry_at, entry.error, entry.state = 0, None, None, "missing"
        session.commit()
        return {"generation_ids": []}
    generations = [
        service.readable(session, library_key, identity, generation_id) for generation_id in ids
    ]
    created_paths: list[str] = []
    copied = []
    try:
        for generation in generations:
            if payload.action in {"favorite", "unfavorite"}:
                favorite = session.scalar(
                    select(Favorite).where(
                        Favorite.owner_id == context.user.id,
                        Favorite.generation_id == generation.id,
                    )
                )
                if payload.action == "favorite" and favorite is None:
                    session.add(Favorite(owner_id=context.user.id, generation_id=generation.id))
                elif payload.action == "unfavorite" and favorite:
                    session.delete(favorite)
            if payload.action in {"copy", "move"}:
                copied.append(
                    GalleryService(container.generations, container.collections)._copy_generation(
                        session,
                        generation,
                        payload.collection_id,
                        created_paths,
                        owner_id=context.user.id,
                    )
                )
            if payload.action in {"delete", "move", "regenerate"}:
                selected_entry = session.scalar(
                    select(DemoReelEntry).where(DemoReelEntry.generation_id == generation.id)
                )
                assert selected_entry is not None
                service._discard(session, selected_entry)
                (
                    selected_entry.state,
                    selected_entry.failures,
                    selected_entry.next_retry_at,
                    selected_entry.error,
                ) = (
                    "missing",
                    0,
                    None,
                    None,
                )
        if payload.action in {"copy", "move"}:
            session.add(
                DemoReelTransfer(
                    actor_id=context.user.id,
                    key=str(payload.key),
                    digest=receipt_digest,
                    generation_ids=copied,
                )
            )
        session.commit()
    except Exception:
        session.rollback()
        container.assets.delete_paths(created_paths)
        raise
    return {"generation_ids": copied}


@router.post("/demo-reels/{library_key}/{identity}/download")
@database_handler
def download(
    library_key: str,
    identity: str,
    payload: GallerySelection,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> Response:
    if payload.collection_ids or payload.scope is not None:
        raise AppError("selection_invalid", "Select Demo Reel images explicitly.", status_code=422)
    container = get_container(request)
    for generation_id in payload.generation_ids:
        container.demo_reels.readable(session, library_key, identity, generation_id)
    path = GalleryService(container.generations, container.collections).download(
        session, owner_id=SYSTEM_OWNER_ID, payload=payload
    )
    session.close()
    return StoredFileResponse(
        path,
        media_type="application/zip",
        filename="demo-reel.zip",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.post("/demo-reels/{library_key}/{identity}/prompt-rerun/preview")
@database_handler
def rerun_preview(
    library_key: str,
    identity: str,
    payload: GallerySelection,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> dict[str, Any]:
    if payload.collection_ids or payload.scope is not None:
        raise AppError("selection_invalid", "Select Demo Reel images explicitly.", status_code=422)
    container = get_container(request)
    for generation_id in payload.generation_ids:
        container.demo_reels.readable(session, library_key, identity, generation_id)
    return preview(
        session, container.generations, container.collections, SYSTEM_OWNER_ID, payload
    ).model_dump(mode="json")
