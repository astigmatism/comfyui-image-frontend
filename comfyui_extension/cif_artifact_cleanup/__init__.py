"""ComfyUI companion route for frontend-authoritative artifact ownership.

Install this directory in each ComfyUI runtime's ``custom_nodes`` directory. The frontend calls
the route only after every referenced file has been copied into its own durable storage.

Set ``CIF_RETAIN_STAGES`` on the ComfyUI process to keep the stage artefacts a delete would have
removed, so a defect can still be attributed to the stage that produced it. See README.md.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import time
import uuid
from pathlib import PurePosixPath
from typing import Any

import folder_paths
from aiohttp import web
from server import PromptServer

_MAX_REQUEST_BYTES = 512 * 1024
_MAX_ARTIFACTS = 4096
_ROUTE = "/comfyui-image-frontend/artifacts/delete"
_RETENTION_ROUTE = "/comfyui-image-frontend/artifacts/retention"

# Why this switch exists: every stage image of a generation (``first_pass``, ``color_matched``,
# ``sd_upscaled``, ``final``) is deleted seconds after the run finished, so "which stage produced
# this defect?" cannot be answered after the fact. ``CIF_RETAIN_STAGES`` moves the files a delete
# would have removed into a bounded retention directory instead of deleting them. The values are
# ``off`` (default, today's behaviour), ``last`` (keep the newest generation) or a positive
# integer up to 32 (keep the newest N generations). It is bounded because one 5616x3152 PNG here
# is ~20 MB and a generation writes four of them, so an unbounded switch is its own defect.
_RETENTION_ENV = "CIF_RETAIN_STAGES"
_RETENTION_MAX_GENERATIONS = 32
_RETENTION_SUBFOLDER = "comfyui-image-frontend-retention"
# A generation is one HTTP request, and its directory name is ``<UTC-YYYYMMDDTHHMMSSZ>-<8 hex>``.
# The prune decides age by sorting those names, so the shape is load-bearing: only directories
# matching it are ever removed, which also protects anything an operator puts in the root.
_GROUP_NAME = re.compile(r"\A\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
# Why a cap: the report is read on every catalog refresh, and a retention root an operator let
# fill with years of groups must not turn that cheap probe into a long walk. Counting stops; the
# numbers are then a lower bound rather than a lie about the filesystem.
_MAX_REPORT_DIRECTORIES = 512
_MAX_REPORT_FILES = 20_000


def _safe_locator(value: Any) -> tuple[str, str, str] | None:
    if not isinstance(value, dict):
        return None
    filename = value.get("filename")
    subfolder = value.get("subfolder", "")
    storage_type = value.get("type", "output")
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > 500
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or PurePosixPath(filename).name != filename
        or not isinstance(subfolder, str)
        or len(subfolder) > 500
        or subfolder.startswith("/")
        or "\\" in subfolder
        or "//" in subfolder
        or any(part in {".", ".."} for part in PurePosixPath(subfolder).parts)
        or (subfolder and str(PurePosixPath(subfolder)) != subfolder)
        or storage_type not in {"output", "temp"}
    ):
        return None
    return filename, subfolder, storage_type


def _storage_root(storage_type: str) -> str:
    if storage_type == "output":
        return folder_paths.get_output_directory()
    return folder_paths.get_temp_directory()


def _target_path(filename: str, subfolder: str, storage_type: str) -> tuple[str, str] | None:
    root = os.path.realpath(_storage_root(storage_type))
    target = os.path.realpath(os.path.join(root, *PurePosixPath(subfolder).parts, filename))
    try:
        contained = os.path.commonpath((root, target)) == root
    except ValueError:
        contained = False
    return (root, target) if contained and target != root else None


def _remove_empty_parents(path: str, root: str) -> None:
    current = os.path.dirname(path)
    while current != root and os.path.commonpath((root, current)) == root:
        try:
            os.rmdir(current)
        except OSError:
            break
        current = os.path.dirname(current)


def _retention_setting() -> tuple[str, int, str | None]:
    """Read ``CIF_RETAIN_STAGES`` into ``(mode, generations_to_keep, invalid_value)``.

    The mode string is what both routes report: ``off``, ``last`` or the normalised integer as a
    string. An invalid value falls back to ``off`` and is returned as ``invalid_value`` so the
    route response carries the operator's typo instead of cleanup raising over a debug switch —
    a wrong value must never stop the pipeline from reclaiming its disk space.
    """

    raw = (os.environ.get(_RETENTION_ENV) or "").strip().lower()
    if raw in {"", "off", "0", "false", "none"}:
        # "0" and the empty string mean the same thing as off: nothing to keep. They are not
        # reported as invalid because they are what an operator writes to disable the switch.
        return "off", 0, None
    if raw == "last":
        return "last", 1, None
    if raw.isdigit():
        limit = int(raw)
        if 1 <= limit <= _RETENTION_MAX_GENERATIONS:
            return str(limit), limit, None
    return "off", 0, raw


def _new_group_name() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"


def _retention_root(storage_type: str) -> str | None:
    """Absolute retention directory for a storage type, or ``None`` when it would escape.

    Why the containment test: ``realpath`` follows symlinks, so a retention directory an
    operator symlinked onto another volume resolves outside the storage root.
    Refusing it is what keeps "never follow symlinks out of the retention root" true, and the
    refusal is silent (the artefact is then simply deleted) because a move is a debug nicety.
    """

    root = os.path.realpath(_storage_root(storage_type))
    candidate = os.path.realpath(os.path.join(root, _RETENTION_SUBFOLDER))
    if candidate == root or not candidate.startswith(root + os.sep):
        return None
    return candidate


def _retain_file(path: str, storage_type: str, group: str) -> str | None:
    """Move ``path`` into the generation directory ``group``. Returns that directory, or ``None``.

    ``os.replace`` is the normal case — both ends live under the same ComfyUI storage root, so
    the rename is atomic and the file can never appear twice. EXDEV (a symlinked ``output`` or
    ``temp`` root on another filesystem) falls back to copy+remove, accepting the documented
    partial-failure window: the app already holds its own durable copy, so a half-finished move
    only costs a debug artefact. Any failure returns ``None`` and the caller deletes instead,
    which is why a misconfigured retention directory cannot fill the disk unnoticed.
    """

    retention = _retention_root(storage_type)
    if retention is None:
        return None
    # Why computed here: the relative path is the locator path under the storage root, which is
    # what the retained layout mirrors; deriving it in this sync helper keeps the async route free
    # of filesystem calls of its own.
    root = os.path.realpath(_storage_root(storage_type))
    relative = os.path.relpath(path, root)
    group_directory = os.path.join(retention, group)
    destination = os.path.join(group_directory, *PurePosixPath(relative).parts)
    # Why the second containment test: ``relative`` is derived from a validated locator, but the
    # destination is built from strings, so re-check that the retained path stays inside this
    # generation's directory before writing through it.
    if os.path.commonpath((retention, destination)) != retention:
        return None

    def attempt() -> str:
        """One rename attempt: ``moved``, ``missing``, ``copy`` (EXDEV) or ``failed``."""

        try:
            os.replace(path, destination)
        except FileNotFoundError:
            return "missing"
        except IsADirectoryError:
            return "failed"
        except OSError as exc:
            return "copy" if exc.errno == errno.EXDEV else "failed"
        return "moved"

    outcome = attempt()
    if outcome == "missing":
        # Why the retry: this is the first retained file of the generation, so the group and its
        # stage subfolders do not exist yet. Creating the directories lazily means a request that
        # retained nothing (every file was already gone) leaves no empty generation directory
        # behind, which would otherwise inflate the reported count.
        try:
            os.makedirs(os.path.dirname(destination), 0o750, exist_ok=True)
        except OSError:
            return None
        outcome = attempt()
    if outcome == "copy":
        # Why the fallback: EXDEV means the retention root and the pipeline folder are on
        # different filesystems, so no atomic rename exists at all. The app already holds its own
        # durable copy, so a half-finished copy costs a debug artefact and nothing more.
        try:
            shutil.copy2(path, destination)
            os.remove(path)
        except OSError:
            return None
        return group_directory
    return group_directory if outcome == "moved" else None


def _prune_retention(storage_type: str, keep: int, current: str) -> None:
    """Keep the newest ``keep`` generation directories, never deleting ``current``.

    Age is the directory name, not mtime: mtime moves when a file inside is opened for reading
    during debugging, and a debug switch must not forget a generation because somebody looked at
    it. ``current`` is excluded from the age ranking and always kept, so a second request in the
    same second (same timestamp prefix, so the order is the random suffix) can never throw away
    the group it just wrote; the directory count stays at ``keep`` either way.
    """

    if keep <= 0:
        return
    retention = _retention_root(storage_type)
    if retention is None or not os.path.isdir(retention):
        return
    try:
        entries = os.listdir(retention)
    except OSError:
        return
    # Why the islink guard: a symlinked group could be pruned with rmtree and delete files that
    # live outside the retention root, which the count bound must never be able to do.
    groups = sorted(
        name
        for name in entries
        if _GROUP_NAME.match(name) and os.path.isdir(os.path.join(retention, name))
    )
    if len(groups) <= keep:
        return
    current_name = os.path.basename(current)
    others = [name for name in groups if name != current_name]
    survivors = {current_name} | set(others[len(others) - (keep - 1) :] if keep > 1 else [])
    for name in groups:
        if name in survivors:
            continue
        path = os.path.join(retention, name)
        try:
            shutil.rmtree(path)
        except OSError:
            # A directory that will not go away is reported by the GET route, which counts what
            # is actually on disk; failing here must not fail the cleanup request.
            continue


def _count_group(directory: str, counters: dict[str, int]) -> None:
    """Add one generation directory's file count and bytes, bounded by the report caps."""

    # Why followlinks=False: a symlink inside a retained generation must be counted or ignored,
    # never walked into — that is the rule that keeps the report from following files out of the
    # retention root.
    for current_root, _directories, names in os.walk(directory, followlinks=False):
        for name in names:
            if counters["files"] >= _MAX_REPORT_FILES:
                return
            counters["files"] += 1
            try:
                # Why lstat: a retained symlink's own size is the honest number; following it
                # would attribute some unrelated file's size to the retained generation.
                counters["bytes"] += os.lstat(os.path.join(current_root, name)).st_size
            except OSError:
                continue


