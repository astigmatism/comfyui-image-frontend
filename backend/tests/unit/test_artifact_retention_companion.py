"""Companion-side tests for ``CIF_RETAIN_STAGES`` (``cif_artifact_cleanup/__init__.py``).

The companion is loaded from its file with ``folder_paths``, ``server`` and ``aiohttp`` stubbed,
which is how the ComfyUI-side suites work (see
``comfyui_extension/comfyui-image-frontend-interface/tests/test_lora_management_routes.py``).
Those files are not collected by pytest — ``testpaths = ["backend/tests"]`` in pyproject.toml and
no gate runs ``comfyui_extension/**/tests`` — so this file lives in the backend suite to be part
of the gate while still exercising the real ComfyUI-side module. No ComfyUI import and no network
access happens here.

The handlers are aiohttp handlers, so they are called directly through the route table the stub
``PromptServer`` captured; that also pins the method and path each route is registered on.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest

COMPANION = (
    Path(__file__).resolve().parents[3]
    / "comfyui_extension"
    / "cif_artifact_cleanup"
    / "__init__.py"
)
DELETE_ROUTE = "/comfyui-image-frontend/artifacts/delete"
RETENTION_ROUTE = "/comfyui-image-frontend/artifacts/retention"
STAGE = "comfyui-image-frontend/moody-krea2-advanced-v1/final"
OTHER_STAGE = "comfyui-image-frontend/moody-krea2-advanced-v1/first_pass"
RETENTION = "comfyui-image-frontend-retention"


# --- aiohttp / ComfyUI stubs ---------------------------------------------------------------


class _Response:
    """Stand-in for ``web.Response``: only the body and status matter to these tests."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def json(self) -> Any:
        return json.loads(self.body)


def _json_response(payload: Any, **_kwargs: Any) -> _Response:
    return _Response(json.dumps(payload).encode())


class _HttpError(Exception):
    status_code = 500

    def __init__(self, *, text: str = "", **_kwargs: Any) -> None:
        super().__init__(text)
        self.text = text


class _BadRequest(_HttpError):
    status_code = 400


class _TooLarge(_HttpError):
    status_code = 413


class _Routes:
    """Captures the decorated handlers the way ComfyUI's aiohttp route table does."""

    def __init__(self) -> None:
        self.handlers: dict[tuple[str, str], Any] = {}

    def _register(self, method: str, path: str):  # type: ignore[no-untyped-def]
        def decorator(handler):  # type: ignore[no-untyped-def]
            self.handlers[(method, path)] = handler
            return handler

        return decorator

    def post(self, path: str):  # type: ignore[no-untyped-def]
        return self._register("POST", path)

    def get(self, path: str):  # type: ignore[no-untyped-def]
        return self._register("GET", path)


class _Reader:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, _limit: int) -> bytes:
        return self._body


def _request(payload: Any) -> types.SimpleNamespace:
    body = json.dumps(payload).encode()
    return types.SimpleNamespace(content_length=len(body), content=_Reader(body))


class _GroupIds:
    """Deterministic group names: the prune ranks generations by name, so tests own the order."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def new(self) -> str:
        name = f"20260101T000000Z-{len(self.names):08x}"
        self.names.append(name)
        return name


def load_companion(tmp_path: Path) -> tuple[Any, _Routes, Path, Path]:
    """Import the companion with ComfyUI stubbed; returns it with its route table and roots."""

    output = tmp_path / "output"
    temp = tmp_path / "temp"
    output.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)

    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_output_directory = lambda: str(output)  # type: ignore[attr-defined]
    folder_paths.get_temp_directory = lambda: str(temp)  # type: ignore[attr-defined]

    routes = _Routes()
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(  # type: ignore[attr-defined]
        instance=types.SimpleNamespace(routes=routes)
    )

    aiohttp = types.ModuleType("aiohttp")
    aiohttp.web = types.SimpleNamespace(  # type: ignore[attr-defined]
        Request=object,
        Response=_Response,
        json_response=_json_response,
        HTTPBadRequest=_BadRequest,
        HTTPRequestEntityTooLarge=_TooLarge,
    )

    stubs = {"folder_paths": folder_paths, "server": server, "aiohttp": aiohttp}
    previous = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("cif_artifact_cleanup_under_test", COMPANION)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module, routes, output, temp


# --- helpers -------------------------------------------------------------------------------


def call(routes: _Routes, method: str, path: str, payload: Any | None = None) -> Any:
    handler = routes.handlers[(method, path)]
    return asyncio.run(handler(_request(payload if payload is not None else {}))).json()


def artifact(root: Path, subfolder: str, name: str, body: bytes = b"png") -> Path:
    path = root / subfolder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def locator(name: str, subfolder: str = STAGE, storage_type: str = "output") -> dict[str, str]:
    return {"filename": name, "subfolder": subfolder, "type": storage_type}


def make_group(root: Path, name: str, body: bytes = b"old") -> Path:
    """A generation directory an earlier run (or an operator) left behind."""

    group = root / RETENTION / name
    target = group / STAGE / "stage.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return group


# Generation directories are the only thing the companion may prune, so the test helper counts
# exactly those and ignores an operator's own folders in the same root.
GENERATION = re.compile(r"\A\d{8}T\d{6}Z-[0-9a-f]{8}\Z")


def groups(root: Path) -> list[str]:
    directory = root / RETENTION
    return sorted(
        path.name for path in directory.iterdir() if path.is_dir() and GENERATION.match(path.name)
    )


@pytest.fixture()
def companion(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Fresh companion module, storage roots and deterministic group names for one test."""

    module, routes, output, temp = load_companion(tmp_path)
    monkeypatch.delenv("CIF_RETAIN_STAGES", raising=False)
    identifiers = _GroupIds()
    monkeypatch.setattr(module, "_new_group_name", identifiers.new)
    return types.SimpleNamespace(
        module=module, routes=routes, output=output, temp=temp, ids=identifiers
    )


