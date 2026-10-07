"""The LLM Router reference-client port: selection, classification and the subscriber loop.

Contract: docs/llm-router-contract.md (version 1). These tests pin the behaviour of
``app.services.llm_router`` to the reference client ``router_watch.py`` at llm-router d5edba8.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from app.services.llm_router import (
    CLIENT_NAME,
    CLIENT_NAME_HEADER,
    FAIL,
    FALLBACK,
    RETRY,
    STREAM_IDLE_SECONDS,
    UNAVAILABLE,
    WAIT,
    RouterWatch,
    classify_error,
    error_code,
    model_for,
    pick_service,
    resolve,
    supported_efforts,
    thinking_value,
)
from tests.router_fixtures import (
    CAPABILITIES_PATH,
    DAYTIME_PAIRED_ID,
    EVENTS_PATH,
    NIGHTTIME_ID,
    capabilities_response,
    paired_document,
    router_document,
    router_model,
    solo_document,
    sse_event,
)

# ---------- selection by capability (pick_service) ----------


def test_paired_configuration_prefers_the_most_capable_nsfw_model() -> None:
    doc = paired_document()
    assert pick_service(doc, nsfw=True, fallback_any=True) == "nighttime"
    # Daytime scores higher; without the NSFW filter the most capable model wins.
    assert pick_service(doc) == "daytime"
    assert pick_service(doc, nsfw=False) == "daytime"


def test_solo_configuration_falls_back_to_the_most_capable_model() -> None:
    doc = solo_document()
    assert pick_service(doc, nsfw=True, fallback_any=True) == "daytime"
    assert pick_service(doc, nsfw=True) == UNAVAILABLE


def test_unhealthy_nsfw_model_falls_back_to_daytime() -> None:
    doc = paired_document(unavailable={"nighttime"})
    assert pick_service(doc, nsfw=True, fallback_any=True) == "daytime"


def test_two_nsfw_models_choose_the_higher_score_then_the_larger_context() -> None:
    doc = router_document(
        [
            router_model("daytime", nsfw=False, score=90.0),
            router_model("nighttime", nsfw=True, score=64.9),
            router_model("midnight", nsfw=True, score=71.2),
        ]
    )
    assert pick_service(doc, nsfw=True, fallback_any=True) == "midnight"
    tied = router_document(
        [
            router_model("nighttime", nsfw=True, score=70.0, context_window=98_304),
            router_model("midnight", nsfw=True, score=70.0, context_window=131_072),
            router_model("unscored", nsfw=True, score=None, context_window=1_000_000),
        ]
    )
    assert pick_service(tied, nsfw=True) == "midnight"


def test_vision_requirement_selects_the_only_model_with_image_input() -> None:
    doc = router_document(
        [
            router_model("daytime", nsfw=False, score=60.0, vision=True),
            router_model("nighttime", nsfw=True, score=70.0, vision=False),
        ]
    )
    assert pick_service(doc, nsfw=True, require=["vision"], fallback_any=True) == "daytime"
    assert pick_service(doc, nsfw=True, require=["vision"]) == UNAVAILABLE
    with pytest.raises(ValueError, match="unknown required feature"):
        pick_service(doc, require=["telepathy"])


def test_draining_router_waits_instead_of_choosing() -> None:
    for doc in (paired_document(draining=True), paired_document(maintenance=True)):
        assert pick_service(doc, nsfw=True, fallback_any=True) == WAIT
        assert resolve(doc, "nighttime", ["daytime"]) == WAIT


def test_exclude_skips_a_service_that_just_failed() -> None:
    doc = paired_document()
    assert pick_service(doc, nsfw=True, fallback_any=True, exclude=["nighttime"]) == "daytime"
    assert pick_service(doc, exclude=["daytime", "nighttime"]) == UNAVAILABLE


def test_no_document_is_unavailable_by_capability_but_named_sends_the_preference() -> None:
    assert pick_service(None, nsfw=True, fallback_any=True) == UNAVAILABLE
    assert resolve(None, "nighttime", ["daytime"]) == "nighttime"
    assert resolve(None, "nighttime", ["daytime"], exclude=["nighttime"]) == "daytime"


def test_nsfw_null_is_not_known_to_be_nsfw() -> None:
    doc = router_document([router_model("daytime", nsfw=None, score=50.0)])
    assert pick_service(doc, nsfw=True) == UNAVAILABLE
    assert pick_service(doc, nsfw=False) == UNAVAILABLE
    assert pick_service(doc, nsfw=True, fallback_any=True) == "daytime"


# ---------- selection by name (resolve) ----------


def test_named_selection_uses_the_preferred_service_then_fallbacks() -> None:
    assert resolve(paired_document(), "nighttime", ["daytime"]) == "nighttime"
    assert resolve(solo_document(), "nighttime", ["daytime"]) == "daytime"
    assert resolve(solo_document(), "nighttime", []) == UNAVAILABLE
    # Aliases are accepted IDs too.
    assert resolve(solo_document(), "local-active") == "local-active"
    blind = paired_document(vision=False)
    assert resolve(blind, "nighttime", ["daytime"], require=["vision"]) == UNAVAILABLE


def test_model_for_returns_the_limits_of_the_model_behind_a_service() -> None:
    doc = paired_document()
    nighttime = model_for(doc, "nighttime")
    assert nighttime is not None and nighttime["id"] == NIGHTTIME_ID
    assert nighttime["context_window"] == 98_304
    assert model_for(doc, "local-active")["id"] == DAYTIME_PAIRED_ID  # type: ignore[index]
    assert model_for(doc, "missing") is None
    assert model_for(None, "daytime") is None


# ---------- error classification (contract section 10) ----------


@pytest.mark.parametrize(
    ("status", "body", "action"),
    [
        (503, {"error": {"code": "SERVICE_OFFLINE", "message": "x"}}, FALLBACK),
        (503, {"error": {"code": "BACKEND_UNAVAILABLE", "message": "x"}}, FALLBACK),
        (404, {"error": {"code": "MODEL_NOT_FOUND", "message": "x"}}, FALLBACK),
        (503, {"error": {"code": "BACKEND_DRAINING", "message": "x"}}, WAIT),
        (503, {"error": {"code": "MAINTENANCE_MODE", "message": "x"}}, WAIT),
        (503, {"error": {"code": "TOO_MANY_SUBSCRIBERS", "message": "x"}}, RETRY),
        (502, {"error": {"code": "UPSTREAM_TIMEOUT", "message": "x"}}, RETRY),
        (500, {"error": {"code": "SOMETHING_NEW", "message": "x"}}, RETRY),
        (429, None, RETRY),
        (408, None, RETRY),
        (None, None, RETRY),
        (400, {"error": {"code": "context_length_exceeded", "message": "x"}}, FAIL),
        (400, {"error": {"code": "INVALID_THINK_VALUE", "message": "x"}}, FAIL),
        (422, {"error": {"code": "SOMETHING_NEW", "message": "x"}}, FAIL),
        # A legacy string error is tolerated: the status decides.
        (503, {"error": "temporarily busy"}, RETRY),
        (400, {"error": "bad request"}, FAIL),
        (
            400,
            {"error": {"message": "x", "type": "invalid_request_error", "code": "MODEL_NOT_FOUND"}},
            FALLBACK,
        ),
    ],
)
def test_errors_are_classified_by_code_then_status(
    status: int | None, body: object, action: str
) -> None:
    assert classify_error(status, body) == action
    assert classify_error(status, json.dumps(body)) == action


def test_error_code_reads_the_object_form_only() -> None:
    assert error_code(b'{"error": {"code": "SERVICE_OFFLINE"}}') == "SERVICE_OFFLINE"
    assert error_code({"error": "SERVICE_OFFLINE"}) is None
    assert error_code("not json") is None
    assert error_code(None) is None


# ---------- reasoning effort check ----------


def test_thinking_effort_is_sent_only_when_the_model_lists_it() -> None:
    full = router_model("nighttime")
    assert supported_efforts(full) == {"default", "off", "low", "medium", "xhigh"}
    assert thinking_value(full, "xhigh") == "xhigh"
    assert thinking_value(full, "max") == "xhigh"  # alias
    assert thinking_value(full, False) is False
    no_xhigh = router_model("daytime", efforts={"default": "default", "low": "low", "medium": "m"})
    assert thinking_value(no_xhigh, "xhigh") == "medium"  # the closest listed effort
    only_low = router_model("daytime", efforts=["default", "low"])
    assert thinking_value(only_low, "xhigh") == "low"
    only_default = router_model("daytime", efforts={"default": "default", "off": "none"})
    assert thinking_value(only_default, "xhigh") is True
    no_thinking = router_model("daytime", thinking=False)
    assert thinking_value(no_thinking, "xhigh") is False
    assert thinking_value(no_thinking, True) is False
    # Without model facts only the router's default effort is requested, never an unlisted one.
    assert thinking_value(None, "xhigh") is True
    unpublished = router_model("daytime")
    del unpublished["metadata"]["reasoning"]
    assert thinking_value(unpublished, "xhigh") is True


# ---------- the subscriber (RouterWatch) ----------


def _stream(*chunks: str):
    async def body():
        for chunk in chunks:
            yield chunk.encode()

    return body()


async def test_fetch_sends_if_none_match_and_keeps_the_document_on_304() -> None:
    seen: list[str | None] = []
    document = paired_document()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == CAPABILITIES_PATH
        assert request.headers[CLIENT_NAME_HEADER] == CLIENT_NAME
        seen.append(request.headers.get("if-none-match"))
        return capabilities_response(request, document)

    async with httpx.AsyncClient(
        base_url="http://router.test",
        transport=httpx.MockTransport(handler),
        headers={CLIENT_NAME_HEADER: CLIENT_NAME},
    ) as client:
        watch = RouterWatch(client)
        changes: list[str] = []
        watch.add_listener(lambda doc: changes.append(doc["revision"]))
        assert (await watch.fetch())["revision"] == document["revision"]  # type: ignore[index]
        assert (await watch.fetch())["revision"] == document["revision"]  # type: ignore[index]
    assert seen == [None, f'"{document["revision"]}"']
    assert changes == [document["revision"]]


async def test_refresh_tolerates_an_unreachable_router_and_keeps_the_last_document() -> None:
    reachable = True

    def handler(request: httpx.Request) -> httpx.Response:
        if not reachable:
            raise httpx.ConnectError("refused", request=request)
        return capabilities_response(request)

    async with httpx.AsyncClient(
        base_url="http://router.test", transport=httpx.MockTransport(handler)
    ) as client:
        watch = RouterWatch(client)
        reachable = False
        assert await watch.refresh() is None
        assert watch.last_error == "ConnectError"
        reachable = True
        assert (await watch.refresh()) is not None
        reachable = False
        assert (await watch.refresh()) is not None  # the last safe document is retained
        assert watch.last_error == "ConnectError"


async def test_invalid_documents_are_rejected_and_unknown_fields_ignored() -> None:
    payloads = [{"object": "not a document"}, paired_document()]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payloads.pop(0))

    async with httpx.AsyncClient(
        base_url="http://router.test", transport=httpx.MockTransport(handler)
    ) as client:
        watch = RouterWatch(client)
        assert await watch.refresh() is None
        assert watch.last_error == "RouterDocumentError"
        doc = await watch.refresh()
        assert doc is not None and "some_future_field" in doc
        assert pick_service(doc, nsfw=True, fallback_any=True) == "nighttime"


async def test_unexpected_schema_versions_are_warned_about_once(caplog) -> None:
    document = router_document([router_model("daytime")], schema_version=2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document)

    async with httpx.AsyncClient(
        base_url="http://router.test", transport=httpx.MockTransport(handler)
    ) as client:
        watch = RouterWatch(client)
        with caplog.at_level(logging.WARNING, logger="app.services.llm_router"):
            await watch.refresh()
            await watch.refresh()
    warnings = [
        record
        for record in caplog.records
        if record.message == "llm_router_schema_version_unexpected"
    ]
    assert len(warnings) == 1
    assert warnings[0].router_schema_version == 2  # type: ignore[attr-defined]


async def test_subscriber_follows_events_replaces_the_document_and_ignores_keepalives() -> None:
    first = paired_document()
    second = solo_document()
    stop = asyncio.Event()
    timeouts: list[dict] = []
    revisions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            return capabilities_response(request, first)
        assert request.url.path == EVENTS_PATH
        assert request.headers["accept"] == "text/event-stream"
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_stream(
                "retry: 3000\n\n",
                sse_event("capabilities", first, identity=first["revision"]),
                sse_event("load", {"revision": first["revision"], "load": {}}),
                ": keepalive\n\n",
                # The same revision again is not a change.
                sse_event("capabilities", first, identity=first["revision"]),
                sse_event("capabilities", second, identity=second["revision"]),
            ),
        )

    async def sleeper(_: float) -> None:
        stop.set()

    async with httpx.AsyncClient(
        base_url="http://router.test", transport=httpx.MockTransport(handler)
    ) as client:
        watch = RouterWatch(client, sleeper=sleeper)
        watch.add_listener(lambda doc: revisions.append(doc["revision"]))
        await asyncio.wait_for(watch.run_forever(stop), timeout=5)
    assert revisions == [first["revision"], second["revision"]]
    assert watch.doc is not None and watch.doc["configuration"]["id"] == "fake-solo"
    # 60 s without bytes, keepalives included, means the stream is dead.
    assert timeouts[0]["read"] == STREAM_IDLE_SECONDS == 60.0
    assert watch.running is False and watch.connected is False


async def test_subscriber_polls_while_disconnected_and_backs_off_from_3_to_30_seconds() -> None:
    stop = asyncio.Event()
    waits: list[float] = []
    polls: list[str | None] = []
    document = paired_document()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            polls.append(request.headers.get("if-none-match"))
            return capabilities_response(request, document)
        raise httpx.ConnectError("events refused", request=request)

    async def sleeper(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 7:
            stop.set()

    async with httpx.AsyncClient(
        base_url="http://router.test", transport=httpx.MockTransport(handler)
    ) as client:
        watch = RouterWatch(client, sleeper=sleeper)
        await asyncio.wait_for(watch.run_forever(stop), timeout=5)
    assert waits == [3.0, 6.0, 12.0, 24.0, 30.0, 30.0, 30.0]
    # One startup read, then one If-None-Match poll per disconnected interval (at most 30 s).
    assert polls[0] is None
    assert polls[1:] == [f'"{document["revision"]}"'] * 7


async def test_subscriber_polls_when_the_event_stream_has_too_many_subscribers() -> None:
    stop = asyncio.Event()
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == CAPABILITIES_PATH:
            return capabilities_response(request)
        return httpx.Response(
            503, json={"error": {"code": "TOO_MANY_SUBSCRIBERS", "message": "limit reached"}}
        )

    async def sleeper(_: float) -> None:
        if paths.count(EVENTS_PATH) >= 2:
            stop.set()

    async with httpx.AsyncClient(
        base_url="http://router.test", transport=httpx.MockTransport(handler)
    ) as client:
        watch = RouterWatch(client, sleeper=sleeper)
        await asyncio.wait_for(watch.run_forever(stop), timeout=5)
        assert watch.doc is not None
    assert paths == [
        CAPABILITIES_PATH,
        EVENTS_PATH,
        CAPABILITIES_PATH,
        EVENTS_PATH,
        CAPABILITIES_PATH,
    ]


async def test_subscriber_survives_an_unreachable_router_at_startup() -> None:
    stop = asyncio.Event()
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("router down", request=request)

    async def sleeper(_: float) -> None:
        if attempts >= 5:
            stop.set()

    async with httpx.AsyncClient(
        base_url="http://router.test", transport=httpx.MockTransport(handler)
    ) as client:
        watch = RouterWatch(client, sleeper=sleeper)
        await asyncio.wait_for(watch.run_forever(stop), timeout=5)
        assert watch.doc is None
        assert watch.last_error == "ConnectError"


async def test_a_connection_that_delivered_reconnects_after_the_retry_hint() -> None:
    stop = asyncio.Event()
    waits: list[float] = []
    document = paired_document()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CAPABILITIES_PATH:
            return capabilities_response(request, document)
        return httpx.Response(
            200,
            content=_stream(
                "retry: 5000\n\n",
                sse_event("capabilities", document, identity=document["revision"]),
            ),
        )

    async def sleeper(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 3:
            stop.set()

    async with httpx.AsyncClient(
        base_url="http://router.test", transport=httpx.MockTransport(handler)
    ) as client:
        await asyncio.wait_for(RouterWatch(client, sleeper=sleeper).run_forever(stop), timeout=5)
    assert waits == [5.0, 5.0, 5.0]
