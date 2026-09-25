"""End-to-end validation of the checkpoint batch ETA feature.

test_checkpoint_batch_eta.py exercises the estimator and the worker within a
single application process. These tests close the remaining lifecycle gaps:

* a four-member checkpoint batch is observed through the same SSE event
  channel the browser subscribes to, and the confidence ladder climbs from
  "no evidence at all" (first member, fresh database) through
  batch/low to batch/medium as comparable samples accumulate;
* a real application restart mid-batch: app #1 is stopped while a member is
  still running in ComfyUI (the fake keeps executing it on its own loop),
  app #2 boots on the same database and the same fake ComfyUI, recovers the
  in-flight member, and the remaining members must be estimated from
  database evidence alone because the fresh process has no in-memory state.

The SSE assertions run against a real uvicorn server instead of TestClient:
starlette's TestClient transport blocks until the ASGI app's coroutine
returns, and a long-lived SSE stream only returns on client disconnect,
which TestClient never reports. A local HTTP server's connections can be
ended deterministically, and every generation event is durable, so a live
drain plus a durable-replay drain from the last observed event id cover the
whole batch without racing the worker.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import uvicorn
from app.config import Settings
from app.main import create_app
from app.models import Generation, GenerationStatus
from fastapi.testclient import TestClient
from tests.conftest import csrf, login
from tests.helpers import USER_PASSWORD, provision_user, wait_for_status
from tests.publication_fixtures import build_publication_bundle

MOODY_DISPLAY_NAME = "Moody Krea 2 Mix V4"


def _post(client, path, payload):
    response = client.post(
        path, headers={"X-CSRF-Token": csrf(client), "Idempotency-Key": str(uuid4())}, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def _moody_profile(client, *, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/api/workflows")
        assert response.status_code == 200, response.text
        match = next(
            (
                item
                for item in response.json()
                if item["display_name"] == MOODY_DISPLAY_NAME and item["available"] is True
            ),
            None,
        )
        if match is not None:
            return match
        time.sleep(0.02)
    raise AssertionError("Moody checkpoint source did not become ready")


def _moody_payload(client, prompt: str, **extra_parameters: object) -> dict[str, object]:
    profile = _moody_profile(client)
    parameters: dict[str, object] = {
        "prompt": prompt,
        "seed": 1234,
        "width": 512,
        "height": 512,
    }
    parameters.update(extra_parameters)
    return {
        "source_key": profile["source_key"],
        "revision": profile["revision"],
        "parameters": parameters,
    }


def _started_at(client, generation_id: str) -> datetime:
    with client.app.state.container.db.session_factory() as session:
        generation = session.get(Generation, generation_id)
        assert generation is not None and generation.started_at is not None
        started_at = generation.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return started_at


def _wait_eta(
    client, generation_id: str, basis: str, *, timeout: float = 15.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_eta: dict[str, object] | None = None
    while time.monotonic() < deadline:
        detail = client.get(f"/api/generations/{generation_id}").json()
        progress = detail.get("progress") or {}
        eta = progress.get("eta") or {}
        last_eta = dict(eta) if eta else None
        if eta.get("basis") == basis:
            return detail
        time.sleep(0.03)
    raise AssertionError(f"eta basis {basis!r} never observed; last_eta={last_eta}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _LiveAppServer:
    """Serve the real app over local HTTP in a daemon thread.

    Mirrors LiveFakeServer: a real uvicorn server whose connections the test
    can end deterministically. The graceful-shutdown bound is the app's own
    configured limit, so a lingering SSE subscription can never hold the
    shutdown open past it.
    """

    def __init__(self, app, settings: Settings) -> None:
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                access_log=False,
                timeout_graceful_shutdown=settings.graceful_shutdown_timeout_seconds,
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> _LiveAppServer:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("app server failed to start")
            if time.monotonic() > deadline:
                raise RuntimeError("app server did not become ready")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=15)
        if self._thread.is_alive():
            raise RuntimeError("app server thread did not stop within the bounded window")


def _parse_sse_frame(frame: str) -> tuple[int | None, dict[str, object] | None]:
    """Return (durable event id, parsed payload) for one SSE frame."""
    event_id: int | None = None
    data_lines: list[str] = []
    for line in frame.splitlines():
        if line.startswith("id:"):
            with contextlib.suppress(ValueError):
                event_id = int(line[3:].strip())
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        return event_id, None  # keep-alive-only frame
    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return event_id, None
    return event_id, payload


def _read_sse_until_quiet(
    client: httpx.Client,
    last_event_id: dict[str, int],
    sink: list[dict[str, object]],
) -> None:
    """Drain one /api/events subscription until the stream goes quiet.

    The short read timeout is the bounded lifecycle: an idle stream emits no
    data, so the read gives up, the single-use stream iterator is abandoned
    cleanly, and the connection is released on the reader's own thread. Every
    generation event is durable, so a follow-up drain resuming at
    last_event_id["value"] covers anything emitted between phases without
    racing the worker.
    """
    parameters: dict[str, int] = {}
    if last_event_id["value"] > 0:
        parameters["last_event_id"] = last_event_id["value"]
    try:
        with client.stream(
            "GET",
            "/api/events",
            params=parameters,
            timeout=httpx.Timeout(10.0, read=1.0),
        ) as response:
            if response.status_code != 200:
                sink.append({"error": f"unexpected status {response.status_code}"})
                return
            buffer = b""
            for chunk in response.iter_raw():
                buffer += chunk
                while b"\n\n" in buffer:
                    raw_frame, buffer = buffer.split(b"\n\n", 1)
                    frame_id, payload = _parse_sse_frame(raw_frame.decode("utf-8", "replace"))
                    if payload is None:
                        continue
                    sink.append(payload)
                    if frame_id is not None and frame_id > last_event_id["value"]:
                        last_event_id["value"] = frame_id
    except httpx.ReadTimeout:
        return  # idle stream: this phase is complete
    except httpx.HTTPError as exc:
        sink.append({"error": repr(exc)})


def _progress_events(
    events: list[dict[str, object]], generation_id: str
) -> list[tuple[dict[str, object], dict[str, object] | None]]:
    """(snapshot, eta) pairs from the captured channel for one generation."""
    pairs: list[tuple[dict[str, object], dict[str, object] | None]] = []
    for event in events:
        if event.get("type") != "generation.progress":
            continue
        if event.get("generation_id") != generation_id:
            continue
        payload = event.get("payload")
        snapshot = payload.get("progress") if isinstance(payload, dict) else None
        if not isinstance(snapshot, dict):
            continue
        eta = snapshot.get("eta")
        pairs.append((snapshot, dict(eta) if isinstance(eta, dict) else None))
    return pairs


def test_live_eta_ladder_arrives_over_the_sse_event_channel(fake_state, settings_factory) -> None:
    """The browser's event channel carries the whole confidence ladder live.

    A four-member checkpoint batch on a fresh database: the first member has
    no evidence at all (no ETA), each following member estimates from the
    completed siblings, and once three siblings have finished the estimate
    gains confidence as consistent samples accumulate. The app runs on a real local uvicorn server
    (see module docstring); the live drain plus the durable-replay drain both
    go through the same /api/events endpoint the browser subscribes to, and
    every phase has an explicitly bounded lifecycle.
    """
    fake_state.workflow_files = dict(build_publication_bundle("moody").files)
    fake_state.stage_delay_overrides = {
        "sse ladder alpha": 0.4,
        "sse ladder bravo": 0.4,
        "sse ladder gamma": 0.4,
        "sse ladder delta": 0.4,
    }
    settings = settings_factory(enable_background_worker=True)
    server = _LiveAppServer(create_app(settings), settings).start()
    try:
        with httpx.Client(base_url=server.base_url, timeout=httpx.Timeout(10.0)) as client:
            provision_user(client, username="eta.sse.e2e")
            prompts = [
                "sse ladder alpha",
                "sse ladder bravo",
                "sse ladder gamma",
                "sse ladder delta",
            ]
            batch = _post(
                client,
                "/api/generations/batch",
                {"items": [_moody_payload(client, prompt) for prompt in prompts]},
            )
            ids = [item["generation"]["id"] for item in batch["items"]]

            # Subscribe the way the browser does. The live drain ends on the
            # first idle second; the durable replay that follows resumes at
            # the last observed event id, so events committed before or
            # between drains are never lost.
            events: list[dict[str, object]] = []
            last_event_id: dict[str, int] = {"value": 0}
            consumer = threading.Thread(
                target=_read_sse_until_quiet,
                args=(client, last_event_id, events),
                daemon=True,
            )
            consumer.start()

            for generation_id in ids:
                wait_for_status(client, generation_id, "succeeded", timeout=30)
            consumer.join(timeout=10)
            assert not consumer.is_alive(), "live SSE consumer did not stop in the bounded window"
            _read_sse_until_quiet(client, last_event_id, events)

        errors = [event for event in events if "error" in event]
        assert not errors, errors

        # First member: progress flowed, but a fresh database has no evidence
        # for it, so no ETA is attached at all.
        first_member = _progress_events(events, ids[0])
        assert first_member, "no generation.progress events observed for the first member"
        assert all(eta is None for _, eta in first_member)

        # Each following member estimates from its completed siblings. Serial
        # execution keeps the completed-sibling set constant during a member's
        # run, so every observed estimate for a member carries one confidence.
        expected_confidence = {ids[1]: "low", ids[2]: "medium", ids[3]: "medium"}
        for generation_id, confidence in expected_confidence.items():
            sibling_etas = [
                eta
                for _, eta in _progress_events(events, generation_id)
                if eta is not None and eta.get("basis") == "batch"
            ]
            assert sibling_etas, f"no batch ETA observed over SSE for {generation_id}"
            observed = {
                str(eta.get("confidence")) for eta in sibling_etas if eta["remaining_seconds"] > 0
            }
            assert observed == {confidence}, (generation_id, sibling_etas)
            for eta in sibling_etas:
                remaining = float(eta["remaining_seconds"])
                assert 0 <= float(eta["lower_seconds"]) <= remaining <= float(eta["upper_seconds"])
                datetime.fromisoformat(str(eta["completion_at"]))
    finally:
        server.stop()


def test_restart_mid_batch_estimates_from_database_evidence(fake_state, settings_factory) -> None:
    """A real application restart mid-batch.

    App #1 submits a three-member checkpoint batch and is stopped gracefully
    while the first member is still running in ComfyUI; the fake keeps
    executing the prompt on its own loop. App #2 boots on the same database
    and the same fake ComfyUI: it must recover the in-flight member and then
    estimate the remaining members from database evidence alone, because the
    fresh process has no in-memory run state.
    """
    fake_state.workflow_files = dict(build_publication_bundle("moody").files)
    fake_state.stage_delay_overrides = {
        "restart e2e alpha": 2.5,  # still running when app #1 stops
        "restart e2e beta": 1.0,
        "restart e2e gamma": 1.0,
    }
    settings1 = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings1)) as client1:
        provision_user(client1, username="eta.restart.e2e")
        prompts = ["restart e2e alpha", "restart e2e beta", "restart e2e gamma"]
        batch = _post(
            client1,
            "/api/generations/batch",
            {"items": [_moody_payload(client1, prompt) for prompt in prompts]},
        )
        ids = [item["generation"]["id"] for item in batch["items"]]
        first_id, second_id, third_id = ids

        wait_for_status(client1, first_id, "running", timeout=30)
        # A graceful stop never touches ComfyUI: the in-flight prompt keeps
        # running and the row stays running with its prompt id, while the
        # remaining members are still queued.
        with client1.app.state.container.db.session_factory() as session:
            first = session.get(Generation, first_id)
            assert first is not None
            assert first.status == GenerationStatus.RUNNING
            assert first.comfyui_prompt_id
            second = session.get(Generation, second_id)
            assert second is not None
            assert second.status == GenerationStatus.QUEUED
            assert not second.comfyui_prompt_id

    # The fake ComfyUI survived the restart with the first prompt still
    # running. Boot a second application on the same data.
    settings2 = settings_factory(
        enable_background_worker=True,
        data_dir=settings1.data_dir,
        database_path=settings1.database_path,
    )
    with TestClient(create_app(settings2)) as client2:
        login(client2, "eta.restart.e2e", USER_PASSWORD)

        # Recovery: the in-flight member is re-attached and completes over its
        # original fake timeline (it was started in app #1).
        wait_for_status(client2, first_id, "succeeded", timeout=30)
        first = client2.get(f"/api/generations/{first_id}").json()
        first_duration = float(first["generation_duration_seconds"])
        assert first_duration > 2.0

        # The remaining member is dispatched by the fresh worker, which has
        # no in-memory state: its ETA must come from the database.
        observed = _wait_eta(client2, second_id, "batch", timeout=30)
        eta = observed["progress"]["eta"]
        assert eta["confidence"] == "low"
        started_at = _started_at(client2, second_id)
        updated_at = datetime.fromisoformat(str(eta["updated_at"]))
        elapsed = max(0.0, (updated_at.astimezone(UTC) - started_at).total_seconds())
        remaining = float(eta["remaining_seconds"])
        assert 0 < remaining <= first_duration + 0.05
        assert remaining == pytest.approx(first_duration - elapsed, abs=0.2)
        assert float(eta["lower_seconds"]) <= remaining <= float(eta["upper_seconds"])

        wait_for_status(client2, second_id, "succeeded", timeout=30)
        wait_for_status(client2, third_id, "succeeded", timeout=30)
