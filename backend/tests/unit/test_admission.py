import asyncio
import threading

import pytest
from app.admission import Admission, AdmissionMiddleware
from app.blocking import run_blocking


async def test_fifo_reserved_capacity_timeout_and_cancel():
    admission = Admission(active_limit=3, media_limit=1, queue_limit=2, wait_seconds=0.05)
    media = await admission.acquire(True)
    queued_media = asyncio.create_task(admission.acquire(True))
    await asyncio.sleep(0)
    ordinary = await admission.acquire(False)
    assert admission.active == 2
    queued_media.cancel()
    await asyncio.gather(queued_media, return_exceptions=True)
    assert not admission.waiters
    waiting = asyncio.create_task(admission.acquire(True))
    with pytest.raises(TimeoutError):
        await waiting
    admission.release(media)
    admission.release(ordinary)
    assert admission.active == admission.media == len(admission.waiters) == 0


async def test_bounded_queue_shutdown_and_exceptions():
    admission = Admission(active_limit=1, queue_limit=1)
    active = await admission.acquire(False)
    waiting = asyncio.create_task(admission.acquire(False))
    await asyncio.sleep(0)
    with pytest.raises(TimeoutError):
        await admission.acquire(False)
    admission.close()
    with pytest.raises(TimeoutError):
        await waiting
    admission.release(active)
    assert admission.active == len(admission.waiters) == 0


async def test_disconnect_removes_waiter_before_authentication():
    admission = Admission(active_limit=1)
    active = await admission.acquire(False)
    entered = False

    async def app(*args):
        nonlocal entered
        entered = True

    messages = asyncio.Queue()
    messages.put_nowait({"type": "http.request", "body": b""})

    async def send(message):
        raise AssertionError("disconnected request must not respond")

    task = asyncio.create_task(
        AdmissionMiddleware(app, admission)(
            {"type": "http", "path": "/api/generations", "method": "GET"},
            messages.get,
            send,
        )
    )
    await asyncio.sleep(0.01)
    assert len(admission.waiters) == 1
    messages.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(task, 1)
    assert not entered
    assert not admission.waiters
    admission.release(active)


async def test_cancelled_transaction_keeps_background_slot_until_finished():
    started = threading.Event()
    release = threading.Event()

    def transaction():
        started.set()
        assert release.wait(2)

    tasks = [asyncio.create_task(run_blocking(transaction)) for _ in range(4)]
    assert await asyncio.to_thread(started.wait, 1)
    await asyncio.sleep(0.02)
    for task in tasks:
        task.cancel()
    fifth = threading.Event()
    next_task = asyncio.create_task(run_blocking(fifth.set))
    await asyncio.sleep(0.02)
    assert not fifth.is_set()
    assert all(not task.done() for task in tasks)
    release.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.wait_for(next_task, 1)
    assert fifth.is_set()


async def test_sustained_overload_is_bounded_and_returns_retryable_responses():
    admission = Admission(active_limit=8, media_limit=4, queue_limit=64, wait_seconds=0.03)
    active = [await admission.acquire(index < 4) for index in range(8)]
    for _ in range(3):
        waiting = [asyncio.create_task(admission.acquire(True)) for _ in range(100)]
        await asyncio.sleep(0.01)
        assert len(admission.waiters) <= 64
        assert admission.active == 8 and admission.media == 4
        results = await asyncio.gather(*waiting, return_exceptions=True)
        assert all(isinstance(result, TimeoutError) for result in results)
        assert not admission.waiters
    sent = []

    async def app(*args):
        raise AssertionError("overloaded request reached handler")

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await AdmissionMiddleware(app, admission)(
        {"type": "http", "path": "/api/generations", "method": "GET"},
        receive,
        send,
    )
    assert sent[0]["status"] == 503
    assert (b"retry-after", b"1") in sent[0]["headers"]
    assert b"service_busy" in sent[1]["body"]
    for ticket in active:
        admission.release(ticket)
    assert admission.active == 0


async def test_oldest_eligible_waiter_gets_each_released_slot():
    admission = Admission(active_limit=2, media_limit=1)
    media = await admission.acquire(True)
    ordinary = await admission.acquire(False)
    first_media = asyncio.create_task(admission.acquire(True))
    next_ordinary = asyncio.create_task(admission.acquire(False))
    last_media = asyncio.create_task(admission.acquire(True))
    await asyncio.sleep(0)
    admission.release(ordinary)
    ordinary = await next_ordinary
    assert not first_media.done() and not last_media.done()
    admission.release(media)
    media = await first_media
    assert not last_media.done()
    admission.release(media)
    media = await last_media
    admission.release(media)
    admission.release(ordinary)
    assert admission.active == admission.media == 0


async def test_stream_releases_admission_at_headers_and_cancellation_is_idempotent():
    admission = Admission(active_limit=1)
    headers_sent = asyncio.Event()

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        headers_sent.set()
        await asyncio.Event().wait()

    async def receive():
        await asyncio.Event().wait()

    async def send(_message):
        pass

    stream = asyncio.create_task(
        AdmissionMiddleware(app, admission)(
            {"type": "http", "path": "/api/events", "method": "GET"},
            receive,
            send,
        )
    )
    await headers_sent.wait()
    permit = await admission.acquire(False)
    stream.cancel()
    await asyncio.gather(stream, return_exceptions=True)
    assert admission.active == 1
    admission.release(permit)
    assert admission.active == 0
