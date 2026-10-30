from __future__ import annotations

import io

from app.main import create_app
from app.models import LoraLibraryImage
from fastapi.testclient import TestClient
from PIL import Image
from tests.conftest import csrf
from tests.helpers import login_ready_admin, provision_user
from tests.publication_fixtures import (
    add_lora_stack,
    build_publication_bundle,
    moody_lora_library_files,
)


def _store_lora_image(container) -> str:
    image = Image.new("RGB", (28, 22), "red")
    output = io.BytesIO()
    image.save(output, "PNG")
    output.seek(0)
    return container.assets.store_lora_thumbnail(output).relative_path


def _sources(client: TestClient) -> list[str]:
    keys = []
    for item in client.get("/api/workflows").json():
        detail = client.get(f"/api/workflows/{item['source_key']}").json()
        if any(control["type"] == "lora_stack" for control in detail["interface"]["inputs"]):
            keys.append(item["source_key"])
    return keys


def test_lora_images_are_read_only_for_every_signed_in_user(fake_state, settings_factory):
    fake_state.workflow_files = dict(
        build_publication_bundle("moody", mutate_artifacts=add_lora_stack).files
    )
    with TestClient(create_app(settings_factory())) as client:
        assert client.get("/api/workflows/anything/lora-images/loras").status_code == 401
        provision_user(client, username="lora.images")
        path = f"/api/workflows/{_sources(client)[0]}/lora-images/loras"
        before = client.get(path)
        assert before.status_code == 200, before.text
        items = {item["id"]: item for item in before.json()["items"]}
        assert set(items) == {"a", "b"}
        assert all(item["image_url"] is None for item in items.values())
        assert all(item["lora_identity"].startswith("lr1_") for item in items.values())

        # Nothing is uploaded any more: a LoRA's picture comes from its Demo Reel.
        tokens = [csrf(client)]
        login_ready_admin(client)
        tokens.append(csrf(client))
        for token in tokens:
            refused = client.post(path, headers={"X-CSRF-Token": token}, data={"changes": "[]"})
            assert refused.status_code == 405
        assert client.get(path).json()["items"] == before.json()["items"]


def test_one_lora_image_is_shared_by_every_workflow_that_lists_it(fake_state, settings_factory):
    fake_state.workflow_files = moody_lora_library_files()
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client, username="lora.library.images")
        first, second = (f"/api/workflows/{key}/lora-images/loras" for key in _sources(client))
        items = {item["id"]: item for item in client.get(first).json()["items"]}
        assert items["a"]["image_url"] is None

        # Writing the row is the Demo Reel reconciliation's job; here it stands in.
        container = client.app.state.container
        with container.db.session_factory() as session:
            session.add(
                LoraLibraryImage(
                    lora_identity=items["a"]["lora_identity"],
                    revision=1,
                    storage_path=_store_lora_image(container),
                )
            )
            session.commit()

        published = {item["id"]: item for item in client.get(first).json()["items"]}
        shared = {item["id"]: item for item in client.get(second).json()["items"]}
        assert shared["a"] == published["a"]
        assert shared["a"]["image_url"].startswith("/api/lora-library/images/lr1_")
        response = client.get(shared["a"]["image_url"])
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/webp")
        assert Image.open(io.BytesIO(response.content)).size == (28, 22)

        # Another account reads the same shared picture, and an old version token is
        # refused so the immutable cache can never pin a superseded picture.
        login_ready_admin(client)
        assert client.get(first).json()["items"] == list(published.values())
        assert client.get(shared["a"]["image_url"]).status_code == 200
        stale = shared["a"]["image_url"].split("?v=")[0] + "?v=" + "0" * 64
        assert client.get(stale).status_code == 404

        with container.db.session_factory() as session:
            row = session.get(LoraLibraryImage, items["a"]["lora_identity"])
            row.storage_path = None
            row.revision += 1
            session.commit()
        assert {item["id"]: item for item in client.get(first).json()["items"]}["a"][
            "image_url"
        ] is None
        assert client.get(shared["a"]["image_url"]).status_code == 404