def _retention_report() -> dict[str, Any]:
    """Everything the app shows in the health tab about this switch.

    The scan covers both storage roots even when the mode is ``off``: switching the debug feature
    off does not delete what it already kept, and an operator needs to see what is still there.
    """

    mode, keep, invalid = _retention_setting()
    counters = {"directories": 0, "files": 0, "bytes": 0}
    directory: str | None = None
    for storage_type in ("output", "temp"):
        retention = _retention_root(storage_type)
        if retention is None:
            continue
        if directory is None:
            directory = retention
        if not os.path.isdir(retention):
            continue
        try:
            names = sorted(os.listdir(retention))
        except OSError:
            continue
        for name in names:
            if counters["directories"] >= _MAX_REPORT_DIRECTORIES:
                break
            path = os.path.join(retention, name)
            if not _GROUP_NAME.match(name) or not os.path.isdir(path):
                continue
            counters["directories"] += 1
            _count_group(path, counters)
    report: dict[str, Any] = {
        # ``mode`` is ``off`` or the normalised value, so the app can print exactly what this
        # runtime is doing without re-deriving the semantics of the environment variable.
        "mode": mode,
        "limit": keep,
        "retained_generations": counters["directories"],
        "retained_files": counters["files"],
        "retained_bytes": counters["bytes"],
        "directory": directory or os.path.join(str(_storage_root("output")), _RETENTION_SUBFOLDER),
    }
    if invalid is not None:
        # Reported, never raised: the response tells the operator the switch did not take.
        report["invalid_value"] = invalid
    return report


