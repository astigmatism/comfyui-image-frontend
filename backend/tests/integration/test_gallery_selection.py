from __future__ import annotations

import errno
import os
import time
from functools import partial
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import gettempdir
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from app.main import create_app
from app.models import Artifact, Generation
from app.services import gallery as gallery_service
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.conftest import csrf
from tests.helpers import (
    create_generation,
    login_ready_admin,
    provision_user,
    restore_cookie,
    wait_for_status,
)


def folder(client, name, parent_id=None):
    response = client.post(
        "/api/collections",
        headers={"X-CSRF-Token": csrf(client)},
        json={"name": name, "parent_id": parent_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def transfer(client, operation, generations=(), collections=(), destination=None):
    return client.post(
        "/api/gallery/transfer",
        headers={"X-CSRF-Token": csrf(client)},
        json={
            "operation": operation,
            "generation_ids": list(generations),
            "collection_ids": list(collections),
            "collection_id": destination,
        },
    )


def test_bulk_favorites_bookmark_explicit_cards_without_recursing(settings_factory, fake_state):
    del fake_state
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client)
        parent = folder(client, "Studies")
        child = folder(client, "Winter", parent["id"])
        unselected = folder(client, "Summer", parent["id"])
        selected_image = create_generation(client, "selected favorite")
        other_image = create_generation(client, "not a favorite")
        for image in (selected_image, other_image):
            assert (
                transfer(client, "move", [image["id"]], destination=child["id"]).status_code == 200
            )
        payload = {
            "generation_ids": [selected_image["id"], selected_image["id"]],
            "collection_ids": [parent["id"], child["id"]],
        }
        for _ in range(2):
            response = client.post(
                "/api/gallery/favorite", headers={"X-CSRF-Token": csrf(client)}, json=payload
            )
            assert response.status_code == 200, response.text
            assert response.json()["generation_ids"] == [selected_image["id"]]
            assert response.json()["collection_ids"] == [parent["id"], child["id"]]
        assert client.get(f"/api/generations/{selected_image['id']}").json()["is_favorite"]
        assert not client.get(f"/api/generations/{other_image['id']}").json()["is_favorite"]
        collections = {item["id"]: item for item in client.get("/api/collections").json()}
        assert collections[parent["id"]]["is_favorite"]
        assert collections[child["id"]]["is_favorite"]
        assert not collections[unselected["id"]]["is_favorite"]


def test_bulk_download_includes_nested_batches_once_and_cleans_up(
    settings_factory, fake_state, monkeypatch, tmp_path
):
    del fake_state, tmp_path
    staged: list[Path] = []
    original = gallery_service.NamedTemporaryFile

    def record_staging(*args, **kwargs):
        staged.append(Path(kwargs["dir"]))
        return original(*args, **kwargs)

    monkeypatch.setattr("app.services.gallery.NamedTemporaryFile", record_staging)
    app = create_app(settings_factory(enable_background_worker=True))
    with TestClient(app) as client:
        staging = app.state.container.settings.staging_dir
        provision_user(client)
        parent = folder(client, "Studies")
        child = folder(client, "Winter", parent["id"])
        batch = create_generation(client, "multi image download")
        batch_detail = wait_for_status(client, batch["id"], "succeeded")
        outside = create_generation(client, "outside download")
        outside_detail = wait_for_status(client, outside["id"], "succeeded")
        assert transfer(client, "move", [batch["id"]], destination=child["id"]).status_code == 200
        payload = {
            "generation_ids": [batch["id"], outside["id"]],
            "collection_ids": [parent["id"], child["id"]],
        }
        response = client.post(
            "/api/gallery/download", headers={"X-CSRF-Token": csrf(client)}, json=payload
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert 'filename="gallery-selection.zip"' in response.headers["content-disposition"]
        # Archives are staged inside the writable data volume, never the container /tmp,
        # which deployments mount as a small tmpfs under a read-only root filesystem.
        assert staged == [staging]
        assert staging.is_relative_to(app.state.container.settings.data_dir)
        assert staging != Path(gettempdir())
        expected = [
            item
            for detail in (batch_detail, outside_detail)
            for item in detail["artifacts"]
            if item["kind"] == "image"
        ]
        assert batch_detail["image_count"] == 2
        with ZipFile(BytesIO(response.content)) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names)) == len(expected)
            for artifact in expected:
                name = next(name for name in names if f"image-{artifact['id']}." in name)
                assert archive.read(name) == client.get(artifact["content_url"]).content
                assert not PurePosixPath(name).is_absolute()
                assert ".." not in PurePosixPath(name).parts
                # Extraction drops every image straight into the chosen directory: no
                # folder, generation, or any other directory component survives, even
                # for images selected through nested folders.
                assert name == f"image-{artifact['id']}{PurePosixPath(name).suffix}"
            assert all("/" not in name and "\\" not in name for name in names)
        assert not list(staging.iterdir())

        # A batched manifest must not drop or duplicate rows when the selection spans
        # more IN-clause chunks than a single statement can bind.
        monkeypatch.setattr("app.services.gallery.BATCH_SIZE", 1)
        chunked = client.post(
            "/api/gallery/download", headers={"X-CSRF-Token": csrf(client)}, json=payload
        )
        assert chunked.status_code == 200, chunked.text
        with ZipFile(BytesIO(chunked.content)) as archive:
            assert sorted(archive.namelist()) == sorted(names)
        assert not list(staging.iterdir())


