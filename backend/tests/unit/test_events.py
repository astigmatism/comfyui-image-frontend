from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
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