# --- off is today's behaviour --------------------------------------------------------------


def test_off_deletes_and_reports_yesterdays_response(companion):
    """The default must be the behaviour from before the switch, plus the two new fields."""

    artifact(companion.output, STAGE, "final.png", b"final-bytes")

    body = call(companion.routes, "POST", DELETE_ROUTE, {"artifacts": [locator("final.png")]})
    assert body == {"deleted": 1, "missing": 0, "retained": 0, "retention": "off"}
    assert not (companion.output / RETENTION).exists()
    # The emptied stage folders are still pruned, exactly as before the switch existed.
    assert not (companion.output / STAGE).exists()

    report = call(companion.routes, "GET", RETENTION_ROUTE)
    assert report == {
        "mode": "off",
        "limit": 0,
        "retained_generations": 0,
        "retained_files": 0,
        "retained_bytes": 0,
        "directory": os.path.realpath(companion.output / RETENTION),
    }


def test_existing_routes_and_their_methods_are_registered(companion):
    """A route renamed by accident would silently disable the feature, so the paths are pinned."""

    assert sorted(companion.routes.handlers) == [("GET", RETENTION_ROUTE), ("POST", DELETE_ROUTE)]


def test_missing_files_are_still_reported_as_missing(companion, monkeypatch):
    """``missing`` keeps counting files that were already gone; retention does not absorb them."""

    monkeypatch.setenv("CIF_RETAIN_STAGES", "last")
    artifact(companion.output, STAGE, "kept.png")

    body = call(
        companion.routes,
        "POST",
        DELETE_ROUTE,
        {"artifacts": [locator("kept.png"), locator("gone.png")]},
    )
    assert body == {"deleted": 0, "missing": 1, "retained": 1, "retention": "last"}
    # Nothing was retained for the missing file, so the one group holds exactly one file.
    assert len(list((companion.output / RETENTION).rglob("*.png"))) == 1


# --- retention moves -----------------------------------------------------------------------


def test_move_preserves_relative_path_and_bytes(companion, monkeypatch):
    monkeypatch.setenv("CIF_RETAIN_STAGES", "last")
    source = artifact(companion.output, STAGE, "first_pass.png", b"stage-bytes")

    body = call(companion.routes, "POST", DELETE_ROUTE, {"artifacts": [locator("first_pass.png")]})
    assert body["retained"] == 1 and body["deleted"] == 0

    retention_root = companion.output / RETENTION
    (group,) = groups(companion.output)
    retained = retention_root / group / STAGE / "first_pass.png"
    # The relative subfolder path is preserved, so a retained file sits where the pipeline put it
    # — that mapping is the whole point of the switch.
    assert retained.read_bytes() == b"stage-bytes"
    assert not source.exists()

    report = call(companion.routes, "GET", RETENTION_ROUTE)
    assert report == {
        "mode": "last",
        "limit": 1,
        "retained_generations": 1,
        "retained_files": 1,
        "retained_bytes": len(b"stage-bytes"),
        "directory": os.path.realpath(retention_root),
    }


