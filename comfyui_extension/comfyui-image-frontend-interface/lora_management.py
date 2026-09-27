"""Narrow, opt-in ComfyUI authority for a published CIFLoraStack catalog.

The application orchestrates replicas. This module never accepts a replacement graph:
it derives each candidate from a byte-verified existing publication and changes only
the LoRA declaration. Operation files live in ComfyUI userdata, not /tmp.
"""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import struct
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

PUBLIC_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
SAFE_USER = re.compile(r"[a-zA-Z0-9_-]{1,100}\Z")
MAX_JSON = 64 * 1024
MAX_BUNDLE = 32 * 1024 * 1024
MAX_HEADER = 16 * 1024 * 1024
DEFAULT_MAX_UPLOAD = 8 * 1024 * 1024 * 1024
WIDGETS = (
    "catalog_json",
    "value",
    "minimum",
    "maximum",
    "step",
    "parameter_id",
    "instance_uuid",
    "label",
    "description",
    "semantic_role",
    "required",
    "advanced",
    "group",
    "order",
)


class ManagementError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status

    @property
    def code(self) -> str:
        return {
            "LoRA is used by another published workflow": "shared_publication",
            "LoRA is used by another authoring workflow": "authoring_reference",
            "Native ComfyUI queue must be empty before LoRA removal": "native_queue_active",
            "LoRA is used by another active loader in this workflow": "same_workflow_loader",
            "LoRA is used by another loader in this authoring workflow": "same_workflow_loader",
            "LoRA file is shared by another catalog entry": "shared_catalog_file",
            "Publication revision changed": "publication_changed",
        }.get(str(self), "lora_management_error")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def _parse(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_BUNDLE:
        raise ManagementError("Publication artifact is too large")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ManagementError("Publication artifact is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ManagementError("Publication artifact must be an object")
    return value


def _safe_source(value: Any) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 2048:
        raise ManagementError("Invalid workflow path", 400)
    path = PurePosixPath(value)
    if (
        not value.startswith("workflows/")
        or "\\" in value
        or "//" in value
        or any(part in ("", ".", "..") for part in path.parts)
        or str(path) != value
        or not value.endswith(".json")
        or value.endswith((".api.json", ".interface.json"))
    ):
        raise ManagementError("Invalid workflow path", 400)
    return value


def _paths(source: str) -> tuple[str, str, str]:
    source = _safe_source(source)
    stem = source[: -len(".workflow.json")] if source.endswith(".workflow.json") else source[:-5]
    return source, stem + ".api.json", stem + ".interface.json"


def _safe_model_name(value: Any) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1000:
        raise ManagementError("Invalid LoRA filename", 400)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or "//" in value
        or any(part in ("", ".", "..") for part in path.parts)
        or str(path) != value
        or not value.lower().endswith(".safetensors")
    ):
        raise ManagementError("Invalid LoRA filename", 400)
    return value


def _file_under(root: Path, filename: str, *, must_exist: bool = True) -> Path:
    target = root.joinpath(*PurePosixPath(_safe_model_name(filename)).parts)
    if not target.is_relative_to(root):
        raise ManagementError("LoRA path escapes the model library", 400)
    current = root
    for component in target.relative_to(root).parts:
        current = current / component
        if current.is_symlink():
            raise ManagementError("A LoRA path component is a symlink")
    if must_exist:
        try:
            info = target.stat(follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ManagementError("LoRA file is missing") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ManagementError("LoRA must be a regular, unlinked file")
    return target


def _confined(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ManagementError("Unsafe management path")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ManagementError("Management path contains a symlink")
    if not current.is_relative_to(root):
        raise ManagementError("Management path escapes its root")
    return current


def _safe_id(value: Any) -> str:
    try:
        canonical = str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ManagementError("Invalid operation ID", 400) from exc
    if canonical != value:
        raise ManagementError("Invalid operation ID", 400)
    return canonical


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".new")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _regular_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ManagementError("Publication file is missing or unsafe")
    if path.stat().st_size > MAX_BUNDLE:
        raise ManagementError("Publication file is too large")
    return path.read_bytes()


def _safetensors(path: Path) -> None:
    size = path.stat().st_size
    if size < 10:
        raise ManagementError("Invalid safetensors file", 400)
    with path.open("rb") as stream:
        header_size = struct.unpack("<Q", stream.read(8))[0]
        if not 2 <= header_size <= MAX_HEADER or 8 + header_size > size:
            raise ManagementError("Invalid safetensors header size", 400)
        try:
            header = json.loads(stream.read(header_size))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ManagementError("Invalid safetensors header", 400) from exc
    if not isinstance(header, dict) or not header:
        raise ManagementError("Invalid safetensors tensors", 400)
    payload_size = size - 8 - header_size
    count = 0
    for key, tensor in header.items():
        if key == "__metadata__":
            if not isinstance(tensor, dict):
                raise ManagementError("Invalid safetensors metadata", 400)
            continue
        if not isinstance(tensor, dict) or not isinstance(tensor.get("dtype"), str):
            raise ManagementError("Invalid safetensors tensor", 400)
        shape, offsets = tensor.get("shape"), tensor.get("data_offsets")
        if not isinstance(shape, list) or any(type(n) is not int or n < 0 for n in shape):
            raise ManagementError("Invalid safetensors tensor shape", 400)
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or any(type(n) is not int for n in offsets)
            or not 0 <= offsets[0] <= offsets[1] <= payload_size
        ):
            raise ManagementError("Invalid safetensors tensor offsets", 400)
        count += 1
    if not count:
        raise ManagementError("Safetensors file contains no tensors", 400)


def _stack(workflow: dict, api: dict, manifest: dict) -> tuple[dict, dict, dict, dict, list]:
    nodes = [node for node in workflow.get("nodes", []) if node.get("type") == "CIFLoraStack"]
    frozen = [(key, node) for key, node in api.items() if node.get("class_type") == "CIFLoraStack"]
    inputs = [
        item
        for item in manifest.get("interface", {}).get("inputs", [])
        if item.get("type") == "lora_stack"
    ]
    if len(nodes) != 1 or len(frozen) != 1 or len(inputs) != 1:
        raise ManagementError("Publication must contain one root CIFLoraStack")
    editable, (node_id, frozen_node), declaration = nodes[0], frozen[0], inputs[0]
    if str(editable.get("id")) != node_id or not isinstance(frozen_node.get("inputs"), dict):
        raise ManagementError("Editable and frozen LoRA nodes differ")
    widget_names = [
        entry.get("widget", {}).get("name")
        for entry in editable.get("inputs", [])
        if entry.get("widget")
    ]
    values = editable.get("widgets_values")
    if widget_names != list(WIDGETS) or not isinstance(values, list) or len(values) != len(WIDGETS):
        raise ManagementError("Unknown editable CIFLoraStack widget layout")
    bindings = declaration.get("bindings")
    if (
        not isinstance(bindings, list)
        or len(bindings) != 1
        or bindings[0].get("node_id") != node_id
        or bindings[0].get("input") != "value"
    ):
        raise ManagementError("Unknown CIFLoraStack publication binding")
    if declaration.get("semantic_role") != "lora" or declaration.get("required") is not False:
        raise ManagementError("Invalid CIFLoraStack publication")
    frozen_inputs = frozen_node["inputs"]
    if any(frozen_inputs.get(name) != values[index] for index, name in enumerate(WIDGETS)):
        raise ManagementError("Editable and frozen CIFLoraStack values differ")
    try:
        catalog, defaults = json.loads(values[0]), json.loads(values[1])
    except (TypeError, ValueError) as exc:
        raise ManagementError("Invalid CIFLoraStack JSON") from exc
    if (
        not isinstance(catalog, list)
        or not isinstance(defaults, list)
        or len(catalog) != len(defaults)
    ):
        raise ManagementError("Invalid CIFLoraStack catalog/default")
    if any(
        not isinstance(item, dict)
        or not {"id", "label", "filename"}.issubset(item)
        or not isinstance(item["filename"], str)
        for item in catalog
    ):
        raise ManagementError("Invalid private CIFLoraStack catalog")
    public = [
        {key: item[key] for key in ("id", "label", "description", "trigger_word") if key in item}
        for item in catalog
    ]
    if declaration.get("items") != public or declaration.get("default") != defaults:
        raise ManagementError("Published and frozen CIFLoraStack values differ")
    inventory = manifest.get("technical_inventory")
    inventory_loras = inventory.get("loras") if isinstance(inventory, dict) else None
    entries = [
        item
        for item in inventory_loras or []
        if item.get("usage") == "public_stack" and item.get("parameter_id") == declaration.get("id")
    ]
    if len(entries) != 1 or any(
        entries[0].get(key) != declaration.get(key)
        for key in ("items", "default", "minimum", "maximum", "step")
    ):
        raise ManagementError("Technical inventory does not match the LoRA stack")
    return editable, frozen_inputs, declaration, entries[0], catalog


def _revision(raw: dict[str, bytes]) -> dict[str, str]:
    manifest = _parse(raw["manifest"])
    return {
        "publication_id": manifest.get("publication_id"),
        "workflow_sha256": _sha(raw["workflow"]),
        "api_sha256": _sha(raw["api"]),
        "manifest_sha256": _sha(raw["manifest"]),
    }


def _active_api_reference(api: dict, filename: str, *, ignore_stack_id: str | None = None) -> bool:
    for node_id, node in api.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        class_type = node.get("class_type", "")
        if class_type == "CIFLoraStack" and node_id != ignore_stack_id:
            try:
                if any(
                    item.get("filename") == filename
                    for item in json.loads(inputs.get("catalog_json", "[]"))
                ):
                    return True
            except (TypeError, ValueError) as exc:
                raise ManagementError("Cannot inspect a published LoRA catalog") from exc
        if "LoraLoader" in class_type and inputs.get("lora_name") == filename:
            return True
        if "Power Lora Loader" in class_type and any(
            isinstance(value, dict) and value.get("on") is True and value.get("lora") == filename
            for value in inputs.values()
        ):
            return True
    return False


def _active_editable_reference(
    workflow: dict, filename: str, *, ignore_stack_id: str | None = None
) -> bool:
    roots = workflow.get("nodes", [])
    definitions = workflow.get("definitions", {})
    subgraphs = definitions.get("subgraphs", []) if isinstance(definitions, dict) else None
    if not isinstance(roots, list) or not isinstance(subgraphs, list):
        raise ManagementError("Cannot inspect an authoring workflow")
    nodes = [(node, True) for node in roots]
    for subgraph in subgraphs:
        if not isinstance(subgraph, dict) or not isinstance(subgraph.get("nodes"), list):
            raise ManagementError("Cannot inspect an authoring subgraph")
        nodes.extend((node, False) for node in subgraph["nodes"])
    for node, is_root in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type", "")
        widgets = node.get("widgets_values", [])
        if (
            node_type == "CIFLoraStack"
            and (not is_root or str(node.get("id")) != ignore_stack_id)
            and widgets
        ):
            try:
                if any(item.get("filename") == filename for item in json.loads(widgets[0])):
                    return True
            except (TypeError, ValueError) as exc:
                raise ManagementError("Cannot inspect an authoring LoRA catalog") from exc
        if "LoraLoader" in node_type and filename in widgets:
            return True
        if "Power Lora Loader" in node_type and any(
            isinstance(value, dict) and value.get("on") is True and value.get("lora") == filename
            for value in widgets
        ):
            return True
    return False


class LoraManagement:
    def __init__(
        self,
        userdata: Path,
        model_root: Path,
        *,
        model_writer: bool,
        max_upload: int = DEFAULT_MAX_UPLOAD,
        queue: Any = None,
    ):
        self.userdata = userdata.resolve()
        self.root = model_root.resolve()
        self.model_writer = model_writer
        self.max_upload = max_upload
        self.queue = queue
        if not self.userdata.is_dir() or not self.root.is_dir():
            raise ManagementError("Management storage is unavailable", 503)

    @property
    def model_root_id(self) -> str:
        info = self.root.stat()
        return _sha(f"{info.st_dev}:{info.st_ino}".encode())

    def _opdir(self, operation_id: str) -> Path:
        return _confined(self.userdata, ".cif-lora-operations/" + _safe_id(operation_id))

    def _stage_path(self, operation_id: str, suffix: str = ".part") -> Path:
        return _confined(self.root, ".cif-lora-staging/" + _safe_id(operation_id) + suffix)

    def _publish_lock_path(self, source: str) -> Path:
        key = _sha(_safe_source(source).encode("utf-8"))
        return _confined(self.userdata, ".cif-lora-publisher-locks/" + key + ".json")

    @contextmanager
    def _source_guard(self, source: str):
        key = _sha(_safe_source(source).encode("utf-8"))
        path = _confined(self.userdata, ".cif-lora-source-guards/" + key + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _publisher_locked(self, source: str) -> bool:
        path = self._publish_lock_path(source)
        if not path.exists():
            return False
        try:
            lock = _parse(_regular_bytes(path))
            if lock.get("expires_at", 0) > time.time():
                return True
        except ManagementError:
            raise
        path.unlink(missing_ok=True)
        return False

    def acquire_publisher_lock(self, source: str) -> dict:
        source = _safe_source(source)
        with self._source_guard(source):
            return self._acquire_publisher_lock_locked(source)

    def _acquire_publisher_lock_locked(self, source: str) -> dict:
        if self._publisher_locked(source):
            raise ManagementError("Another Save & Publish is active for this workflow")
        operations = _confined(self.userdata, ".cif-lora-operations")
        if operations.exists():
            for path in operations.glob("*/journal.json"):
                path = _confined(self.userdata, path.relative_to(self.userdata).as_posix())
                journal = _parse(_regular_bytes(path))
                if journal.get("source_path") == source and journal.get("state") not in (
                    "rolled_back",
                    "finalized",
                ):
                    raise ManagementError("LoRA administration is active for this workflow")
        token = str(uuid.uuid4())
        path = self._publish_lock_path(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x") as output:
                json.dump(
                    {"source_path": source, "token": token, "expires_at": time.time() + 600}, output
                )
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError as exc:
            raise ManagementError("Another Save & Publish is active for this workflow") from exc
        return {"token": token}

    def renew_publisher_lock(self, source: str, token: str) -> dict:
        path = self._publish_lock_path(source)
        if not path.exists():
            raise ManagementError("Save & Publish lease expired")
        lock = _parse(_regular_bytes(path))
        if not isinstance(token, str) or not hmac.compare_digest(str(lock.get("token", "")), token):
            raise ManagementError("Save & Publish lease token differs", 403)
        lock["expires_at"] = time.time() + 600
        _atomic(path, _json_bytes(lock))
        return {"state": "renewed"}

    def release_publisher_lock(self, source: str, token: str) -> dict:
        path = self._publish_lock_path(source)
        if not path.exists():
            return {"state": "released"}
        lock = _parse(_regular_bytes(path))
        if not isinstance(token, str) or not hmac.compare_digest(str(lock.get("token", "")), token):
            raise ManagementError("Save & Publish lease token differs", 403)
        path.unlink()
        return {"state": "released"}

    def _journal(self, operation_id: str) -> dict:
        path = self._opdir(operation_id) / "journal.json"
        if not path.is_file():
            raise ManagementError("Unknown LoRA operation", 404)
        return _parse(path.read_bytes())

    def _save(self, operation_id: str, journal: dict) -> None:
        _atomic(self._opdir(operation_id) / "journal.json", _json_bytes(journal))

    def _bundle_paths(self, source: str) -> dict[str, Path]:
        names = _paths(source)
        return {
            key: _confined(self.userdata, name)
            for key, name in zip(("workflow", "api", "manifest"), names, strict=False)
        }

    def _bundle(self, source: str) -> dict[str, bytes]:
        return {key: _regular_bytes(path) for key, path in self._bundle_paths(source).items()}

    def bundle(self, source: str) -> dict:
        raw = self._bundle(source)
        workflow, api, manifest = (_parse(raw[key]) for key in ("workflow", "api", "manifest"))
        _, _, declaration, _, catalog = _stack(workflow, api, manifest)
        if (
            manifest.get("source_id") != source
            or manifest.get("workflow", {}).get("path") != source
            or manifest.get("api", {}).get("path") != _paths(source)[1]
        ):
            raise ManagementError("Publication paths are inconsistent")
        if manifest["workflow"].get("sha256") != _sha(raw["workflow"]) or manifest["api"].get(
            "sha256"
        ) != _sha(raw["api"]):
            raise ManagementError("Publication files differ from recorded hashes")
        return {
            "revision": _revision(raw),
            "loras": declaration["items"],
            "files": [{"id": item["id"], "filename": item["filename"]} for item in catalog],
        }

    async def stage(
        self, operation_id: str, stream: Any, *, content_length: int | None, filename: str
    ) -> dict:
        if not self.model_writer:
            raise ManagementError("This instance cannot write LoRA models", 403)
        _safe_id(operation_id)
        if (
            not isinstance(filename, str)
            or not filename.lower().endswith(".safetensors")
            or PurePosixPath(filename).name != filename
            or "\\" in filename
        ):
            raise ManagementError("Upload must be one .safetensors file", 400)
        if content_length is None or not 0 < content_length <= self.max_upload:
            raise ManagementError("Invalid upload length", 413)
        if shutil.disk_usage(self.root).free < content_length * 2:
            raise ManagementError("Insufficient model-library space", 507)
        opdir = self._opdir(operation_id)
        if (opdir / "journal.json").exists() and self._journal(operation_id).get(
            "state"
        ) != "staged":
            raise ManagementError("Operation ID is already in progress")
        opdir.mkdir(parents=True, exist_ok=True)
        stage = self._stage_path(operation_id)
        stage.parent.mkdir(parents=True, exist_ok=True)
        incoming = stage.with_name(stage.name + "." + uuid.uuid4().hex)
        digest = hashlib.sha256()
        received = 0
        try:
            with incoming.open("xb") as output:
                while chunk := await stream.read(1024 * 1024):
                    received += len(chunk)
                    if received > self.max_upload or received > content_length:
                        raise ManagementError("Upload exceeds declared limit", 413)
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if received != content_length:
                raise ManagementError("Incomplete upload", 400)
            _safetensors(incoming)
            try:
                os.link(incoming, stage)
            except FileExistsError as exc:
                if (
                    stage.is_symlink()
                    or not stage.is_file()
                    or _sha_file(stage) != digest.hexdigest()
                ):
                    raise ManagementError("Operation ID is already used with another file") from exc
        finally:
            incoming.unlink(missing_ok=True)
        result = {
            "filename": f"cif-managed/{operation_id}.safetensors",
            "sha256": digest.hexdigest(),
            "size": received,
        }
        journal = {"state": "staged", "action": "install", "file": result}
        self._save(operation_id, journal)
        return result

    def _references(self, source: str, filename: str) -> None:
        workflows = _confined(self.userdata, "workflows")
        for path in workflows.rglob("*.interface.json"):
            path = _confined(self.userdata, path.relative_to(self.userdata).as_posix())
            if path.is_symlink() or not path.is_file():
                raise ManagementError("Cannot safely inspect another publication")
            manifest = _parse(_regular_bytes(path))
            other_source = manifest.get("source_id")
            if other_source == source:
                continue
            try:
                other_api = self._bundle_paths(_safe_source(other_source))["api"]
                if _active_api_reference(_parse(_regular_bytes(other_api)), filename):
                    raise ManagementError("LoRA is used by another published workflow")
            except ManagementError:
                raise
        for path in workflows.rglob("*.json"):
            path = _confined(self.userdata, path.relative_to(self.userdata).as_posix())
            if path.name.endswith((".api.json", ".interface.json")) or path.is_symlink():
                continue
            relative = path.relative_to(self.userdata).as_posix()
            if relative == source:
                continue
            if _active_editable_reference(_parse(_regular_bytes(path)), filename):
                raise ManagementError("LoRA is used by another authoring workflow")
        if self.queue is None:
            raise ManagementError("Native ComfyUI queue is unavailable")
        try:
            running, pending = self.queue.get_current_queue()
            if running or pending:
                raise ManagementError("Native ComfyUI queue must be empty before LoRA removal")
        except ManagementError:
            raise
        except Exception as exc:
            raise ManagementError("Cannot inspect native ComfyUI queue") from exc

    def prepare(self, operation_id: str, payload: dict) -> dict:
        source = _safe_source(payload.get("source_path"))
        with self._source_guard(source):
            return self._prepare_locked(operation_id, payload, source)

    def _prepare_locked(self, operation_id: str, payload: dict, source: str) -> dict:
        _safe_id(operation_id)
        if self._publisher_locked(source):
            raise ManagementError("Save & Publish is active for this workflow")
        expected = payload.get("expected_revision")
        change = payload.get("change")
        new_id = payload.get("publication_id")
        published_at = payload.get("published_at")
        if not isinstance(expected, dict) or not isinstance(change, dict):
            raise ManagementError("Invalid operation request", 400)
        try:
            uuid.UUID(new_id)
            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ManagementError("Invalid new publication identity", 400) from exc
        opdir = self._opdir(operation_id)
        if (opdir / "journal.json").exists():
            current_journal = self._journal(operation_id)
            if current_journal.get("state") == "prepared":
                if current_journal.get("request") != payload:
                    raise ManagementError("Operation ID is already prepared for another change")
                return {
                    "state": "prepared",
                    "candidate_revision": current_journal["candidate_revision"],
                    "candidate_hashes": current_journal["candidate_revision"],
                    "filename": current_journal["filename"],
                }
            if current_journal.get("state") != "staged":
                raise ManagementError("Operation is already in progress")
        raw = self._bundle(source)
        old = _revision(raw)
        if old != expected:
            raise ManagementError("Publication revision changed")
        workflow, api, manifest = (
            copy.deepcopy(_parse(raw[key])) for key in ("workflow", "api", "manifest")
        )
        self.bundle(source)  # verify hashes and publication structure before changing anything
        editable, frozen, declaration, inventory, catalog = _stack(workflow, api, manifest)
        action = change.get("action")
        if action == "install":
            lora_id, label, trigger = (
                change.get("id"),
                change.get("label"),
                change.get("trigger_word"),
            )
            filename, expected_hash = change.get("filename"), change.get("sha256")
            if (
                not isinstance(lora_id, str)
                or not PUBLIC_ID.fullmatch(lora_id)
                or any(item["id"] == lora_id for item in catalog)
            ):
                raise ManagementError("Invalid or duplicate LoRA ID", 400)
            if (
                not isinstance(label, str)
                or not label.strip()
                or len(label) > 120
                or not isinstance(trigger, str)
                or not trigger.strip()
                or len(trigger) > 120
            ):
                raise ManagementError("LoRA title and trigger word are required", 400)
            if (
                len(catalog) >= 100
                or filename != f"cif-managed/{operation_id}.safetensors"
                or not isinstance(expected_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            ):
                raise ManagementError("Invalid LoRA upload declaration", 400)
            stage = self._stage_path(operation_id)
            if not stage.is_file() or stage.is_symlink() or _sha_file(stage) != expected_hash:
                raise ManagementError("Validated LoRA upload is unavailable")
            _safetensors(stage)
            catalog.append(
                {
                    "id": lora_id,
                    "label": label.strip(),
                    "filename": filename,
                    "trigger_word": trigger.strip(),
                }
            )
        elif action == "remove":
            lora_id = change.get("id")
            found = [item for item in catalog if item.get("id") == lora_id]
            if len(found) != 1:
                raise ManagementError("LoRA is not in this publication", 404)
            filename = _safe_model_name(found[0]["filename"])
            if any(item is not found[0] and item.get("filename") == filename for item in catalog):
                raise ManagementError("LoRA file is shared by another catalog entry")
            _file_under(self.root, filename)
            if _active_api_reference(api, filename, ignore_stack_id=str(editable["id"])):
                raise ManagementError("LoRA is used by another active loader in this workflow")
            if _active_editable_reference(workflow, filename, ignore_stack_id=str(editable["id"])):
                raise ManagementError("LoRA is used by another loader in this authoring workflow")
            self._references(source, filename)
            catalog.remove(found[0])
        else:
            raise ManagementError("Unsupported LoRA change", 400)
        default = [{"id": item["id"], "strength": 0} for item in catalog]
        public = [
            {
                key: item[key]
                for key in ("id", "label", "description", "trigger_word")
                if key in item
            }
            for item in catalog
        ]
        editable["widgets_values"][0] = frozen["catalog_json"] = json.dumps(
            catalog, ensure_ascii=False
        )
        editable["widgets_values"][1] = frozen["value"] = json.dumps(default, ensure_ascii=False)
        declaration["items"] = inventory["items"] = public
        declaration["default"] = inventory["default"] = default
        manifest["publication_id"] = new_id
        manifest["published_at"] = published_at
        manifest["workflow"].pop("compiled_sha256", None)
        new_raw = {"workflow": _json_bytes(workflow), "api": _json_bytes(api)}
        manifest["workflow"]["sha256"] = _sha(new_raw["workflow"])
        manifest["api"]["sha256"] = _sha(new_raw["api"])
        new_raw["manifest"] = _json_bytes(manifest)
        candidate = _revision(new_raw)
        if candidate["publication_id"] != new_id:
            raise ManagementError("Candidate publication identity mismatch")
        opdir.mkdir(parents=True, exist_ok=True)
        for key in ("workflow", "api", "manifest"):
            _atomic(opdir / f"old.{key}", raw[key])
            _atomic(opdir / f"new.{key}", new_raw[key])
        journal = {
            "state": "prepared",
            "action": action,
            "source_path": source,
            "expected_revision": old,
            "candidate_revision": candidate,
            "filename": filename,
            "model_writer": self.model_writer,
            "request": payload,
        }
        self._save(operation_id, journal)
        return {
            "state": "prepared",
            "candidate_revision": candidate,
            "candidate_hashes": candidate,
            "filename": filename,
        }

    def candidate(self, operation_id: str) -> dict:
        journal = self._journal(operation_id)
        if journal["state"] not in ("prepared", "committing", "committed", "quarantined"):
            raise ManagementError("Candidate is unavailable")
        opdir = self._opdir(operation_id)
        return {
            "workflow_b64": base64.b64encode((opdir / "new.workflow").read_bytes()).decode(),
            "api_b64": base64.b64encode((opdir / "new.api").read_bytes()).decode(),
            "manifest_b64": base64.b64encode((opdir / "new.manifest").read_bytes()).decode(),
            "revision": journal["candidate_revision"],
        }

    def commit(self, operation_id: str) -> dict:
        journal = self._journal(operation_id)
        if "source_path" not in journal:
            raise ManagementError("Operation is not prepared")
        with self._source_guard(journal["source_path"]):
            return self._commit_locked(operation_id)

    def _commit_locked(self, operation_id: str) -> dict:
        journal = self._journal(operation_id)
        if journal["state"] == "committed":
            return {"state": "committed", "revision": journal["candidate_revision"]}
        if journal["state"] not in ("prepared", "committing"):
            raise ManagementError("Operation is not prepared")
        source = journal["source_path"]
        if self._publisher_locked(source):
            raise ManagementError("Save & Publish is active for this workflow")
        paths = self._bundle_paths(source)
        opdir = self._opdir(operation_id)
        current = {key: _regular_bytes(path) for key, path in paths.items()}
        for key in paths:
            if current[key] not in (
                (opdir / f"old.{key}").read_bytes(),
                (opdir / f"new.{key}").read_bytes(),
            ):
                raise ManagementError("Publication changed during operation")
        if journal["action"] == "install":
            filename = journal["filename"]
            target = _file_under(self.root, filename, must_exist=False)
            stage = self._stage_path(operation_id)
            expected_hash = journal["request"]["change"]["sha256"]
            if self.model_writer and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                if not stage.is_file() or stage.is_symlink():
                    raise ManagementError("Uploaded LoRA staging file is missing")
                if _sha_file(stage) != expected_hash:
                    raise ManagementError("Uploaded LoRA staging file changed")
                os.replace(stage, target)
            _file_under(self.root, filename)
            if _sha_file(target) != expected_hash:
                raise ManagementError("Installed LoRA file changed")
        journal["state"] = "committing"
        self._save(operation_id, journal)
        try:
            for key in ("workflow", "api", "manifest"):
                _atomic(paths[key], (opdir / f"new.{key}").read_bytes())
            if _revision(self._bundle(source)) != journal["candidate_revision"]:
                raise ManagementError("Committed publication did not verify")
        except Exception:
            self.rollback(operation_id)
            raise
        journal["state"] = "committed"
        self._save(operation_id, journal)
        return {"state": "committed", "revision": journal["candidate_revision"]}

    def quarantine(self, operation_id: str) -> dict:
        journal = self._journal(operation_id)
        if journal["action"] != "remove":
            raise ManagementError("Only removal has a quarantine phase", 400)
        if journal["state"] == "quarantined":
            return {"state": "quarantined"}
        if journal["state"] != "committed":
            raise ManagementError("Removal must be committed before quarantine")
        if self.model_writer:
            filename = journal["filename"]
            self._references(journal["source_path"], filename)
            target = _file_under(self.root, filename)
            quarantine = self._stage_path(operation_id, ".quarantine")
            os.replace(target, quarantine)
        journal["state"] = "quarantined"
        self._save(operation_id, journal)
        return {"state": "quarantined"}

    def rollback(self, operation_id: str) -> dict:
        _safe_id(operation_id)
        try:
            journal = self._journal(operation_id)
        except ManagementError as exc:
            if exc.status == 404:
                # A process can stop after linking validated upload bytes but before
                # the first journal write. It can also stop during the streamed
                # upload, leaving the uniquely named incoming file behind.
                if self.model_writer:
                    stage = self._stage_path(operation_id)
                    stage.unlink(missing_ok=True)
                    for incoming in stage.parent.glob(stage.name + ".*"):
                        incoming.unlink()
                return {"state": "rolled_back"}
            raise
        if journal["state"] == "rolled_back":
            return {"state": "rolled_back"}
        if journal["state"] == "finalized":
            raise ManagementError("Finalized operation cannot be rolled back")
        opdir = self._opdir(operation_id)
        try:
            if "source_path" in journal:
                paths = self._bundle_paths(journal["source_path"])
                for key in ("workflow", "api", "manifest"):
                    current = _regular_bytes(paths[key])
                    if current not in (
                        (opdir / f"old.{key}").read_bytes(),
                        (opdir / f"new.{key}").read_bytes(),
                    ):
                        raise ManagementError("External publication edit prevents rollback")
            if self.model_writer and journal["action"] == "remove":
                quarantine = self._stage_path(operation_id, ".quarantine")
                if quarantine.is_file():
                    target = _file_under(self.root, journal["filename"], must_exist=False)
                    if target.exists():
                        raise ManagementError("LoRA file was replaced during rollback")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(quarantine, target)
            if "source_path" in journal:
                for key in ("workflow", "api", "manifest"):
                    _atomic(paths[key], (opdir / f"old.{key}").read_bytes())
            if self.model_writer and journal["action"] == "install":
                if "source_path" in journal:
                    _file_under(self.root, journal["filename"], must_exist=False).unlink(
                        missing_ok=True
                    )
                stage = self._stage_path(operation_id)
                stage.unlink(missing_ok=True)
                for incoming in stage.parent.glob(stage.name + ".*"):
                    incoming.unlink()
        except Exception:
            journal["state"] = "repair_required"
            self._save(operation_id, journal)
            raise
        journal["state"] = "rolled_back"
        self._save(operation_id, journal)
        return {"state": "rolled_back"}

    def finalize(self, operation_id: str) -> dict:
        journal = self._journal(operation_id)
        if journal["state"] == "finalized":
            return {"state": "finalized"}
        required = (
            {"quarantined"}
            if journal["action"] == "remove" and self.model_writer
            else {"committed", "quarantined"}
        )
        if journal["state"] not in required:
            raise ManagementError("Operation is not ready to finalize")
        if self.model_writer and journal["action"] == "remove":
            self._stage_path(operation_id, ".quarantine").unlink(missing_ok=True)
        journal["state"] = "finalized"
        self._save(operation_id, journal)
        for key in ("workflow", "api", "manifest"):
            for prefix in ("old", "new"):
                (self._opdir(operation_id) / f"{prefix}.{key}").unlink(missing_ok=True)
        return {"state": "finalized"}

    def status(self, operation_id: str) -> dict:
        journal = self._journal(operation_id)
        return {
            key: journal.get(key)
            for key in ("state", "action", "candidate_revision", "expected_revision")
        }
