from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.helpers import provision_user


def test_transcription_proxy_keeps_credentials_server_side(
    app_client: TestClient,
    fake_state,
) -> None:
    provision_user(app_client, username="voice.user")
    status = app_client.get("/api/speech-to-text/status")
    assert status.status_code == 200
    assert status.json() == {"available": True, "message": None}
    assert status.headers["Permissions-Policy"] == ("camera=(), microphone=(self), geolocation=()")

    response = app_client.post(
        "/api/speech-to-text/transcriptions",
        headers={"X-CSRF-Token": csrf(app_client)},
        files={"file": ("recording.webm", b"browser audio", "audio/webm")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "transcribed speech"}
    assert "secret" not in response.text
    assert fake_state.speech_to_text_calls == [
        {
            "authorization": "Bearer test-whisper-secret",
            "filename": "recording.webm",
            "content_type": "audio/webm",
            "content": b"browser audio",
            "model": "whisper-1",
            "response_format": "json",
        }
    ]


def test_transcription_rejects_invalid_empty_and_oversized_audio(
    settings_factory,
) -> None:
    with TestClient(create_app(settings_factory(speech_to_text_max_bytes=1024))) as client:
        provision_user(client, username="voice.validation")
        headers = {"X-CSRF-Token": csrf(client)}

        invalid = client.post(
            "/api/speech-to-text/transcriptions",
            headers=headers,
            files={"file": ("notes.txt", b"not audio", "text/plain")},
        )
        assert invalid.status_code == 415
        assert invalid.json()["error"]["code"] == "speech_audio_invalid"

        empty = client.post(
            "/api/speech-to-text/transcriptions",
            headers=headers,
            files={"file": ("recording.webm", b"", "audio/webm")},
        )
        assert empty.status_code == 422
        assert empty.json()["error"]["code"] == "speech_audio_empty"

        oversized = client.post(
            "/api/speech-to-text/transcriptions",
            headers=headers,
            files={"file": ("recording.webm", b"x" * 1025, "audio/webm")},
        )
        assert oversized.status_code == 413
        assert oversized.json()["error"]["details"] == {"maximum_bytes": 1024}


def test_transcription_outage_and_missing_configuration_are_isolated(
    fake_state,
    settings_factory,
) -> None:
    fake_state.speech_to_text_available = False
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="voice.outage")
        failed = client.post(
            "/api/speech-to-text/transcriptions",
            headers={"X-CSRF-Token": csrf(client)},
            files={"file": ("recording.webm", b"audio", "audio/webm")},
        )
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "speech_to_text_unavailable"
        assert client.get("/api/workflows").status_code == 200

    with TestClient(
        create_app(
            settings_factory(
                speech_to_text_url=None,
                speech_to_text_api_key=None,
            )
        )
    ) as client:
        provision_user(client, username="voice.disabled")
        assert client.get("/api/speech-to-text/status").json() == {
            "available": False,
            "message": "Voice input is not configured.",
        }
        failed = client.post(
            "/api/speech-to-text/transcriptions",
            headers={"X-CSRF-Token": csrf(client)},
            files={"file": ("recording.webm", b"audio", "audio/webm")},
        )
        assert failed.status_code == 503


def test_transcription_requires_csrf(app_client: TestClient) -> None:
    provision_user(app_client, username="voice.csrf")
    response = app_client.post(
        "/api/speech-to-text/transcriptions",
        files={"file": ("recording.webm", b"audio", "audio/webm")},
    )
    assert response.status_code == 403