def test_last_keeps_newest_group_and_prunes_older_ones(companion, monkeypatch):
    monkeypatch.setenv("CIF_RETAIN_STAGES", "last")
    retention_root = companion.output / RETENTION
    older = make_group(companion.output, "20200101T000000Z-aaaaaaaa")
    second = make_group(companion.output, "20200102T000000Z-bbbbbbbb")
    artifact(companion.output, STAGE, "final.png")

    call(companion.routes, "POST", DELETE_ROUTE, {"artifacts": [locator("final.png")]})

    (group,) = groups(companion.output)
    assert (retention_root / group / STAGE / "final.png").is_file()
    assert not older.exists() and not second.exists()


def test_two_keeps_two_generations_and_prunes_the_oldest(companion, monkeypatch):
    monkeypatch.setenv("CIF_RETAIN_STAGES", "2")
    retention_root = companion.output / RETENTION
    ancient = make_group(companion.output, "20200101T000000Z-aaaaaaaa")
    previous = make_group(companion.output, "20200102T000000Z-bbbbbbbb")
    artifact(companion.output, STAGE, "g1.png")
    artifact(companion.output, OTHER_STAGE, "g2.png")

    call(companion.routes, "POST", DELETE_ROUTE, {"artifacts": [locator("g1.png")]})
    # Only the oldest generation goes, so a retained generation that was already inside the
    # limit is untouched by the request that filled it.
    assert not ancient.exists() and previous.exists()
    assert groups(companion.output) == ["20200102T000000Z-bbbbbbbb", companion.ids.names[0]]

    call(
        companion.routes,
        "POST",
        DELETE_ROUTE,
        {"artifacts": [locator("g2.png", OTHER_STAGE)]},
    )

    assert groups(companion.output) == companion.ids.names[-2:]
    assert {path.name for path in retention_root.rglob("g*.png")} == {"g1.png", "g2.png"}


def test_count_stays_bounded_over_many_generations(companion, monkeypatch):
    """The operational promise: N is a hard ceiling on directories, whatever the load."""

    monkeypatch.setenv("CIF_RETAIN_STAGES", "3")
    retention_root = companion.output / RETENTION
    for index in range(9):
        artifact(companion.output, STAGE, f"final-{index}.png", bytes([index]) * 4)
        call(companion.routes, "POST", DELETE_ROUTE, {"artifacts": [locator(f"final-{index}.png")]})
        assert len(groups(companion.output)) <= 3

    surviving = groups(companion.output)
    assert len(surviving) == 3
    # The just-written group is never the one pruned, even though the limit was already reached.
    assert (retention_root / surviving[-1] / STAGE / "final-8.png").is_file()

    report = call(companion.routes, "GET", RETENTION_ROUTE)
    assert report["retained_generations"] == 3
    assert report["retained_files"] == 3
    assert report["retained_bytes"] == 12


def test_invalid_value_falls_back_to_off_and_is_reported(companion, monkeypatch):
    """A typo in a debug switch is reported, never raised, and never keeps files."""

    monkeypatch.setenv("CIF_RETAIN_STAGES", "everything")
    artifact(companion.output, STAGE, "final.png")

    body = call(companion.routes, "POST", DELETE_ROUTE, {"artifacts": [locator("final.png")]})
    assert body == {
        "deleted": 1,
        "missing": 0,
        "retained": 0,
        "retention": "off",
        "invalid_value": "everything",
    }
    report = call(companion.routes, "GET", RETENTION_ROUTE)
    assert report["mode"] == "off" and report["limit"] == 0
    assert report["invalid_value"] == "everything"
    assert not (companion.output / RETENTION).exists()


@pytest.mark.parametrize(
    ("value", "mode", "limit", "invalid"),
    [
        ("off", "off", 0, None),
        ("OFF", "off", 0, None),
        ("", "off", 0, None),
        ("last", "last", 1, None),
        # An operator padding the value with spaces is still using the switch, not typo-ing it.
        ("  last  ", "last", 1, None),
        ("1", "1", 1, None),
        ("02", "2", 2, None),
        ("32", "32", 32, None),
        # Anything else is out of range or not a number, so cleanup keeps deleting.
        ("33", "off", 0, "33"),
        ("0", "off", 0, None),
        ("-1", "off", 0, "-1"),
        ("1.0", "off", 0, "1.0"),
        ("lastest", "off", 0, "lastest"),
    ],
)
def test_switch_values_normalise(companion, monkeypatch, value, mode, limit, invalid):
    monkeypatch.setenv("CIF_RETAIN_STAGES", value)
    report = call(companion.routes, "GET", RETENTION_ROUTE)
    assert report["mode"] == mode
    assert report["limit"] == limit
    assert ("invalid_value" in report) == (invalid is not None)
    if invalid is not None:
        assert report["invalid_value"] == invalid


