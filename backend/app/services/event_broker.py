from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any


class EventBroker:
    """In-process fan-out; GenerationEvent rows remain the durable source of truth."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, owner_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(owner_id, set()))
        for queue in queues:
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, owner_id: str) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers[owner_id].add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers[owner_id].discard(queue)
                if not self._subscribers[owner_id]:
                    self._subscribers.pop(owner_id, None)
