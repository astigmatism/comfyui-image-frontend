"""The Prompt Assistant against the fake LLM Router, end to end (docs/llm-router-contract.md).

The fake serves ``/v1/router/capabilities`` and ``/v1/router/events`` and answers ``/api/chat``
with the router's error codes, so these tests switch between paired and solo configurations the
way the AI Runtime does, without notice to the client.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.main import create_app
from fastapi.testclient import TestClient
from tests.conftest import csrf
from tests.helpers import provision_user


def _compose(client: TestClient, direction: str = "a moonlit lake") -> Any:
    return client.post(
        "/api/prompt-assistant/compose",
        headers={"X-CSRF-Token": csrf(client)},
        json={"mode": "create", "prompt": "", "creative_direction": direction},
    )


def _eventually(probe: Callable[[], Any], *, timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    value = probe()
    while not value and time.monotonic() < deadline:
        time.sleep(0.05)
        value = probe()
    return value


def test_compose_follows_configuration_switches_without_sticky_fallback(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="router.switches")

    paired = _compose(app_client)
    assert paired.status_code == 200, paired.text
    assert paired.json()["service"] == "nighttime"
    assert paired.json()["fallback"] is False

    fake_state.router_configuration = "solo"
    solo = _compose(app_client, "a moonlit lake at dawn")
    assert solo.status_code == 200, solo.text
    assert solo.json()["service"] == "daytime"
    assert solo.json()["fallback"] is True

    fake_state.router_configuration = "paired"
    restored = _compose(app_client, "a moonlit lake at dusk")
    assert restored.status_code == 200, restored.text
    assert restored.json()["service"] == "nighttime"

    assert [call["model"] for call in fake_state.ollama_calls] == [
        "nighttime",
        "daytime",
        "nighttime",
    ]
    chat_requests = [item for item in fake_state.router_requests if item["path"] == "/api/chat"]
    assert chat_requests
    assert all(
        item["client_name"] == "comfyui-image-frontend" for item in fake_state.router_requests
    )
    assert not any(item["path"] == "/api/tags" for item in fake_state.router_requests)


def test_a_declining_fallback_is_reported_plainly_and_recorded(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="router.declined")
    fake_state.router_configuration = "solo"
    fake_state.router_declining_services = {"daytime"}

    response = _compose(app_client, "an explicit scene")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "ollama_model_declined"
    assert error["message"].startswith("No NSFW model is available; Daytime declined this request.")
    assert error["details"]["router"]["service"] == "daytime"
    assert len(fake_state.ollama_calls) == 1  # never retried in a loop

    from app.models import PromptAssistantRun
    from sqlalchemy import select

    with app_client.app.state.container.db.session_factory() as session:
        run = session.scalar(
            select(PromptAssistantRun).where(
                PromptAssistantRun.error_code == "ollama_model_declined"
            )
        )
        assert run is not None
        assert run.raw_response_json["error_details"]["router"]["fallback"] is True


def test_draining_router_holds_the_request_until_the_switch_finishes(
    app_client: TestClient, fake_state
) -> None:
    provision_user(app_client, username="router.draining")
    fake_state.router_draining = True
    waits: list[float] = []

    async def waiter(seconds: float) -> None:
        waits.append(seconds)
        fake_state.router_draining = False
        fake_state.router_configuration = "solo"

    app_client.app.state.container.ollama.router_waiter = waiter
    response = _compose(app_client)

    assert response.status_code == 200, response.text
    assert waits == [2.0]
    assert response.json()["service"] == "daytime"
    assert [call["model"] for call in fake_state.ollama_calls] == ["daytime"]


def test_subscriber_keeps_the_status_endpoint_current(settings_factory, fake_state) -> None:
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="router.subscriber")

        def router_status() -> dict[str, Any] | None:
            return client.get("/api/prompt-assistant/status").json().get("router")

        status = _eventually(lambda: (router_status() or {}).get("service") == "nighttime")
        assert status, router_status()
        assert _eventually(lambda: fake_state.router_event_connections >= 1)
        current = client.get("/api/prompt-assistant/status").json()
        assert current["available"] is True
        assert current["router"] == {
            "selection": "capability",
            "nsfw_preference": "prefer",
            "state": "ready",
            "service": "nighttime",
            "nsfw": True,
            "fallback": False,
            "reason": "most_capable_nsfw",
            "configuration_id": "fake-paired",
            "vision_service": "nighttime",
            "vision_nsfw": True,
            "vision_fallback": False,
            "notice": None,
        }

        # A solo configuration arrives as a change event and is persisted at once.
        fake_state.router_configuration = "solo"
        assert _eventually(lambda: (router_status() or {}).get("service") == "daytime")
        solo = client.get("/api/prompt-assistant/status").json()
        assert solo["available"] is True
        assert solo["router"]["fallback"] is True
        assert solo["router"]["nsfw"] is False
        assert solo["router"]["configuration_id"] == "fake-solo"
        assert solo["router"]["notice"].startswith("No NSFW model is available")

        # Draining reports waiting; the return of Nighttime ends the fallback.
        fake_state.router_draining = True
        assert _eventually(lambda: (router_status() or {}).get("state") == "waiting")
        fake_state.router_draining = False
        fake_state.router_configuration = "paired"
        assert _eventually(lambda: (router_status() or {}).get("service") == "nighttime")
        assert client.get("/api/prompt-assistant/status").json()["router"]["fallback"] is False

        # The subscriber, not a per-call read, keeps the document current.
        before = sum(
            1 for item in fake_state.router_requests if item["path"] == "/v1/router/capabilities"
        )
        assert _compose(client).status_code == 200
        after = sum(
            1 for item in fake_state.router_requests if item["path"] == "/v1/router/capabilities"
        )
        assert after == before
    assert _eventually(lambda: fake_state.router_event_connections == 0)


def test_subscriber_polls_while_the_event_stream_is_refused(settings_factory, fake_state) -> None:
    fake_state.router_events_status = 503
    settings = settings_factory(enable_background_worker=True)
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="router.polling")
        assert _eventually(
            lambda: (
                (client.get("/api/prompt-assistant/status").json().get("router") or {}).get(
                    "service"
                )
                == "nighttime"
            )
        )
        assert any(item["path"] == "/v1/router/events" for item in fake_state.router_requests)
        assert fake_state.router_event_connections == 0


def test_router_unreachable_at_startup_does_not_prevent_startup(settings_factory) -> None:
    # Port 9 (discard) is closed on the loopback interface: the router is unreachable.
    settings = settings_factory(enable_background_worker=True, ollama_base_url="http://127.0.0.1:9")
    with TestClient(create_app(settings)) as client:
        provision_user(client, username="router.unreachable")
        assert client.get("/api/health").status_code == 200

        def status() -> dict[str, Any]:
            return client.get("/api/prompt-assistant/status").json()

        unavailable = _eventually(
            lambda: (status().get("router") or {}).get("reason") == "router_unreachable"
        )
        assert unavailable, status()
        current = status()
        assert current["available"] is False
        assert "capabilities could not be read" in current["message"]
        compose = _compose(client)
        assert compose.status_code == 503
        assert compose.json()["error"]["code"] == "ollama_unavailable"
