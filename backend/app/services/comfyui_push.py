"""Copy one stored gallery image into a ComfyUI instance's input folder.

The browser never learns a ComfyUI URL: it asks the application to deliver an
image it already owns, and the application answers with the file reference the
receiving ComfyUI actually wrote. That reference is what an image-to-video
workflow's file-upload input lists, so no local file picker is involved.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..blocking import run_blocking
from ..config import ComfyUIInstanceConfig, Settings
from ..errors import AppError
from ..models import Artifact, AuditLog, Generation
from ..schemas import (
    ComfyUIPushBatchResult,
    ComfyUIPushFailure,
    ComfyUIPushResult,
    GallerySelection,
)
from .assets import AssetStore
from .comfyui_instances import ComfyUIInstances
from .gallery import GalleryService

logger = logging.getLogger(__name__)

# The input folder only holds files a ComfyUI image loader can read, so the
# outbound name always carries an extension matching the stored bytes.
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\<>:;\"'|?*=]")
_NOT_READABLE = (
    "ComfyUI took the file into its input folder but did not give it back when asked for it, "
    "so a workflow may not offer it yet."
)
# A toast can carry a few reasons; a rejected batch of a hundred images is not a
# hundred messages, so the rest is reported as a count.
_REPORTED_FAILURES = 3
# SQLite binds every ID of an `in_` clause, so selection lookups go out in chunks.
_QUERY_CHUNK = 200


@dataclass(frozen=True)
class _ArtifactRecord:
    artifact_id: str
    storage_path: str
    mime_type: str
    byte_size: int
    source_filename: str
    comfyui_instance_id: str | None


def _load_artifact(
    session_factory: sessionmaker[Session], *, owner_id: str, artifact_id: str
) -> _ArtifactRecord:
    """Read one owned image inside a session created and closed in this thread."""

    with session_factory() as session:
        row = session.execute(
            select(Artifact, Generation.comfyui_instance_id)
            .outerjoin(Generation, Generation.id == Artifact.generation_id)
            .where(Artifact.id == artifact_id, Artifact.owner_id == owner_id)
        ).first()
        if row is None:
            raise AppError("not_found", "Gallery image was not found.", status_code=404)
        artifact, instance_id = row
        if artifact.kind != "image":
            raise AppError("not_found", "Gallery image was not found.", status_code=404)
        return _ArtifactRecord(
            artifact_id=artifact.id,
            storage_path=artifact.storage_path,
            mime_type=artifact.mime_type,
            byte_size=artifact.byte_size,
            source_filename=artifact.source_filename or f"gallery-{artifact.id}",
            comfyui_instance_id=instance_id,
        )


def _record_push(
    session_factory: sessionmaker[Session],
    *,
    owner_id: str,
    artifact_id: str,
    instance_id: str,
    name: str,
    subfolder: str,
    byte_size: int,
) -> None:
    with session_factory() as session:
        session.add(
            AuditLog(
                actor_user_id=owner_id,
                target_type="artifact",
                target_id=artifact_id,
                action="comfyui_input_pushed",
                metadata_json={
                    "instance_id": instance_id,
                    "name": name,
                    "subfolder": subfolder,
                    "byte_size": byte_size,
                },
            )
        )
        session.commit()


def resolve_target_instance(
    instances: ComfyUIInstances,
    *,
    requested_id: str | None = None,
    generating_id: str | None = None,
) -> ComfyUIInstanceConfig:
    """Choose the container that receives the file.

    Only a container that executes images is eligible: the assigned prompt/dataset
    service answers uploads like any other, and a file parked there would be invisible
    to the workflow that needs it. Otherwise the image that is being sent decides, so a
    second GPU keeps the inputs of the work it will do next.
    """

    eligible = frozenset(instances.image_pool_ids)
    if requested_id is not None:
        config = instances.config(requested_id)
        if config is None:
            raise AppError("not_found", "That ComfyUI service is not configured.", status_code=404)
        if requested_id not in eligible:
            raise AppError(
                "comfyui_input_instance_required",
                "Image inputs can only be sent to a ComfyUI service that generates images.",
                status_code=409,
                details={"instance_id": requested_id},
            )
        return config
    if generating_id is not None and generating_id in eligible:
        config = instances.config(generating_id)
        if config is not None:
            return config
    return instances.for_stage("image")


def outbound_filename(record: _ArtifactRecord, *, override: str | None = None) -> str:
    """A single, loadable file name: no path, no separators, an honest extension."""

    candidate = override or record.source_filename
    name = _UNSAFE.sub("_", PurePosixPath(candidate).name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:120].rstrip(" .")
    if not name:
        name = f"gallery-{record.artifact_id}"
    allowed = _EXTENSIONS.get(record.mime_type)
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in _EXTENSIONS.values():
        name = f"{name}{allowed or _EXTENSIONS['image/png']}"
    return name


async def push_artifact_to_comfyui(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    assets: AssetStore,
    instances: ComfyUIInstances,
    owner_id: str,
    artifact_id: str,
    instance_id: str | None = None,
    filename: str | None = None,
) -> ComfyUIPushResult:
    record = await run_blocking(
        _load_artifact, session_factory, owner_id=owner_id, artifact_id=artifact_id
    )
    maximum = settings.comfyui_push_max_bytes
    if record.byte_size > maximum:
        raise AppError(
            "comfyui_push_too_large",
            f"This image is larger than the {maximum:,} byte limit for sending to ComfyUI.",
            status_code=413,
        )
    config = resolve_target_instance(
        instances, requested_id=instance_id, generating_id=record.comfyui_instance_id
    )
    adapter = instances.get(config.id)
    content = await asyncio.to_thread(assets.read, record.storage_path)
    if len(content) > maximum:
        raise AppError(
            "comfyui_push_too_large",
            f"This image is larger than the {maximum:,} byte limit for sending to ComfyUI.",
            status_code=413,
        )
    name = outbound_filename(record, override=filename)
    try:
        uploaded = await adapter.upload_input_image(content, name, mime_type=record.mime_type)
    except AppError as error:
        raise AppError(
            "comfyui_upload_failed",
            "ComfyUI did not accept the image for its input folder.",
            status_code=502,
            details={"instance_id": config.id, "reason": error.code},
        ) from error
    except httpx.HTTPStatusError as error:
        # A service that answers 4xx refused this particular file; one that answers 5xx,
        # or never answers, cannot take anything into its input folder right now.
        refused = error.response.status_code < 500
        raise AppError(
            "comfyui_upload_failed" if refused else "comfyui_unavailable",
            (
                "ComfyUI refused the image for its input folder."
                if refused
                else "The ComfyUI image service could not take the image."
            ),
            status_code=502 if refused else 503,
            details={"instance_id": config.id, "status": error.response.status_code},
        ) from error
    except httpx.HTTPError as error:
        raise AppError(
            "comfyui_unavailable",
            "The ComfyUI image service could not be reached.",
            status_code=503,
            details={"instance_id": config.id},
        ) from error
    readable = await adapter.input_file_readable(uploaded["name"], uploaded["subfolder"])
    await run_blocking(
        _record_push,
        session_factory,
        owner_id=owner_id,
        artifact_id=record.artifact_id,
        instance_id=config.id,
        name=uploaded["name"],
        subfolder=uploaded["subfolder"],
        byte_size=len(content),
    )
    logger.info(
        "comfyui_input_pushed",
        extra={"instance_id": config.id, "artifact_id": record.artifact_id},
    )
    return ComfyUIPushResult(
        artifact_id=record.artifact_id,
        instance_id=config.id,
        instance_label=config.label,
        name=uploaded["name"],
        subfolder=uploaded["subfolder"],
        mime_type=record.mime_type,
        byte_size=len(content),
        readable_as_input=readable,
        warning=_NOT_READABLE if readable is False else None,
    )


def _selection_image_ids(
    session_factory: sessionmaker[Session],
    gallery: GalleryService,
    *,
    owner_id: str,
    payload: GallerySelection,
    maximum: int,
) -> list[str]:
    """Every stored image the selection holds, folders included, as the ZIP download lists them."""

    with session_factory() as session:
        chosen = gallery.selection(session, owner_id, payload)
        generation_ids = {item.id for item in chosen.generations}
        subtree = sorted(chosen.subtree_ids)
        for start in range(0, len(subtree), _QUERY_CHUNK):
            generation_ids |= set(
                session.scalars(
                    select(Generation.id).where(
                        Generation.owner_id == owner_id,
                        Generation.collection_id.in_(subtree[start : start + _QUERY_CHUNK]),
                    )
                )
            )
        if not generation_ids:
            raise AppError(
                "comfyui_push_no_images", "The selection holds no images to send.", status_code=409
            )
        ordered = sorted(generation_ids)
        artifact_ids: list[str] = []
        for start in range(0, len(ordered), _QUERY_CHUNK):
            chunk = ordered[start : start + _QUERY_CHUNK]
            artifact_ids.extend(
                session.scalars(
                    select(Artifact.id)
                    .where(
                        Artifact.owner_id == owner_id,
                        Artifact.generation_id.in_(chunk),
                        Artifact.kind == "image",
                    )
                    .order_by(Artifact.generation_id, Artifact.sequence, Artifact.batch_index)
                )
            )
        if not artifact_ids:
            raise AppError(
                "comfyui_push_no_images", "The selection holds no images to send.", status_code=409
            )
        if len(artifact_ids) > maximum:
            raise AppError(
                "comfyui_push_too_many",
                f"{len(artifact_ids)} images are selected, and at most {maximum} can be sent "
                "to ComfyUI in one action.",
                status_code=422,
                details={"selected": len(artifact_ids), "limit": maximum},
            )
        return artifact_ids


async def push_selection_to_comfyui(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    assets: AssetStore,
    instances: ComfyUIInstances,
    gallery: GalleryService,
    owner_id: str,
    payload: GallerySelection,
    instance_id: str | None = None,
) -> ComfyUIPushBatchResult:
    """Send every image of a selection, one file at a time, and report what did not go.

    A batch that starts must finish: one offline container makes one image fail while the
    rest still land, which is what the gallery can show usefully.
    """

    artifact_ids = await run_blocking(
        _selection_image_ids,
        session_factory,
        gallery,
        owner_id=owner_id,
        payload=payload,
        maximum=settings.comfyui_push_max_files,
    )
    pushed: list[ComfyUIPushResult] = []
    failures: list[ComfyUIPushFailure] = []
    for artifact_id in artifact_ids:
        try:
            pushed.append(
                await push_artifact_to_comfyui(
                    session_factory,
                    settings=settings,
                    assets=assets,
                    instances=instances,
                    owner_id=owner_id,
                    artifact_id=artifact_id,
                    instance_id=instance_id,
                )
            )
        except AppError as error:
            failures.append(
                ComfyUIPushFailure(artifact_id=artifact_id, code=error.code, message=error.message)
            )
    labels = list(dict.fromkeys(item.instance_label for item in pushed))
    warnings = [item.warning for item in pushed if item.warning]
    notice = warnings[0] if warnings else None
    if failures:
        reasons = " ".join({item.message for item in failures[:_REPORTED_FAILURES]})
        rest = len(failures) - min(len(failures), _REPORTED_FAILURES)
        notice = f"{notice + ' ' if notice else ''}{reasons}" + (
            f" {rest} more image{'s' if rest != 1 else ''} failed for the same kind of reason."
            if rest > 0
            else ""
        )
    return ComfyUIPushBatchResult(
        requested=len(artifact_ids),
        sent=len(pushed),
        instance_labels=labels,
        warning=notice,
        failures=failures,
    )
