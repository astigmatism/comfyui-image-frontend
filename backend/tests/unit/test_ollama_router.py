from __future__ import annotations

import json

import httpx
import pytest
from app.config import Settings
from app.errors import AppError
from app.services.ollama import OllamaAdapter


@pytest.mark.parametrize("mode", ["create", "refine"])
@pytest.mark.parametrize("think", [False, True])
async def test_alias_and_auth_survive_transport_and_budget_retries(mode: str, think: bool) -> None:
    settings = Settings(
        test_mode=True,
        ollama_base_url=" http://router.test/v1/ ",
        ollama_api_key="router-private-key",
    )
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer router-private-key"
        assert request.url.host == "router.test"
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "nighttime"}]})
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["model"] == "nighttime"
        assert payload["think"] == ("xhigh" if think else False)
        assert "change to moonlight" in payload["messages"][0]["content"]
        assert ("existing daylight scene" in payload["messages"][0]["content"]) is (
            mode == "refine"
        )
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "temporarily busy"})
        if len(calls) == 2:
            return httpx.Response(
                200,
                json={
                    "model": "resolved-backend",
                    "message": {"content": "", "thinking": "private reasoning"},
                    "done_reason": "length",
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "resolved-backend",
                "message": {
                    "content": '{"prompt":"A quiet lake illuminated by moonlight."}',
                    "thinking": "private reasoning" if think else "",
                },
                "done_reason": "stop",
            },
        )

    async def skip_retry(_: float) -> None:
        pass

    adapter = OllamaAdapter(
        settings, transport=httpx.MockTransport(handler), retry_sleeper=skip_retry
    )
    try:
        result = await adapter.compose(
            mode=mode,
            prompt="existing daylight scene",
            direction="change to moonlight",
            think=think,
        )
    finally:
        await adapter.close()
    assert result.model == "resolved-backend"
    assert len(calls) == 3
    assert calls[0] == calls[1]
    assert calls[2]["options"]["num_predict"] == 4096
    assert calls[0]["options"]["seed"] == calls[2]["options"]["seed"]
    assert "router-private-key" not in repr(settings)
    assert "router-private-key" not in json.dumps(result.raw_response)
    assert "private reasoning" not in json.dumps(result.raw_response)


@pytest.mark.parametrize(
    "models",
    [
        [{"name": "daytime"}],
        [
            {"name": "daytime"},
            {"name": "nighttime", "x_ollama_router": {"health": {"available": False}}},
        ],
        None,
        "nighttime",
        [{"name": "   "}],
    ],
)
async def test_missing_or_offline_alias_never_falls_back_to_another_model(models: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags", "unavailable alias must not generate"
        return httpx.Response(200, json={"models": models})

    adapter = OllamaAdapter(
        Settings(test_mode=True, ollama_base_url="http://router.test"),
        transport=httpx.MockTransport(handler),
    )
    try:
        available, message = await adapter.status()
        assert not available
        assert message
        with pytest.raises(AppError) as error:
            await adapter.compose(mode="create", prompt="", direction="a fox")
        assert error.value.code == "ollama_unavailable"
    finally:
        await adapter.close()


async def test_empty_model_explicitly_preserves_legacy_router_default_without_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "other-model"}]})
        assert "model" not in json.loads(request.content)
        return httpx.Response(
            200, json={"model": "other-model", "response": '{"prompt":"A fox in a forest."}'}
        )

    adapter = OllamaAdapter(
        Settings(test_mode=True, ollama_base_url="http://router.test", ollama_model=" "),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert (await adapter.status())[0]
        assert (await adapter.compose(mode="create", prompt="", direction="a fox")).model == (
            "other-model"
        )
    finally:
        await adapter.close()
