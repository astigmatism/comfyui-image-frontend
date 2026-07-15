from __future__ import annotations

import asyncio
import hashlib
import io
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import Settings
from ..errors import AppError


@dataclass(frozen=True)
class StoredImage:
    relative_path: str
    thumbnail_path: str | None
    mime_type: str
    byte_size: int
    width: int
    height: int
    sha256: str


class AssetStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.data_dir = settings.data_dir
        self.assets_dir = settings.assets_dir
        self.uploads_dir = settings.uploads_dir
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def store_upload(self, source: BinaryIO, *, kind: str) -> StoredImage:
        raw = _read_limited(source, self.settings.upload_max_bytes)
        image = self._decode_image(raw)
        if kind == "mask":
            image = _normalize_mask(image)
        else:
            image = ImageOps.exif_transpose(image).convert(
                "RGBA" if "A" in image.getbands() else "RGB"
            )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        normalized = buffer.getvalue()
        relative = f"uploads/{uuid.uuid4()}.png"
        sha256 = hashlib.sha256(normalized).hexdigest()
        self._atomic_write(relative, normalized)
        return StoredImage(
            relative_path=relative,
            thumbnail_path=None,
            mime_type="image/png",
            byte_size=len(normalized),
            width=image.width,
            height=image.height,
            sha256=sha256,
        )

    async def store_upload_async(self, source: BinaryIO, *, kind: str) -> StoredImage:
        """Normalize and durably write an upload without occupying the event loop."""

        return await self._store_async(lambda: self.store_upload(source, kind=kind))

    def store_reference_upload(self, source: BinaryIO) -> StoredImage:
        return self.store_reference_content(_read_limited(source, self.settings.upload_max_bytes))

    def store_reference_content(self, content: bytes) -> StoredImage:
        if not content:
            raise AppError("upload_invalid", "Upload is empty.")
        if len(content) > self.settings.upload_max_bytes:
            raise AppError(
                "upload_too_large",
                f"Upload exceeds the {self.settings.upload_max_bytes:,}-byte limit.",
            )
        image = self._decode_image(content, require_static=True)
        detected_format = str(image.format or "").upper()
        mime_and_extension = {
            "PNG": ("image/png", ".png"),
            "JPEG": ("image/jpeg", ".jpg"),
            "WEBP": ("image/webp", ".webp"),
        }.get(detected_format)
        if mime_and_extension is None:
            raise AppError(
                "upload_invalid", "Reference images must be static PNG, JPEG, or WebP files."
            )
        mime_type, extension = mime_and_extension
        relative = f"uploads/{uuid.uuid4()}{extension}"
        self._atomic_write(relative, content)
        return StoredImage(
            relative_path=relative,
            thumbnail_path=None,
            mime_type=mime_type,
            byte_size=len(content),
            width=image.width,
            height=image.height,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    async def store_reference_upload_async(self, source: BinaryIO) -> StoredImage:
        return await self._store_async(lambda: self.store_reference_upload(source))

    async def store_reference_content_async(self, content: bytes) -> StoredImage:
        return await self._store_async(lambda: self.store_reference_content(content))

    def store_artifact(
        self, content: bytes, *, generation_id: str, kind: str = "image"
    ) -> StoredImage:
        if len(content) > self.settings.upload_max_bytes * 20:
            raise AppError(
                "artifact_persistence_failed", "Generated artifact exceeds storage limits."
            )
        if kind != "image":
            relative = f"assets/{generation_id}/{uuid.uuid4()}.bin"
            sha256 = hashlib.sha256(content).hexdigest()
            self._atomic_write(relative, content)
            return StoredImage(
                relative_path=relative,
                thumbnail_path=None,
                mime_type="application/octet-stream",
                byte_size=len(content),
                width=0,
                height=0,
                sha256=sha256,
            )
        image = self._decode_image(content, max_pixels=max(self.settings.upload_max_pixels * 4, 1))
        detected_format = image.format or "PNG"
        mime = Image.MIME.get(detected_format, "image/png")
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
            mime, ".img"
        )
        relative = f"assets/{generation_id}/{uuid.uuid4()}{extension}"
        sha256 = hashlib.sha256(content).hexdigest()
        created_paths: list[str] = []
        try:
            self._atomic_write(relative, content)
            created_paths.append(relative)
            thumbnail = self._thumbnail(image, generation_id)
        except BaseException:
            # A thumbnail encoding/write failure must not strand an unowned original.
            self.delete_paths(created_paths)
            raise
        return StoredImage(
            relative_path=relative,
            thumbnail_path=thumbnail,
            mime_type=mime,
            byte_size=len(content),
            width=image.width,
            height=image.height,
            sha256=sha256,
        )

    async def store_artifact_async(
        self, content: bytes, *, generation_id: str, kind: str = "image"
    ) -> StoredImage:
        """Decode, hash, thumbnail, and durably write an artifact off the event loop."""

        return await self._store_async(
            lambda: self.store_artifact(content, generation_id=generation_id, kind=kind)
        )

    async def delete_stored_async(self, stored: StoredImage) -> None:
        await asyncio.to_thread(
            self.delete_paths,
            [path for path in (stored.relative_path, stored.thumbnail_path) if path],
        )

    async def _store_async(self, operation: Callable[[], StoredImage]) -> StoredImage:
        # Shield the thread future so request/worker cancellation cannot close an UploadFile or
        # abandon a durable write midway through the operation. If cancellation wins, wait for
        # the thread and remove any completed, as-yet-unowned files before propagating it.
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            stored: StoredImage | None = None
            try:
                stored = await task
            except Exception:
                stored = None
            if stored is not None:
                await asyncio.shield(self.delete_stored_async(stored))
            raise

    def _decode_image(
        self,
        content: bytes,
        *,
        max_pixels: int | None = None,
        require_static: bool = False,
    ) -> Image.Image:
        limit = max_pixels or self.settings.upload_max_pixels
        try:
            with Image.open(io.BytesIO(content)) as probe:
                width, height = probe.size
                if width <= 0 or height <= 0 or width * height > limit:
                    raise AppError(
                        "upload_invalid",
                        f"Image exceeds the decompressed pixel limit of {limit:,} pixels.",
                    )
                if require_static and getattr(probe, "n_frames", 1) != 1:
                    raise AppError(
                        "upload_invalid",
                        "Animated or multi-frame reference images are not supported.",
                    )
                probe.verify()
            image = Image.open(io.BytesIO(content))
            if require_static and getattr(image, "n_frames", 1) != 1:
                raise AppError(
                    "upload_invalid", "Animated or multi-frame reference images are not supported."
                )
            image.load()
            return image
        except AppError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise AppError("upload_invalid", "File is not a safely decodable image.") from exc

    def _thumbnail(self, image: Image.Image, generation_id: str) -> str:
        thumb = ImageOps.exif_transpose(image.copy())
        if thumb.mode not in {"RGB", "RGBA"}:
            thumb = thumb.convert("RGBA" if "A" in thumb.getbands() else "RGB")
        thumb.thumbnail((self.settings.thumbnail_max_edge, self.settings.thumbnail_max_edge))
        buffer = io.BytesIO()
        thumb.save(buffer, format="WEBP", quality=82, method=4)
        relative = f"assets/{generation_id}/thumb-{uuid.uuid4()}.webp"
        self._atomic_write(relative, buffer.getvalue())
        return relative

    def open(self, relative_path: str) -> Path:
        candidate = (self.data_dir / relative_path).resolve()
        root = self.data_dir.resolve()
        if candidate == root or root not in candidate.parents:
            raise AppError("unsafe_path", "Stored asset path is unsafe.", status_code=404)
        if not candidate.is_file():
            raise AppError("not_found", "Stored asset is unavailable.", status_code=404)
        return candidate

    def read(self, relative_path: str) -> bytes:
        return self.open(relative_path).read_bytes()

    def delete_paths(self, relative_paths: list[str]) -> None:
        for relative in relative_paths:
            try:
                path = self.open(relative)
            except AppError:
                continue
            try:
                path.unlink(missing_ok=True)
                _remove_empty_parents(path.parent, self.data_dir)
            except OSError:
                continue

    def _atomic_write(self, relative_path: str, content: bytes) -> None:
        target = (self.data_dir / relative_path).resolve()
        root = self.data_dir.resolve()
        if root not in target.parents:
            raise AppError("unsafe_path", "Storage path escaped the application data directory.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def _read_limited(source: BinaryIO, maximum: int) -> bytes:
    output = bytearray()
    while True:
        chunk = source.read(min(1024 * 1024, maximum + 1 - len(output)))
        if not chunk:
            break
        output.extend(chunk)
        if len(output) > maximum:
            raise AppError("upload_too_large", f"Upload exceeds the {maximum:,}-byte limit.")
    if not output:
        raise AppError("upload_invalid", "Upload is empty.")
    return bytes(output)


def _normalize_mask(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if "A" in image.getbands():
        return image.getchannel("A")
    return image.convert("L")


def _remove_empty_parents(path: Path, root: Path) -> None:
    root = root.resolve()
    current = path.resolve()
    while root in current.parents and current != root:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