def test_download_reports_capacity_limits_instead_of_failing(
    settings_factory, fake_state, monkeypatch
):
    del fake_state
    app = create_app(settings_factory(enable_background_worker=True))
    with TestClient(app) as client:
        settings = app.state.container.settings
        staging = settings.staging_dir
        provision_user(client)
        image = create_generation(client, "capacity guard")
        wait_for_status(client, image["id"], "succeeded")
        headers = {"X-CSRF-Token": csrf(client)}
        payload = {"generation_ids": [image["id"]]}
        post = partial(client.post, "/api/gallery/download", headers=headers, json=payload)

        # A selection larger than the configured ceiling is refused before any file exists.
        monkeypatch.setattr(settings, "download_max_bytes", 1)
        refused = post()
        assert refused.status_code == 507, refused.text
        assert refused.json()["error"]["code"] == "download_too_large"
        assert "Download fewer items" in refused.json()["error"]["message"]
        assert not list(staging.iterdir()) if staging.is_dir() else True
        monkeypatch.undo()

        # So is a selection that would not fit in the staging filesystem.
        monkeypatch.setattr(
            "app.services.gallery.shutil.disk_usage",
            lambda _: SimpleNamespace(total=0, used=0, free=0),
        )
        cramped = post()
        assert cramped.status_code == 507, cramped.text
        assert cramped.json()["error"]["code"] == "download_too_large"
        assert "is free" in cramped.json()["error"]["message"]
        monkeypatch.undo()

        # Running out of space mid-archive is reported, not raised as HTTP 500.
        def no_space(*args, **kwargs):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr("app.services.gallery.ZipFile.write", no_space)
        full = post()
        assert full.status_code == 507, full.text
        assert full.json()["error"]["code"] == "download_failed"
        assert "temporary space" in full.json()["error"]["message"]
        assert not list(staging.iterdir())
        monkeypatch.undo()

        # Any other archive failure also stays inside the error envelope.
        def broken(*args, **kwargs):
            raise OSError(errno.EACCES, "Permission denied")

        monkeypatch.setattr("app.services.gallery.ZipFile.write", broken)
        denied = post()
        assert denied.status_code == 500, denied.text
        assert denied.json()["error"]["code"] == "download_failed"
        assert not list(staging.iterdir())
        monkeypatch.undo()

        assert post().status_code == 200


