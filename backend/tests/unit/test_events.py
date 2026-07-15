from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from app.api import events as events_api
from app.api.events import _event_stream, _sse
from app.services.event_broker import EventBroker


def test_sse_serialization_carries_id_type_and_payload() -> None:
    text = _sse(
        {
            "id": 41,
            "type": "artifact.available",
            "generation_id": "g1",
            "payload": {"artifact_id": "a1"},
        }
    )
    assert text.startswith("id: 41\nevent: artifact.available\ndata: ")
    assert '"generation_id":"g1"' in text
    assert text.endswith("\n\n")


def test_event_broker_delivers_only_to_the_subscribed_owner() -> None:
    async def scenario() -> None:
        broker = EventBroker()
        async with (
            broker.subscribe("owner-a") as queue_a,
            broker.subscribe("owner-b") as queue_b,
        ):
            await broker.publish("owner-a", {"type": "generation.running"})
            event = await asyncio.wait_for(queue_a.get(), timeout=0.1)
            assert event["type"] == "generation.running"
            try:
                await asyncio.wait_for(queue_b.get(), timeout=0.02)
            except TimeoutError:
                return
            raise AssertionError("cross-owner event leaked")

    asyncio.run(scenario())


async def test_cancelling_event_stream_releases_broker_subscription() -> None:
    broker = EventBroker()

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    container = SimpleNamespace(broker=broker)
    stream = _event_stream(  # type: ignore[arg-type]
        ConnectedRequest(),  # type: ignore[arg-type]
        container,
        "owner-a",
        "session-token",
        [],
    )
    pending_item = asyncio.create_task(anext(stream))
    for _ in range(10):
        await asyncio.sleep(0)
        if broker._subscribers:
            break

    assert len(broker._subscribers["owner-a"]) == 1
    pending_item.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_item

    assert not broker._subscribers


async def test_stream_subscribes_before_replay_and_deduplicates_by_high_water(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = EventBroker()
    replay_started = threading.Event()
    release_replay = threading.Event()
    replay_event = {"id": 41, "type": "generation.running", "payload": {}}
    live_event = {"id": 42, "type": "generation.progress", "payload": {}}
    ephemeral_event = {"id": None, "type": "generation.deleted", "payload": {}}

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    def load_replay(_container: object, owner_id: str, after_id: int):  # type: ignore[no-untyped-def]
        assert owner_id == "owner-a"
        assert after_id == 40
        replay_started.set()
        if not release_replay.wait(timeout=2):
            raise TimeoutError("test did not release replay query")
        return (replay_event,)

    monkeypatch.setattr(events_api, "_load_replay", load_replay)
    container = SimpleNamespace(broker=broker)
    stream = _event_stream(  # type: ignore[arg-type]
        ConnectedRequest(),  # type: ignore[arg-type]
        container,
        "owner-a",
        "session-token",
        None,
        after_id=40,
    )
    first_item = asyncio.create_task(anext(stream))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(replay_started.wait, 1), timeout=2)
        assert len(broker._subscribers["owner-a"]) == 1

        # Event 41 models a commit observed by both the replay SELECT and the live queue. Event 42
        # and the ephemeral deletion exist only in the queue and must retain publication order.
        await broker.publish("owner-a", replay_event)
        await broker.publish("owner-a", live_event)
        await broker.publish("owner-a", ephemeral_event)
        release_replay.set()

        assert await asyncio.wait_for(first_item, timeout=2) == _sse(replay_event)
        assert await asyncio.wait_for(anext(stream), timeout=2) == _sse(live_event)
        assert await asyncio.wait_for(anext(stream), timeout=2) == _sse(ephemeral_event)
    finally:
        release_replay.set()
        if not first_item.done():
            first_item.cancel()
            await asyncio.gather(first_item, return_exceptions=True)
        await stream.aclose()

    assert not broker._subscribers


async def test_keepalive_revalidation_uses_a_fresh_short_lived_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = EventBroker()
    active_sessions = 0
    session_calls = 0
    session_valid = True

    class SessionScope:
        def __enter__(self) -> object:
            nonlocal active_sessions, session_calls
            active_sessions += 1
            session_calls += 1
            return object()

        def __exit__(self, *_args: object) -> None:
            nonlocal active_sessions
            active_sessions -= 1

    class Auth:
        def resolve_session(
            self, _session: object, _raw_token: str
        ) -> tuple[object, object] | None:
            if not session_valid:
                return None
            return SimpleNamespace(id="owner-a", must_change_password=False), object()

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    container = SimpleNamespace(
        broker=broker,
        db=SimpleNamespace(session_factory=SessionScope),
        auth=Auth(),
    )
    monkeypatch.setattr(events_api, "_KEEPALIVE_SECONDS", 0.01)
    stream = _event_stream(  # type: ignore[arg-type]
        ConnectedRequest(),  # type: ignore[arg-type]
        container,
        "owner-a",
        "session-token",
        [],
    )

    assert await asyncio.wait_for(anext(stream), timeout=0.5) == ": keep-alive\n\n"
    assert session_calls == 1
    assert active_sessions == 0

    session_valid = False
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=0.5)
    assert session_calls == 2
    assert active_sessions == 0
    assert not broker._subscribers


async def test_continuous_events_cannot_postpone_session_revalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = EventBroker()
    valid = True
    validation_calls = 0

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    def validate_session(_container: object, _owner_id: str, _raw_token: str) -> bool:
        nonlocal validation_calls
        validation_calls += 1
        return valid

    container = SimpleNamespace(broker=broker)
    monkeypatch.setattr(events_api, "_KEEPALIVE_SECONDS", 0.02)
    monkeypatch.setattr(events_api, "_session_is_valid", validate_session)
    stream = _event_stream(  # type: ignore[arg-type]
        ConnectedRequest(),  # type: ignore[arg-type]
        container,
        "owner-a",
        "session-token",
        [],
    )

    async def consume() -> None:
        async for _item in stream:
            pass

    async def publish_continuously() -> None:
        sequence = 0
        while True:
            sequence += 1
            await broker.publish(
                "owner-a",
                {"id": sequence, "type": "generation.progress", "payload": {}},
            )
            await asyncio.sleep(0.002)

    consumer = asyncio.create_task(consume())
    for _ in range(100):
        if broker._subscribers:
            break
        await asyncio.sleep(0.001)
    assert broker._subscribers
    producer = asyncio.create_task(publish_continuously())
    try:
        for _ in range(100):
            if validation_calls:
                break
            await asyncio.sleep(0.005)
        assert validation_calls >= 1
        calls_before_revocation = validation_calls
        valid = False
        await asyncio.wait_for(consumer, timeout=0.5)
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        if not consumer.done():
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)

    assert validation_calls > calls_before_revocation
    assert not broker._subscribers
