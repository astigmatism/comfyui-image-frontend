from __future__ import annotations

from typing import Any

import httpx
from app.services.queue_worker import (
    _cached_node_count,
    _failure_status,
    _sources_were_absent,
)


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://comfyui.invalid/view")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("failed", request=request, response=response)


def test_failure_status_records_the_transport_status_when_present() -> None:
    assert _failure_status(_http_error(404)) == {"status": 404}
    assert _failure_status(_http_error(500)) == {"status": 500}


def test_failure_status_is_empty_for_errors_without_a_response() -> None:
    """A timeout or a decode error carries no status and must not imply absence."""

    assert _failure_status(httpx.ReadTimeout("slow")) == {}
    assert _failure_status(ValueError("decode")) == {}


def test_sources_were_absent_requires_every_failure_to_be_missing() -> None:
    absent = {"filename": "final_00001_.png", "status": 404}
    unreachable = {"filename": "final_00002_.png", "status": 500}

    assert _sources_were_absent([absent]) is True
    assert _sources_were_absent([absent, dict(absent, filename="other.png")]) is True
    # A single ambiguous failure withholds the retry: the run may have produced output.
    assert _sources_were_absent([absent, unreachable]) is False
    assert _sources_were_absent([unreachable]) is False
    assert _sources_were_absent([{"filename": "no-status.png"}]) is False


def test_sources_were_absent_rejects_empty_and_malformed_input() -> None:
    assert _sources_were_absent([]) is False
    assert _sources_were_absent(None) is False
    assert _sources_were_absent("not-a-list") is False
    assert _sources_were_absent(["not-a-mapping"]) is False


def test_cached_node_count_sums_reported_cache_hits() -> None:
    history: dict[str, Any] = {
        "status": {
            "messages": [
                ["execution_start", {"prompt_id": "p"}],
                ["execution_cached", {"prompt_id": "p", "nodes": ["1", "2", "3"]}],
                ["execution_cached", {"prompt_id": "p", "nodes": ["4"]}],
                ["execution_success", {"prompt_id": "p"}],
            ]
        }
    }

    assert _cached_node_count(history) == 4


def test_cached_node_count_tolerates_absent_or_unusable_history() -> None:
    assert _cached_node_count({}) == 0
    assert _cached_node_count({"status": None}) == 0
    assert _cached_node_count({"status": {"messages": None}}) == 0
    assert _cached_node_count({"status": {"messages": [["execution_cached", {}]]}}) == 0
    assert _cached_node_count({"status": {"messages": [["execution_cached"]]}}) == 0
    assert _cached_node_count({"status": {"messages": ["malformed"]}}) == 0
