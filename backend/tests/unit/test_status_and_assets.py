from __future__ import annotations

import asyncio
import threading
from io import BytesIO

import pytest
from app.domain.results import NativeFileOutput
from app.domain.status import assert_transition
from app.errors import AppError
from app.models import GenerationStatus
from app.services.assets import AssetStore
from app.services.queue_worker import QueueWorker
from tests.fake_services import make_png


def test_status_model_allows_declared_transitions_and_rejects_terminal_mutation() -> None:
    assert_transition(GenerationStatus.QUEUED, GenerationStatus.DISPATCHING)
    assert_transition(GenerationStatus.RUNNING, GenerationStatus.CANCEL_REQUESTED)
    assert_transition(GenerationStatus.CANCEL_REQUESTED, GenerationStatus.SUCCEEDED)
    with pytest.raises(AppError) as exc:
        assert_transition(GenerationStatus.SUCCEEDED, GenerationStatus.RUNNING)
    assert exc.value.code == "invalid_status_transition"


def test_asset_store_checks_images_pixels_and_path_traversal(settings_factory) -> None:
    settings = settings_factory(upload_max_pixels=10_000, upload_max_bytes=100_000)
    store = AssetStore(settings)
    saved = store.store_upload(BytesIO(make_png("upload", width=80, height=60)), kind="image")
    assert saved.mime_type == "image/png"
    assert saved.width == 80 and saved.height == 60
    assert store.open(saved.relative_path).is_file()

    with pytest.raises(AppError) as invalid:
        store.store_upload(BytesIO(b"not an image"), kind="image")
    assert invalid.value.code == "upload_invalid"

    with pytest.raises(AppError) as traversal:
        store.open("../outside.txt")
    assert traversal.value.code == "unsafe_path"


