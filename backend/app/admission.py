"""FIFO admission before dependencies, with capacity reserved for non-media APIs."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .blocking import request_work


@dataclass(eq=False)
class Ticket:
    media: bool
    future: asyncio.Future[None]
    admitted: bool = False
    released: bool = False


class Admission:
    def __init__(
        self,
        active_limit: int = 8,
        media_limit: int = 4,
        queue_limit: int = 64,
        wait_seconds: float = 5,
    ):
        self.active_limit, self.media_limit = active_limit, media_limit
        self.queue_limit, self.wait_seconds = queue_limit, wait_seconds
        self.active = self.media = self.rejected = 0
        self.waiters: deque[Ticket] = deque()
        self.total_wait_ms = 0.0
        self.accepted = 0
        self.closed = False

    def drain(self) -> None:
        if self.closed:
            return
        for ticket in list(self.waiters):
            if self.active >= self.active_limit:
                break
            if ticket.media and self.media >= self.media_limit:
                continue
            self.waiters.remove(ticket)
            ticket.admitted = True
            self.active += 1
            self.media += int(ticket.media)
            self.accepted += 1
            ticket.future.set_result(None)

    async def acquire(self, media: bool) -> Ticket:
        if self.closed or len(self.waiters) >= self.queue_limit:
            self.rejected += 1
            raise TimeoutError
        ticket = Ticket(media, asyncio.get_running_loop().create_future())
        started = time.monotonic()
        self.waiters.append(ticket)
        self.drain()
        try:
            await asyncio.wait_for(asyncio.shield(ticket.future), self.wait_seconds)
            return ticket
        except BaseException:
            self.release(ticket)
            self.rejected += 1
            raise
        finally:
            self.total_wait_ms += (time.monotonic() - started) * 1000

    def release(self, ticket: Ticket) -> None:
        if ticket.released:
            return
        ticket.released = True
        if ticket in self.waiters:
            self.waiters.remove(ticket)
        if ticket.admitted:
            self.active -= 1
            self.media -= int(ticket.media)
        self.drain()

    def snapshot(self) -> dict[str, int | float]:
        return {
            "active": self.active,
            "media": self.media,
            "waiting": len(self.waiters),
            "rejected": self.rejected,
            "accepted": self.accepted,
            "queue_wait_ms": round(self.total_wait_ms),
        }

    def close(self) -> None:
        self.closed = True
        for ticket in list(self.waiters):
            ticket.future.set_exception(TimeoutError())
            self.release(ticket)


class AdmissionMiddleware:
    def __init__(self, app: ASGIApp, admission: Admission):
        self.app, self.admission = app, admission

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path == "/api/health":
            await self.app(scope, receive, send)
            return
        media = path.startswith(("/api/artifacts/", "/api/uploads/")) and scope["method"] in {
            "GET",
            "HEAD",
        }
        token = request_work.set(True)
        buffered: deque[Message] = deque()

        async def watch_disconnect() -> bool:
            # Buffer only the initial ASGI body chunk. Large uploads retain server
            # backpressure while queued; ordinary GET/JSON requests can observe a
            # disconnect immediately after their final body chunk.
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.disconnect":
                return True
            if message.get("more_body", False):
                return False
            message = await receive()
            buffered.append(message)
            return bool(message["type"] == "http.disconnect")

        async def buffered_receive() -> Message:
            return buffered.popleft() if buffered else await receive()

        acquiring = asyncio.create_task(self.admission.acquire(media))
        watching = asyncio.create_task(watch_disconnect())
        ticket: Ticket | None = None
        try:
            try:
                done, _ = await asyncio.wait(
                    (acquiring, watching), return_when=asyncio.FIRST_COMPLETED
                )
                if watching in done and watching.result():
                    return
                ticket = await acquiring
            except TimeoutError:
                await JSONResponse(
                    {
                        "error": {
                            "code": "service_busy",
                            "message": "The application is busy. Please retry shortly.",
                            "fields": {},
                            "details": {},
                            "request_id": scope.get("state", {}).get("request_id"),
                        }
                    },
                    status_code=503,
                    headers={"Retry-After": "1"},
                )(scope, buffered_receive, send)
                return
            finally:
                watching.cancel()
                await asyncio.gather(watching, return_exceptions=True)

            async def admitted_send(message: Message) -> None:
                # Function-scoped DB dependencies have closed before headers.
                if message["type"] == "http.response.start":
                    self.admission.release(ticket)
                await send(message)

            try:
                await self.app(scope, buffered_receive, admitted_send)
            finally:
                self.admission.release(ticket)
        finally:
            acquiring.cancel()
            watching.cancel()
            await asyncio.gather(acquiring, watching, return_exceptions=True)
            if not acquiring.cancelled() and acquiring.exception() is None:
                self.admission.release(acquiring.result())
            request_work.reset(token)
