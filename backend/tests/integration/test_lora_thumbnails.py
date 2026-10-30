"""A LoRA's picture is derived from its Demo Reel first slot, never uploaded."""

from __future__ import annotations

import io
from datetime import timedelta
from uuid import uuid4

import pytest
from app.main import create_app
from app.models import Favorite, LoraLibraryImage, utcnow
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from tests.conftest import csrf
from tests.helpers import login_ready_admin, provision_user
from tests.integration.test_demo_reels import configure, finish, jobs, tick, update
from tests.publication_fixtures import moody_lora_library_files


@pytest.fixture
def gallery_client(fake_state, settings_factory):
    fake_state.workflow_files = moody_lora_library_files()
    with TestClient(create_app(settings_factory())) as client:
        _, user_cookie = provision_user(client, username="thumb.artist")
        admin_cookie = login_ready_admin(client)
        client.portal.call(client.app.state.container.worker.start)
        source = client.get("/api/workflows").json()[0]
        detail = client.get(f"/api/workflows/{source['source_key']}").json()
        preferences = client.get("/api/preferences").json()
        assert (
            client.put(
                "/api/preferences",
                headers={"X-CSRF-Token": csrf(client)},
                json={
                    "expected_revision": preferences["revision"],
                    "checkpoint_tiers": {
                        "A": [detail["model_selectors"][0]["choices"][0]["checkpoint_id"]],
                        "B": [detail["model_selectors"][0]["choices"][1]["checkpoint_id"]],
                    },
                },
            ).status_code
            == 200
        )
        yield client, detail, admin_cookie, user_cookie


def control(detail) -> dict:
    return next(item for item in detail["interface"]["inputs"] if item["type"] == "lora_stack")


def container_of(client):
    return client.app.state.container


def pictures(client, detail) -> dict:
    path = f"/api/workflows/{detail['source_key']}/lora-images/loras"
    return {item["id"]: item for item in client.get(path).json()["items"]}


def sweep(client):
    container_of(client).demo_reels.thumbnails(force=True)


def stored(client, identity: str) -> LoraLibraryImage | None:
    with container_of(client).db.session_factory() as session:
        return session.get(LoraLibraryImage, identity)


def generate(client, path: str, *, rounds=2) -> list[dict]:
    """Run the idle reel until its images exist, then return the reel's entries."""

    for _ in range(rounds):
        tick(client, quiet=True)
        finish(client, jobs(client)[-1].id)
    return client.get(path).json()["entries"]


def favorite(client, path: str, generation_id: str, *, at=None):
    response = client.post(
        path + "/actions",
        headers={"X-CSRF-Token": csrf(client)},
        json={"action": "favorite", "generation_ids": [generation_id]},
    )
    assert response.status_code == 200, response.text
    if at is not None:
        with container_of(client).db.session_factory() as session:
            bookmark = session.scalar(
                select(Favorite).where(Favorite.generation_id == generation_id)
            )
            bookmark.created_at = at
            session.commit()
    return response


