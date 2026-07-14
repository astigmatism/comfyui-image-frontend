from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

import httpx

from ..config import Settings
from ..errors import AppError


@dataclass(frozen=True)
class TranscriptionResult:
    text: str


class SpeechToTextAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.url = settings.speech_to_text_url
        self.api_key = settings.speech_to_text_api_key
        self.model = settings.speech_to_text_model
        self._client = (
            httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=settings.speech_to_text_timeout_seconds,
                    write=settings.speech_to_text_timeout_seconds,
                    pool=5.0,
                ),
                transport=transport,
            )
            if self.url and self.api_key and self.api_key.get_secret_value()
            else None
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    def status(self) -> tuple[bool, str | None]:
        if not self._client:
            return False, "Voice input is not configured."
        return True, None

    async def transcribe(
        self,
        *,
        audio: bytes,
        filename: str,
        content_type: str,
    ) -> TranscriptionResult:
        if not self._client or not self.url or not self.api_key:
            raise AppError(
                "speech_to_text_unavailable",
                "Voice input is not configured.",
                status_code=503,
            )
        safe_filename = PurePath(filename or "recording").name[:255] or "recording"
        try:
            response = await self._client.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key.get_secret_value()}",
                    "Accept": "application/json",
                },
                data={"model": self.model, "response_format": "json"},
                files={"file": (safe_filename, audio, content_type)},
            )
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                "speech_to_text_unavailable",
                "The speech-to-text service could not transcribe this recording.",
                status_code=503,
            ) from exc
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise AppError(
                "speech_to_text_invalid_response",
                "The speech-to-text service returned an invalid response.",
                status_code=502,
            )
        normalized = text.strip()
        if not normalized:
            raise AppError(
                "speech_not_recognized",
                "No speech was recognized in the recording.",
                status_code=422,
            )
        return TranscriptionResult(text=normalized)