def test_download_tolerates_missing_artifact_files(settings_factory, fake_state):
    del fake_state
    app = create_app(settings_factory(enable_background_worker=True))
    with TestClient(app) as client:
        container = app.state.container
        provision_user(client)
        kept = create_generation(client, "kept image")
        pruned = create_generation(client, "pruned image")
        kept_detail = wait_for_status(client, kept["id"], "succeeded")
        pruned_detail = wait_for_status(client, pruned["id"], "succeeded")
        headers = {"X-CSRF-Token": csrf(client)}
        payload = {"generation_ids": [kept["id"], pruned["id"]]}
        with container.db.session_factory() as session:
            paths = list(
                session.scalars(
                    select(Artifact.storage_path).where(Artifact.generation_id == pruned["id"])
                )
            )
        assert paths
        for relative in paths:
            (container.settings.data_dir / relative).unlink()

        # One pruned file must not fail an otherwise complete archive.
        response = client.post("/api/gallery/download", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        with ZipFile(BytesIO(response.content)) as archive:
            names = archive.namelist()
        images = {
            detail["id"]: [item["id"] for item in detail["artifacts"] if item["kind"] == "image"]
            for detail in (kept_detail, pruned_detail)
        }
        # Flat names carry the artifact identifier only, so the pruned generation's
        # images are absent and the kept generation's images are all at the root.
        assert {PurePosixPath(name).stem for name in names} == {
            f"image-{identifier}" for identifier in images[kept["id"]]
        }
        assert all("/" not in name for name in names)
        assert not {PurePosixPath(name).stem for name in names} & {
            f"image-{identifier}" for identifier in images[pruned["id"]]
        }
        assert len(names) == kept_detail["image_count"]

        # When nothing survives, the selection reports the same empty result as before.
        empty = client.post(
            "/api/gallery/download", headers=headers, json={"generation_ids": [pruned["id"]]}
        )
        assert empty.status_code == 409, empty.text
        assert empty.json()["error"]["code"] == "download_empty"
        assert not list(container.settings.staging_dir.iterdir())


def test_sweep_download_staging_reclaims_abandoned_archives(settings_factory, fake_state):
    del fake_state
    settings = settings_factory()
    staging = settings.staging_dir
    staging.mkdir(parents=True, exist_ok=True)
    stale = staging / f"{gallery_service.DOWNLOAD_STAGING_PREFIX}stale.zip"
    fresh = staging / f"{gallery_service.DOWNLOAD_STAGING_PREFIX}fresh.zip"
    unrelated = staging / "keep-me.txt"
    for item in (stale, fresh, unrelated):
        item.write_bytes(b"x")
    aged = time.time() - gallery_service.DOWNLOAD_STAGING_MAX_AGE_SECONDS - 60
    os.utime(stale, (aged, aged))

    assert gallery_service.sweep_download_staging(settings) == 1
    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()

    # A missing staging directory is created rather than failing startup.
    for item in (fresh, unrelated):
        item.unlink()
    staging.rmdir()
    assert gallery_service.sweep_download_staging(settings) == 0
    assert staging.is_dir()


@pytest.mark.parametrize("operation", ["favorite", "download"])
def test_favorite_and_download_validate_ownership_and_csrf(settings_factory, fake_state, operation):
    del fake_state
    with TestClient(create_app(settings_factory())) as client:
        _, owner_cookie = provision_user(client)
        image = create_generation(client, "private image")
        private_folder = folder(client, "Private")
        headers = {"X-CSRF-Token": csrf(client)}
        path = f"/api/gallery/{operation}"
        assert client.post(path, json={"generation_ids": [image["id"]]}).status_code == 403
        assert client.post(path, headers=headers, json={}).status_code == 422
        assert (
            client.post(
                path, headers=headers, json={"generation_ids": [image["id"], "missing"]}
            ).status_code
            == 404
        )
        assert not client.get(f"/api/generations/{image['id']}").json()["is_favorite"]
        if operation == "download":
            for payload in (
                {"generation_ids": [image["id"]]},
                {"collection_ids": [private_folder["id"]]},
            ):
                response = client.post(path, headers=headers, json=payload)
                assert response.status_code == 409
                assert response.json()["error"]["code"] == "download_empty"
        login_ready_admin(client)
        admin_folder = folder(client, "Admin")
        for payload in (
            {"collection_ids": [admin_folder["id"], private_folder["id"]]},
            {"collection_ids": [admin_folder["id"]], "generation_ids": [image["id"]]},
        ):
            assert (
                client.post(path, headers={"X-CSRF-Token": csrf(client)}, json=payload).status_code
                == 404
            )
        assert not client.get("/api/collections").json()[0]["is_favorite"]
        restore_cookie(client, owner_cookie)
        assert not client.get(f"/api/generations/{image['id']}").json()["is_favorite"]


def test_copies_have_independent_artifacts_and_survive_deleting_original(
    settings_factory, fake_state
):
    del fake_state
    app = create_app(settings_factory(enable_background_worker=True))
    with TestClient(app) as client:
        provision_user(client)
        target = folder(client, "Copies")
        original = create_generation(client, "multi image copy and retain recall")
        detail = wait_for_status(client, original["id"], "succeeded")
        assert detail["image_count"] == 2
        response = transfer(client, "copy", [original["id"]], destination=target["id"])
        assert response.status_code == 200, response.text
        clone_id = response.json()["generation_ids"][0]
        clone = client.get(f"/api/generations/{clone_id}").json()
        assert clone["collection_id"] == target["id"]
        assert clone["image_count"] == detail["image_count"]
        assert clone["effective_controls"] == detail["effective_controls"]
        assert clone["display_artifact"]["id"] != detail["display_artifact"]["id"]
        copied_content = client.get(clone["display_artifact"]["content_url"]).content
        assert copied_content == client.get(detail["display_artifact"]["content_url"]).content
        with app.state.container.db.session_factory() as session:
            original_paths = {
                item.storage_path
                for item in session.scalars(
                    select(Artifact).where(Artifact.generation_id == original["id"])
                )
            }
            copied_paths = {
                item.storage_path
                for item in session.scalars(
                    select(Artifact).where(Artifact.generation_id == clone_id)
                )
            }
            assert original_paths.isdisjoint(copied_paths)
            assert session.get(Generation, clone_id).comfyui_prompt_id is None
        result = client.post(
            "/api/gallery/delete",
            headers={"X-CSRF-Token": csrf(client)},
            json={"generation_ids": [original["id"]]},
        )
        assert result.status_code == 200, result.text
        assert result.json()["items"][0]["status"] == "deleted"
        assert client.get(f"/api/generations/{original['id']}").status_code == 404
        assert client.get(clone["display_artifact"]["content_url"]).content == copied_content
        assert client.get(clone["display_artifact"]["thumbnail_url"]).status_code == 200
        for artifact in clone["artifacts"]:
            assert client.get(artifact["content_url"]).status_code == 200
            if artifact["thumbnail_url"]:
                assert client.get(artifact["thumbnail_url"]).status_code == 200
        assert client.get(f"/api/generations/{clone_id}/recall").status_code == 200


def test_nested_copy_and_delete_normalize_overlapping_selection(settings_factory, fake_state):
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=True))) as client:
        provision_user(client)
        parent = folder(client, "Studies")
        child = folder(client, "Winter", parent["id"])
        target = folder(client, "Archive")
        image = create_generation(client, "nested copy")
        wait_for_status(client, image["id"], "succeeded")
        assert transfer(client, "move", [image["id"]], destination=child["id"]).status_code == 200
        result = transfer(client, "copy", [image["id"]], [parent["id"], child["id"]], target["id"])
        assert result.status_code == 200, result.text
        assert len(result.json()["collection_ids"]) == 1
        assert len(result.json()["generation_ids"]) == 1
        copied_parent = result.json()["collection_ids"][0]
        listed = client.get("/api/collections").json()
        copied_child = next(item for item in listed if item["parent_id"] == copied_parent)
        copied_image = client.get(f"/api/generations/{result.json()['generation_ids'][0]}").json()
        assert copied_child["name"] == "Winter"
        assert copied_image["collection_id"] == copied_child["id"]
        removed = client.post(
            "/api/gallery/delete",
            headers={"X-CSRF-Token": csrf(client)},
            json={"generation_ids": [image["id"]], "collection_ids": [parent["id"], child["id"]]},
        )
        assert removed.status_code == 200, removed.text
        assert len(removed.json()["items"]) == 1
        assert client.get(copied_image["display_artifact"]["content_url"]).status_code == 200