# --- bounds and safety ---------------------------------------------------------------------


def test_both_storage_roots_share_one_generation_name(companion, monkeypatch):
    """One request is one generation, so both storage roots show the same group name."""

    monkeypatch.setenv("CIF_RETAIN_STAGES", "last")
    artifact(companion.output, STAGE, "final.png")
    artifact(companion.temp, OTHER_STAGE, "preview.png")

    body = call(
        companion.routes,
        "POST",
        DELETE_ROUTE,
        {
            "artifacts": [
                locator("final.png"),
                locator("preview.png", OTHER_STAGE, "temp"),
            ]
        },
    )
    assert body["retained"] == 2 and body["deleted"] == 0
    assert groups(companion.output) == groups(companion.temp)
    group = groups(companion.output)[0]
    assert (companion.temp / RETENTION / group / OTHER_STAGE / "preview.png").is_file()
    # Each root is bounded on its own, so neither can grow past the limit.
    assert len(groups(companion.temp)) == 1


def test_symlinked_group_is_never_followed_out_of_the_root(companion, monkeypatch, tmp_path):
    """Count-bounded must not mean free to delete somebody's other volume."""

    monkeypatch.setenv("CIF_RETAIN_STAGES", "1")
    outside = tmp_path / "outside"
    (outside / STAGE).mkdir(parents=True)
    (outside / STAGE / "precious.png").write_bytes(b"keep me")
    retention_root = companion.output / RETENTION
    retention_root.mkdir(parents=True, exist_ok=True)
    (retention_root / "20200101T000000Z-aaaaaaaa").symlink_to(outside, target_is_directory=True)

    artifact(companion.output, STAGE, "final.png")
    call(companion.routes, "POST", DELETE_ROUTE, {"artifacts": [locator("final.png")]})

    assert (outside / STAGE / "precious.png").read_bytes() == b"keep me"


def test_retention_root_symlinked_outside_is_refused_and_deletes(companion, monkeypatch, tmp_path):
    """A retention root that escapes the storage root is refused, and cleanup still happens."""

    monkeypatch.setenv("CIF_RETAIN_STAGES", "last")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (companion.output / RETENTION).symlink_to(elsewhere, target_is_directory=True)
    artifact(companion.output, STAGE, "final.png")

    body = call(companion.routes, "POST", DELETE_ROUTE, {"artifacts": [locator("final.png")]})
    assert body == {"deleted": 1, "missing": 0, "retained": 0, "retention": "last"}
    assert list(elsewhere.iterdir()) == []


def test_only_generation_names_are_pruned(companion, monkeypatch):
    """Foreign directories in the retention root are never pruned, only generation names."""

    monkeypatch.setenv("CIF_RETAIN_STAGES", "1")
    retention_root = companion.output / RETENTION
    handbuilt = retention_root / "notes-before-debugging"
    (handbuilt / "sub").mkdir(parents=True)
    (handbuilt / "sub" / "note.txt").write_text("do not delete me")

    for index in range(2):
        artifact(companion.output, STAGE, f"final-{index}.png")
        call(companion.routes, "POST", DELETE_ROUTE, {"artifacts": [locator(f"final-{index}.png")]})

    assert (handbuilt / "sub" / "note.txt").read_text() == "do not delete me"
    # One generation directory plus the operator's own folder.
    assert groups(companion.output) == [companion.ids.names[-1]]


def test_unsafe_locators_are_still_rejected(companion, monkeypatch):
    """Retention must not widen what the destructive route accepts: same locator validation."""

    monkeypatch.setenv("CIF_RETAIN_STAGES", "last")
    artifact(companion.output, STAGE, "final.png")

    for payload in (
        {"artifacts": [locator("../escape.png")]},
        {"artifacts": [locator("final.png", "/absolute/stage")]},
        {"artifacts": [locator("final.png", STAGE, "input")]},
    ):
        with pytest.raises(_BadRequest):
            call(companion.routes, "POST", DELETE_ROUTE, payload)
    assert (companion.output / STAGE / "final.png").is_file()


def test_oversized_request_is_still_refused(companion, monkeypatch):
    monkeypatch.setenv("CIF_RETAIN_STAGES", "last")
    body = json.dumps({"artifacts": []}).encode()
    # The budget is on the declared length, so the route never reads an oversized body.
    request = types.SimpleNamespace(content_length=2 * 1024 * 1024, content=_Reader(body))
    with pytest.raises(_TooLarge):
        asyncio.run(companion.routes.handlers[("POST", DELETE_ROUTE)](request))