async def _read_body(request: web.Request) -> bytes:
    if request.content_length is not None and request.content_length > _MAX_REQUEST_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=_MAX_REQUEST_BYTES,
            actual_size=request.content_length,
        )
    body = await request.content.read(_MAX_REQUEST_BYTES + 1)
    if len(body) > _MAX_REQUEST_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=_MAX_REQUEST_BYTES,
            actual_size=len(body),
        )
    return body


@PromptServer.instance.routes.get(_RETENTION_ROUTE)
async def retention_frontend_artifacts(request: web.Request) -> web.Response:
    """Read-only view of the retention switch. Never mutates, never deletes."""

    return web.json_response(_retention_report())


@PromptServer.instance.routes.post(_ROUTE)
async def delete_frontend_artifacts(request: web.Request) -> web.Response:
    body = await _read_body(request)
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise web.HTTPBadRequest(text="Invalid JSON payload") from exc
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list) or len(artifacts) > _MAX_ARTIFACTS:
        raise web.HTTPBadRequest(text="Invalid artifact list")

    # One request is one generation, so it becomes one retention directory. The name is created
    # once per request even when the generation wrote to both output and temp storage, which is
    # what lets an operator recognise a single run across the two roots.
    mode, keep, invalid = _retention_setting()
    group = _new_group_name() if keep else None

    deleted = 0
    missing = 0
    retained = 0
    # Storage type -> the directory this request wrote there, so each root is pruned once.
    written: dict[str, str] = {}
    for raw_reference in artifacts:
        locator = _safe_locator(raw_reference)
        if locator is None:
            raise web.HTTPBadRequest(text="Unsafe artifact locator")
        target = _target_path(*locator)
        if target is None:
            raise web.HTTPBadRequest(text="Unsafe artifact path")
        root, path = target
        group_directory = None
        if keep and group is not None:
            group_directory = _retain_file(path, locator[2], group)
            if group_directory is not None:
                retained += 1
                written[locator[2]] = group_directory
        if group_directory is None:
            try:
                os.remove(path)
            except FileNotFoundError:
                missing += 1
                continue
            except IsADirectoryError as exc:
                raise web.HTTPBadRequest(text="Artifact locator is not a file") from exc
            deleted += 1
        # A retained file has left the pipeline folder too, so the emptied stage subfolders are
        # pruned exactly as before: keeping a file must not leave empty trees behind.
        _remove_empty_parents(path, root)

    for storage_type, group_directory in written.items():
        _prune_retention(storage_type, keep, group_directory)

    return web.json_response(
        {
            "deleted": deleted,
            "missing": missing,
            # New fields. ``deleted``/``missing`` keep their exact meaning so an existing client
            # is unaffected: they count only files that were really removed.
            "retained": retained,
            "retention": mode,
            **({"invalid_value": invalid} if invalid is not None else {}),
        }
    )


NODE_CLASS_MAPPINGS: dict[str, type[Any]] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
