from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from ..dependencies import (
    AuthContext,
    get_container,
    get_db,
    require_ready_csrf,
    require_ready_user,
)
from ..errors import AppError
from ..schemas import SpeechToTextStatus, TranscriptionResponse

router = APIRouter(prefix="/api/speech-to-text", tags=["speech-to-text"])


@router.get("/status", response_model=SpeechToTextStatus)
def status(
    request: Request,
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> SpeechToTextStatus:
    available, message = get_container(request).speech_to_text.status()
    return SpeechToTextStatus(available=available, message=message)


@router.post("/transcriptions", response_model=TranscriptionResponse)
async def transcribe(
    request: Request,
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_db)],
    _: Annotated[AuthContext, Depends(require_ready_csrf)],
) -> TranscriptionResponse:
    content_type = (file.content_type or "").split(";", 1)[0].strip().casefold()
    if not (content_type.startswith("audio/") or content_type == "video/webm"):
        raise AppError(
            "speech_audio_invalid",
            "Only browser-recorded audio is accepted.",
            status_code=415,
        )
    container = get_container(request)
    maximum = container.settings.speech_to_text_max_bytes

    # Authentication is complete and this route performs no database writes.
    # Do not retain a pooled connection while reading or transcribing the upload.
    session.close()
    audio = await file.read(maximum + 1)
    if not audio:
        raise AppError("speech_audio_empty", "The recording was empty.", status_code=422)
    if len(audio) > maximum:
        raise AppError(
            "speech_audio_too_large",
            "The recording is too large to transcribe.",
            status_code=413,
            details={"maximum_bytes": maximum},
        )
    result = await container.speech_to_text.transcribe(
        audio=audio,
        filename=file.filename or "recording",
        content_type=content_type,
    )
    return TranscriptionResponse(text=result.text)
