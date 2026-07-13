from __future__ import annotations

from io import BytesIO

import pytest

from app.domain.status import assert_transition
from app.errors import AppError
from app.models import GenerationStatus
from app.services.assets import AssetStore
from tests.fake_services import make_png


def test_status_model_allows_declared_transitions_and_rejects_terminal_mutation() -> None:
    assert_transition(GenerationStatus.QUEUED, GenerationStatus.DISPATCHING)
    assert_transition(GenerationStatus.RUNNING, GenerationStatus.CANCEL_REQUESTED)
    assert_transition(GenerationStatus.CANCEL_REQUESTED, GenerationStatus.SUCCEEDED)
    with pytest.raises(AppError) as exc:
        assert_transition(GenerationStatus.SUCCEEDED, GenerationStatus.RUNNING)
    assert exc.value.code == "invalid_status_transition"


def test_asset_store_checks_images_pixels_and_path_traversal(settings_factory) -> None:
    settings = settings_factory(upload_max_pixels=10_000, upload_max_bytes=100_000)
    store = AssetStore(settings)
    saved = store.store_upload(BytesIO(make_png("upload", width=80, height=60)), kind="image")
    assert saved.mime_type == "image/png"
    assert saved.width == 80 and saved.height == 60
    assert store.open(saved.relative_path).is_file()

    with pytest.raises(AppError) as invalid:
        store.store_upload(BytesIO(b"not an image"), kind="image")
    assert invalid.value.code == "upload_invalid"

    with pytest.raises(AppError) as traversal:
        store.open("../outside.txt")
    assert traversal.value.code == "unsafe_path"
