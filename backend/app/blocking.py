"""Bounded, cancellation-safe execution of thread-owned database operations."""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)
request_work: ContextVar[bool] = ContextVar("request_database_work", default=False)
_limits: weakref.WeakKeyDictionary[Any, dict[bool, asyncio.Semaphore]] = weakref.WeakKeyDictionary()


async def run_blocking[T](operation: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Do not abandon a transaction or reuse its permit while its thread is running.

    Callers own session construction and closure inside ``operation``. No live
    session may be passed in, and external I/O is awaited outside this operation.
    HTTP admission bounds request waiters; background loops await their own work
    rather than submitting an unbounded executor backlog.
    """
    loop = asyncio.get_running_loop()
    limits = _limits.setdefault(loop, {True: asyncio.Semaphore(8), False: asyncio.Semaphore(4)})
    started = time.monotonic()
    async with limits[request_work.get()]:
        admitted = time.monotonic()
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        try:
            result = task.result()
        finally:
            duration = time.monotonic() - admitted
            if duration > 0.25 or admitted - started > 0.25:
                logger.info(
                    "database_operation_timing",
                    extra={
                        "operation": operation.__name__,
                        "duration_ms": round(duration * 1000),
                        "queue_wait_ms": round((admitted - started) * 1000),
                    },
                )
        if cancelled:
            raise asyncio.CancelledError
        return result