def test_moved_and_copied_folders_append_to_the_destination_order(settings_factory, fake_state):
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=False))) as client:
        provision_user(client)
        destination = folder(client, "Destination")
        resident = folder(client, "Resident", destination["id"])
        first = folder(client, "First")
        second = folder(client, "Second")
        nested_a = folder(client, "Nested A", second["id"])
        nested_b = folder(client, "Nested B", second["id"])

        def children(parent_id):
            return [
                item["id"]
                for item in client.get("/api/collections").json()
                if item["parent_id"] == parent_id
            ]

        moved = transfer(
            client, "move", collections=[first["id"], second["id"]], destination=destination["id"]
        )
        assert moved.status_code == 200, moved.text
        # Selection order decides where the moved folders land, after what was there.
        assert children(destination["id"]) == [resident["id"], first["id"], second["id"]]
        assert children(second["id"]) == [nested_a["id"], nested_b["id"]]

        copied = transfer(client, "copy", collections=[second["id"], first["id"]], destination=None)
        assert copied.status_code == 200, copied.text
        copies = copied.json()["collection_ids"]
        assert children(None) == [destination["id"], *copies]
        # A copied subtree keeps the order it had inside the original.
        listed = client.get("/api/collections").json()
        copied_second = next(
            item["id"] for item in listed if item["id"] in copies and item["name"] == "Second"
        )
        assert [item["name"] for item in listed if item["parent_id"] == copied_second] == [
            "Nested A",
            "Nested B",
        ]

        # Filing a folder back where it already is leaves the chosen order alone.
        again = transfer(client, "move", collections=[first["id"]], destination=destination["id"])
        assert again.status_code == 200, again.text
        assert children(destination["id"]) == [resident["id"], first["id"], second["id"]]