async def test_cancelled_asset_storage_finishes_and_removes_unowned_files(
    settings_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_factory(upload_max_pixels=10_000, upload_max_bytes=100_000)
    store = AssetStore(settings)
    original_atomic_write = store._atomic_write
    write_started = threading.Event()
    release_write = threading.Event()
    first_write = True

    def blocking_atomic_write(relative_path: str, content: bytes) -> None:
        nonlocal first_write
        if first_write:
            first_write = False
            write_started.set()
            if not release_write.wait(timeout=5):
                raise TimeoutError("test did not release durable write")
        original_atomic_write(relative_path, content)

    monkeypatch.setattr(store, "_atomic_write", blocking_atomic_write)
    storage = asyncio.create_task(
        store.store_artifact_async(
            make_png("cancelled", width=80, height=60),
            generation_id="cancelled-generation",
        )
    )
    assert await asyncio.wait_for(asyncio.to_thread(write_started.wait, 1), timeout=2)

    storage.cancel()
    release_write.set()
    with pytest.raises(asyncio.CancelledError):
        await storage

    assert not [path for path in settings.assets_dir.rglob("*") if path.is_file()]


def test_thumbnail_failure_removes_the_previously_written_original(
    settings_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_factory(upload_max_pixels=10_000, upload_max_bytes=100_000)
    store = AssetStore(settings)

    def fail_thumbnail(*_args: object, **_kwargs: object) -> str:
        raise OSError("simulated thumbnail failure")

    monkeypatch.setattr(store, "_thumbnail", fail_thumbnail)
    with pytest.raises(OSError, match="simulated thumbnail failure"):
        store.store_artifact(
            make_png("thumbnail failure", width=80, height=60),
            generation_id="failed-generation",
        )

    assert not [path for path in settings.assets_dir.rglob("*") if path.is_file()]


async def test_artifact_metadata_insert_failure_removes_unowned_files(settings_factory) -> None:
    settings = settings_factory(upload_max_pixels=10_000, upload_max_bytes=100_000)
    store = AssetStore(settings)
    recorded_failures: list[Exception] = []

    class SessionScope:
        def __enter__(self) -> SessionScope:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def scalar(self, _statement: object) -> None:
            return None

    class ComfyUI:
        async def retrieve_artifact(self, _reference: object) -> bytes:
            return make_png("database failure", width=80, height=60)

    def fail_insert(**_kwargs: object) -> None:
        raise RuntimeError("simulated database insert failure")

    async def record_failure(
        _generation_id: str,
        _file_output: NativeFileOutput,
        exc: Exception,
    ) -> None:
        recorded_failures.append(exc)

    worker = object.__new__(QueueWorker)
    worker.session_factory = SessionScope  # type: ignore[assignment]
    worker.comfyui = ComfyUI()  # type: ignore[assignment]
    worker.assets = store
    worker._insert_artifact = fail_insert  # type: ignore[method-assign,assignment]
    worker._record_persistence_failure = record_failure  # type: ignore[method-assign,assignment]
    output = NativeFileOutput(
        node_id="900",
        output_id="final",
        role="final",
        kind="image",
        batch_index=0,
        reference={"filename": "result.png", "subfolder": "fake", "type": "output"},
        declared=True,
    )

    await worker._persist_native_file("generation-id", output)

    assert len(recorded_failures) == 1
    assert str(recorded_failures[0]) == "simulated database insert failure"
    assert not [path for path in settings.assets_dir.rglob("*") if path.is_file()]


@pytest.mark.parametrize("retained", [False, True])
async def test_cancelled_artifact_insert_waits_for_ownership_before_cleanup(
    settings_factory,
    retained: bool,
) -> None:
    settings = settings_factory(upload_max_pixels=10_000, upload_max_bytes=100_000)
    store = AssetStore(settings)
    insert_started = threading.Event()
    release_insert = threading.Event()

    class SessionScope:
        def __enter__(self) -> SessionScope:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def scalar(self, _statement: object) -> None:
            return None

    class ComfyUI:
        async def retrieve_artifact(self, _reference: object) -> bytes:
            return make_png("cancelled insert", width=80, height=60)

    def blocking_insert(**_kwargs: object) -> tuple[None, bool]:
        insert_started.set()
        if not release_insert.wait(timeout=5):
            raise TimeoutError("test did not release artifact insertion")
        return None, retained

    worker = object.__new__(QueueWorker)
    worker.session_factory = SessionScope  # type: ignore[assignment]
    worker.comfyui = ComfyUI()  # type: ignore[assignment]
    worker.assets = store
    worker._insert_artifact = blocking_insert  # type: ignore[method-assign,assignment]
    output = NativeFileOutput(
        node_id="900",
        output_id="final",
        role="final",
        kind="image",
        batch_index=0,
        reference={"filename": "result.png", "subfolder": "fake", "type": "output"},
        declared=True,
    )

    persistence = asyncio.create_task(worker._persist_native_file("generation-id", output))
    assert await asyncio.wait_for(asyncio.to_thread(insert_started.wait, 1), timeout=2)

    persistence.cancel()
    await asyncio.sleep(0)
    assert not persistence.done()
    release_insert.set()
    with pytest.raises(asyncio.CancelledError):
        await persistence

    stored_files = [path for path in settings.assets_dir.rglob("*") if path.is_file()]
    assert bool(stored_files) is retained


async def test_cancelled_result_normalization_waits_for_thread_completion() -> None:
    normalization_started = threading.Event()
    release_normalization = threading.Event()

    def blocking_normalization(*_args: object, **_kwargs: object) -> None:
        normalization_started.set()
        if not release_normalization.wait(timeout=5):
            raise TimeoutError("test did not release result normalization")

    worker = object.__new__(QueueWorker)
    worker._normalize_generation_history = blocking_normalization  # type: ignore[method-assign]
    finalization = asyncio.create_task(
        worker._finalize("generation-id", history={"outputs": {}}, outcome="success")
    )
    try:
        assert await asyncio.wait_for(
            asyncio.to_thread(normalization_started.wait, 1),
            timeout=2,
        )
        finalization.cancel()
        await asyncio.sleep(0)
        assert not finalization.done()
    finally:
        release_normalization.set()

    with pytest.raises(asyncio.CancelledError):
        await finalization
