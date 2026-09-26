from __future__ import annotations

import io
import json

from app.main import create_app
from fastapi.testclient import TestClient
from PIL import Image
from tests.conftest import csrf
from tests.helpers import login_ready_admin, provision_user, restore_cookie
from tests.publication_fixtures import add_lora_stack, build_publication_bundle


def _png(color: str = "red") -> bytes:
    image = Image.new("RGB", (28, 22), color)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _source(client: TestClient) -> str:
    for item in client.get("/api/workflows").json():
        detail = client.get(f"/api/workflows/{item['source_key']}").json()
        if any(control["type"] == "lora_stack" for control in detail["interface"]["inputs"]):
            return item["source_key"]
    raise AssertionError("LoRA source missing")


def _update(client: TestClient, path: str, changes: list[dict], files: dict | None = None):
    data = {"changes": json.dumps(changes)}
    uploaded = {key: ("sample.png", value, "image/png") for key, value in (files or {}).items()}
    return client.post(path, headers={"X-CSRF-Token": csrf(client)}, data=data, files=uploaded)


def test_shared_lora_images_upload_conflict_remove_and_auth(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory())) as client:
        assert client.get("/api/workflows/anything/lora-images/loras").status_code == 401
        _, artist_cookie = provision_user(client, username="lora.images")
        source_key = _source(client)
        path = f"/api/workflows/{source_key}/lora-images/loras"
        before = client.get(path)
        assert before.status_code == 200, before.text
        items = {item["id"]: item for item in before.json()["items"]}
        assert set(items) == {"a", "b"}
        assert all(item["image_url"] is None for item in items.values())

        change = {
            "id": "a",
            "version": items["a"]["version"],
            "action": "set",
            "file_key": "image_0",
        }
        saved = _update(client, path, [change], {"image_0": _png()})
        assert saved.status_code == 200, saved.text
        after = {item["id"]: item for item in saved.json()["items"]}
        assert after["a"]["version"] != items["a"]["version"]
        url = after["a"]["image_url"]
        image = client.get(url)
        assert image.status_code == 200
        assert image.headers["content-type"].startswith("image/webp")
        assert Image.open(io.BytesIO(image.content)).size == (28, 22)
        assert client.get(path).json()["items"] == saved.json()["items"]

        login_ready_admin(client)
        assert client.get(path).json()["items"] == saved.json()["items"]
        assert client.get(url).status_code == 200
        conflict = _update(client, path, [change], {"image_0": _png("blue")})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "lora_image_conflict"
        assert client.get(url).status_code == 200

        removed = _update(
            client, path, [{"id": "a", "version": after["a"]["version"], "action": "remove"}]
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["items"][0]["image_url"] is None
        assert client.get(url).status_code == 404
        restore_cookie(client, artist_cookie)
        assert client.get(path).json()["items"] == removed.json()["items"]


def test_lora_image_batch_is_atomic_when_upload_invalid(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="lora.images.invalid")
        path = f"/api/workflows/{_source(client)}/lora-images/loras"
        items = {item["id"]: item for item in client.get(path).json()["items"]}
        changes = [
            {
                "id": key,
                "version": items[key]["version"],
                "action": "set",
                "file_key": f"image_{index}",
            }
            for index, key in enumerate(("a", "b"))
        ]
        response = _update(client, path, changes, {"image_0": _png(), "image_1": b"not an image"})
        assert response.status_code in {400, 415, 422}
        assert client.get(path).json()["items"] == list(items.values())