def test_transfer_validates_all_ids_before_moving_and_requires_csrf(settings_factory, fake_state):
    del fake_state
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client)
        image = create_generation(client, "untouched on validation failure")
        target = folder(client, "Target")
        assert (
            transfer(client, "move", [image["id"], "missing"], destination=target["id"]).status_code
            == 404
        )
        assert client.get(f"/api/generations/{image['id']}").json()["collection_id"] is None
        assert (
            client.post(
                "/api/gallery/transfer", json={"operation": "move", "generation_ids": [image["id"]]}
            ).status_code
            == 403
        )
        assert transfer(client, "move").status_code == 422
        assert transfer(client, "copy", [image["id"]]).status_code == 409


def test_bulk_sources_and_destinations_are_owner_scoped_even_for_admin(
    settings_factory, fake_state
):
    del fake_state
    with TestClient(create_app(settings_factory())) as client:
        _, owner_cookie = provision_user(client)
        image = create_generation(client, "private selection")
        private_folder = folder(client, "Private")
        login_ready_admin(client)
        admin_folder = folder(client, "Administrator")
        for operation in ("move", "copy"):
            assert transfer(client, operation, [image["id"]]).status_code == 404
            assert (
                transfer(client, operation, collections=[private_folder["id"]]).status_code == 404
            )
            assert (
                transfer(
                    client,
                    operation,
                    collections=[admin_folder["id"]],
                    destination=private_folder["id"],
                ).status_code
                == 404
            )
        removed = client.post(
            "/api/gallery/delete",
            headers={"X-CSRF-Token": csrf(client)},
            json={"collection_ids": [admin_folder["id"], private_folder["id"]]},
        )
        assert removed.status_code == 404
        assert any(
            item["id"] == admin_folder["id"] for item in client.get("/api/collections").json()
        )
        restore_cookie(client, owner_cookie)
        assert client.get(f"/api/generations/{image['id']}").json()["collection_id"] is None
        assert any(
            item["id"] == private_folder["id"] for item in client.get("/api/collections").json()
        )


@pytest.mark.parametrize("operation", ["move", "copy"])
def test_transfer_rejects_cycles_and_excessive_depth(settings_factory, fake_state, operation):
    del fake_state
    with TestClient(create_app(settings_factory())) as client:
        provision_user(client)
        parent = folder(client, "Parent")
        child = folder(client, "Child", parent["id"])
        assert (
            transfer(
                client, operation, collections=[parent["id"]], destination=child["id"]
            ).status_code
            == 409
        )
        destination = folder(client, "D1")
        for index in range(2, 6):
            destination = folder(client, f"D{index}", destination["id"])
        response = transfer(
            client, operation, collections=[parent["id"]], destination=destination["id"]
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "collection_depth"


def test_failed_copy_rolls_back_rows_and_new_files(settings_factory, fake_state, monkeypatch):
    del fake_state
    app = create_app(settings_factory(enable_background_worker=True))
    with TestClient(app, raise_server_exceptions=False) as client:
        provision_user(client)
        image = create_generation(client, "storage rollback study")
        wait_for_status(client, image["id"], "succeeded")
        second = create_generation(client, "second storage study")
        wait_for_status(client, second["id"], "succeeded")
        assets = app.state.container.assets
        original_store = assets.store_artifact
        before = {path for path in assets.assets_dir.rglob("*") if path.is_file()}
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated full disk")
            return original_store(*args, **kwargs)

        monkeypatch.setattr(assets, "store_artifact", fail_second)
        response = transfer(client, "copy", [image["id"], second["id"]])
        assert response.status_code == 500
        assert {path for path in assets.assets_dir.rglob("*") if path.is_file()} == before
        with app.state.container.db.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Generation)) == 2
