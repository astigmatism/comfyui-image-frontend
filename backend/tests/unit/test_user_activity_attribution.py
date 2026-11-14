"""Which HTTP requests count as user work that idle Demo Reel generation must yield to.

[docs/demo-reel.md](../../../docs/demo-reel.md) promises that "Passive polling, SSE, image
loading, and programmatic scrolling do not" keep the server awake, while the browser reports
genuine input through `POST /api/activity`. A read must never hold the reels back even if a
future route happens to share the dependency that records activity.
"""

from __future__ import annotations

from app.dependencies import marks_user_activity
from starlette.requests import Request


def request(method: str, path: str) -> Request:
    return Request(
        {"type": "http", "method": method, "path": path, "headers": [], "query_string": b""}
    )


def test_reads_and_the_passive_gallery_lookup_are_not_activity() -> None:
    polls = [
        ("GET", "/api/auto-generation"),
        ("GET", "/api/generation-activity"),
        ("GET", "/api/preferences"),
        ("GET", "/api/comfyui-instances"),
        ("GET", "/api/workflows"),
        ("HEAD", "/api/gallery/42/image"),
    ]
    for method, path in polls:
        assert not marks_user_activity(request(method, path)), f"{method} {path}"
    assert not marks_user_activity(request("POST", "/api/gallery/prompt-groups/lookup"))


def test_reported_input_and_state_changing_requests_are_activity() -> None:
    work = [
        ("POST", "/api/activity"),
        ("POST", "/api/generations"),
        ("PUT", "/api/preferences"),
        ("PATCH", "/api/lora-library/krea2/items/abc123"),
        ("DELETE", "/api/collections/7"),
    ]
    for method, path in work:
        assert marks_user_activity(request(method, path)), f"{method} {path}"
