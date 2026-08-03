from pathlib import Path

from app.main import create_app
from fastapi.testclient import TestClient
from tests.fake_services import FakeServiceState


def test_frontend_revalidates_entrypoints_and_immutably_caches_versioned_assets(
    settings_factory,  # type: ignore[no-untyped-def]
    fake_state: FakeServiceState,
    tmp_path: Path,
) -> None:
    del fake_state
    fingerprint = "a" * 64
    frontend = tmp_path / "frontend"
    versioned_assets = frontend / "assets" / fingerprint
    versioned_assets.mkdir(parents=True)
    app_path = f"/assets/{fingerprint}/app.mjs"
    style_path = f"/assets/{fingerprint}/styles.css"
    (frontend / "index.html").write_text(
        (
            "<!doctype html><html><head>"
            f'<link rel="stylesheet" href="{style_path}">'
            f'</head><body><script type="module" src="{app_path}"></script></body></html>'
        ),
        encoding="utf8",
    )
    (frontend / "build.json").write_text(f'{{"asset_version":"{fingerprint}"}}', encoding="utf8")
    (versioned_assets / "app.mjs").write_text("export const version = 2;\n", encoding="utf8")
    (versioned_assets / "styles.css").write_text("body { color: white; }\n", encoding="utf8")
    (frontend / "assets" / "legacy.mjs").write_text("export const version = 1;\n", encoding="utf8")

    with TestClient(create_app(settings_factory(frontend_dist=frontend))) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert index.headers["cache-control"] == "no-cache, must-revalidate"
        assert app_path in index.text
        revalidated_index = client.get("/", headers={"If-None-Match": index.headers["etag"]})
        assert revalidated_index.status_code == 304
        assert revalidated_index.headers["cache-control"] == "no-cache, must-revalidate"

        build = client.get("/build.json")
        assert build.status_code == 200
        assert build.headers["cache-control"] == "no-cache, must-revalidate"

        app = client.get(app_path)
        assert app.status_code == 200
        assert app.headers["cache-control"] == "public, max-age=31536000, immutable"
        revalidated_app = client.get(app_path, headers={"If-None-Match": app.headers["etag"]})
        assert revalidated_app.status_code == 304
        assert revalidated_app.headers["cache-control"] == "public, max-age=31536000, immutable"

        legacy = client.get("/assets/legacy.mjs")
        assert legacy.status_code == 200
        assert legacy.headers["cache-control"] == "no-cache, must-revalidate"
        missing = client.get(f"/assets/{'b' * 64}/missing.mjs")
        assert missing.status_code == 404
        assert missing.headers["cache-control"] == "no-cache, must-revalidate"

        health = client.get("/api/health")
        assert "cache-control" not in health.headers
