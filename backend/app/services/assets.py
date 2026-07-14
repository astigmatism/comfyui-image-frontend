from __future__ import annotations

import hashlib
import io
import os
import uuid
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
        self._atomic_write(relative, normalized)
        return StoredImage(
            relative_path=relative,
            thumbnail_path=None,
            mime_type="image/png",
            byte_size=len(normalized),
            width=image.width,
            height=image.height,
            sha256=hashlib.sha256(normalized).hexdigest(),
        )

    def store_artifact(
        self, content: bytes, *, generation_id: str, kind: str = "image"
    ) -> StoredImage:
        if len(content) > self.settings.upload_max_bytes * 20:
            raise AppError(
                "artifact_persistence_failed", "Generated artifact exceeds storage limits."
            )
        if kind != "image":
            relative = f"assets/{generation_id}/{uuid.uuid4()}.bin"
            self._atomic_write(relative, content)
            return StoredImage(
                relative_path=relative,
                thumbnail_path=None,
                mime_type="application/octet-stream",
                byte_size=len(content),
                width=0,
                height=0,
                sha256=hashlib.sha256(content).hexdigest(),
            )
        image = self._decode_image(content, max_pixels=max(self.settings.upload_max_pixels * 4, 1))
        detected_format = image.format or "PNG"
        mime = Image.MIME.get(detected_format, "image/png")
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
            mime, ".img"
        )
        relative = f"assets/{generation_id}/{uuid.uuid4()}{extension}"
        self._atomic_write(relative, content)
        thumbnail = self._thumbnail(image, generation_id)
        return StoredImage(
            relative_path=relative,
            thumbnail_path=thumbnail,
            mime_type=mime,
            byte_size=len(content),
            width=image.width,
            height=image.height,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def _decode_image(self, content: bytes, *, max_pixels: int | None = None) -> Image.Image:
        limit = max_pixels or self.settings.upload_max_pixels
        try:
            with Image.open(io.BytesIO(content)) as probe:
                width, height = probe.size
                if width <= 0 or height <= 0 or width * height > limit:
                    raise AppError(
                        "upload_invalid",
                        f"Image exceeds the decompressed pixel limit of {limit:,} pixels.",
                    )
                probe.verify()
            image = Image.open(io.BytesIO(content))
            image.load()
            return image
        except AppError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
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
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)


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
