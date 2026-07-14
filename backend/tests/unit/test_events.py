from __future__ import annotations

import asyncio

from app.api.events import _sse
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