def test_slot_one_image_becomes_the_lora_picture_and_uploading_is_gone(gallery_client):
    client, detail, _, _ = gallery_client
    item = control(detail)["items"][0]
    assert pictures(client, detail)[item["id"]]["image_url"] is None

    _, path = configure(client, detail)
    ready = [entry for entry in generate(client, path) if entry["generation"]]
    assert len(ready) == 2
    sweep(client)

    picture = pictures(client, detail)[item["id"]]
    assert picture["image_url"].startswith(f"/api/lora-library/images/{item['lora_identity']}/")
    served = client.get(picture["image_url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/webp")
    assert Image.open(io.BytesIO(served.content)).size[0] <= 320

    # The first image of the first slot is the reel's own first card: A before B.
    stored_row = stored(client, item["lora_identity"])
    assert stored_row.derived_generation_id == ready[0]["generation"]["id"]
    assert stored_row.derived_artifact_id == ready[0]["generation"]["display_artifact"]["id"]

    upload = client.post(
        f"/api/workflows/{detail['source_key']}/lora-images/loras",
        headers={"X-CSRF-Token": csrf(client)},
        data={"changes": "[]"},
    )
    assert upload.status_code == 405


def test_favoriting_sets_the_picture_and_the_earliest_favorite_keeps_it(gallery_client):
    client, detail, _, _ = gallery_client
    identity = control(detail)["items"][0]["lora_identity"]
    _, path = configure(client, detail)
    first, second = (
        entry["generation"]["id"] for entry in generate(client, path) if entry["generation"]
    )
    sweep(client)
    before = stored(client, identity)
    assert before.derived_generation_id == first

    # Favoriting in the reel changes the LoRA's picture without waiting for a sweep.
    favorite(client, path, second)
    assert stored(client, identity).derived_generation_id == second

    # A later favorite of another image does not overrule the earlier one.
    now = utcnow()
    favorite(client, path, second, at=now - timedelta(minutes=5))
    favorite(client, path, first, at=now)
    sweep(client)
    assert stored(client, identity).derived_generation_id == second

    # With nothing favorited left, the first image of the slot shows again.
    assert (
        client.post(
            path + "/actions",
            headers={"X-CSRF-Token": csrf(client)},
            json={"action": "unfavorite", "generation_ids": [second]},
        ).status_code
        == 200
    )
    assert stored(client, identity).derived_generation_id == first
    assert stored(client, identity).revision > before.revision


def test_only_the_first_slot_supplies_the_picture(gallery_client):
    client, detail, _, _ = gallery_client
    identity = control(detail)["items"][0]["lora_identity"]
    settings, path = configure(client, detail, slots=2)
    entries = generate(client, path, rounds=4)
    first_slot = [entry for entry in entries if entry["slot_id"] == settings["slots"][0]["id"]]
    second_slot = [entry for entry in entries if entry["slot_id"] == settings["slots"][1]["id"]]
    sweep(client)
    assert stored(client, identity).derived_generation_id == first_slot[0]["generation"]["id"]

    favorite(client, path, second_slot[0]["generation"]["id"])
    assert stored(client, identity).derived_generation_id == first_slot[0]["generation"]["id"]

    # Reordering the slots moves the picture to whichever slot is now first.
    assert update(client, settings, slots=list(reversed(settings["slots"]))).status_code == 200
    sweep(client)
    assert stored(client, identity).derived_generation_id == second_slot[0]["generation"]["id"]


def test_removing_the_source_image_falls_back(gallery_client):
    client, detail, _, _ = gallery_client
    identity = control(detail)["items"][0]["lora_identity"]
    _, path = configure(client, detail)
    generate(client, path, rounds=1)
    sweep(client)
    source = stored(client, identity)
    assert source.storage_path is not None
    obsolete = source.storage_path

    assert (
        client.post(
            path + "/actions",
            headers={"X-CSRF-Token": csrf(client)},
            json={"action": "delete", "generation_ids": [source.derived_generation_id]},
        ).status_code
        == 200
    )
    tick(client)
    sweep(client)
    after = stored(client, identity)
    assert after.storage_path is None
    assert after.derived_generation_id is None
    assert after.derived_artifact_id is None
    assert not (container_of(client).assets.data_dir / obsolete).is_file()
    assert pictures(client, detail)[control(detail)["items"][0]["id"]]["image_url"] is None


def test_reconciling_replaces_a_hand_uploaded_picture_once(gallery_client):
    client, detail, _, _ = gallery_client
    identity = control(detail)["items"][0]["lora_identity"]
    _, path = configure(client, detail)
    assets = container_of(client).assets
    buffer = io.BytesIO()
    Image.new("RGB", (24, 18), "red").save(buffer, "PNG")
    buffer.seek(0)
    uploaded = assets.store_lora_thumbnail(buffer)
    with container_of(client).db.session_factory() as session:
        session.add(
            LoraLibraryImage(
                lora_identity=identity, revision=1, storage_path=uploaded.relative_path
            )
        )
        session.commit()

    ready = [entry for entry in generate(client, path) if entry["generation"]]
    sweep(client)
    replaced = stored(client, identity)
    assert replaced.derived_generation_id == ready[0]["generation"]["id"]
    assert not (assets.data_dir / uploaded.relative_path).is_file()

    # A reel that still points at the stored picture rewrites nothing.
    revision = replaced.revision
    sweep(client)
    assert stored(client, identity).revision == revision


def test_a_reel_without_images_leaves_the_picture_empty(gallery_client):
    client, detail, _, _ = gallery_client
    identity = control(detail)["items"][0]["lora_identity"]
    _, path = configure(client, detail)
    sweep(client)
    assert stored(client, identity) is None
    assert pictures(client, detail)[control(detail)["items"][0]["id"]]["image_url"] is None
    assert (
        client.post(
            path + "/actions",
            headers={"X-CSRF-Token": csrf(client)},
            json={"action": "favorite", "generation_ids": [str(uuid4())]},
        ).status_code
        == 404
    )


def test_a_second_library_listing_the_same_lora_keeps_its_picture(gallery_client):
    """The picture is shared per identity, so an empty second reel must not clear it."""

    from app.demo_reel_models import DemoReel
    from app.models import Collection
    from app.services.demo_reel_activity import SYSTEM_OWNER_ID

    client, detail, _, _ = gallery_client
    identity = control(detail)["items"][0]["lora_identity"]
    _, path = configure(client, detail)
    ready = [entry for entry in generate(client, path) if entry["generation"]]
    sweep(client)
    before = stored(client, identity)
    assert before.derived_generation_id == ready[0]["generation"]["id"]

    container = container_of(client)
    with container.db.session_factory() as session:
        hidden = Collection(owner_id=SYSTEM_OWNER_ID, name="Another family reel")
        session.add(hidden)
        session.flush()
        session.add(
            DemoReel(
                library_key="another-family",
                lora_identity=identity,
                collection_id=hidden.id,
            )
        )
        session.commit()

    sweep(client)
    after = stored(client, identity)
    assert (after.storage_path, after.revision) == (before.storage_path, before.revision)
    assert after.derived_generation_id == before.derived_generation_id
    assert pictures(client, detail)[control(detail)["items"][0]["id"]]["image_url"] is not None


def test_switching_a_loras_reel_off_erases_its_picture_too(gallery_client):
    """A LoRA that has left the Demo Reel must not go on showing a picture of it."""

    client, detail, _, _ = gallery_client
    item = control(detail)["items"][0]
    _, path = configure(client, detail)
    ready = [entry for entry in generate(client, path) if entry["generation"]]
    favorite(client, path, ready[1]["generation"]["id"])
    sweep(client)
    before = stored(client, item["lora_identity"])
    picture = pictures(client, detail)[item["id"]]["image_url"]
    assert picture is not None
    assert before.derived_generation_id == ready[1]["generation"]["id"]

    answer = client.put(
        f"/api/admin/lora-library/krea2/demo-reel/{item['lora_identity']}",
        headers={"X-CSRF-Token": csrf(client)},
        json={"enabled": False},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["images_removed"] == len(ready)

    assert pictures(client, detail)[item["id"]]["image_url"] is None
    row = stored(client, item["lora_identity"])
    assert (row.storage_path, row.derived_generation_id) == (None, None)
    assert not (container_of(client).assets.data_dir / before.storage_path).is_file()
    # The favorite that chose this picture was only a marker on the erased image.
    with container_of(client).db.session_factory() as session:
        assert session.scalars(select(Favorite)).first() is None
    assert client.get(picture).status_code == 404

    # Putting the LoRA back in the reel does not resurrect the picture.
    assert (
        client.put(
            f"/api/admin/lora-library/krea2/demo-reel/{item['lora_identity']}",
            headers={"X-CSRF-Token": csrf(client)},
            json={"enabled": True},
        ).status_code
        == 200
    )
    sweep(client)
    assert pictures(client, detail)[item["id"]]["image_url"] is None
