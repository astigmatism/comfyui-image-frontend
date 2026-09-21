"""Translate a deletion between metadata lookup and streaming into a missing resource."""

from starlette.responses import FileResponse
from starlette.types import Message, Receive, Scope, Send

from .errors import AppError


class StoredFileResponse(FileResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def track_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await super().__call__(scope, receive, track_send)
        except (FileNotFoundError, RuntimeError) as error:
            missing = isinstance(error, FileNotFoundError) or isinstance(
                error.__context__, FileNotFoundError
            )
            if started or not missing:
                raise
            raise AppError("not_found", "Stored asset is unavailable.", status_code=404) from error
