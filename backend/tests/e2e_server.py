from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import uvicorn
from app.config import Settings
from app.main import create_app

from tests.fake_services import LiveFakeServer
from tests.publication_fixtures import build_publication_bundle


def main() -> None:
    primary = LiveFakeServer()
    worker = LiveFakeServer()
    primary.state.workflow_files.update(build_publication_bundle("image").files)
    primary.state.workflow_files.update(build_publication_bundle("moody").files)
    primary.state.slow_stage_delay = 2.0
    primary.start()
    worker.start()
    configured_data = os.getenv("CIF_E2E_DATA_DIR")
    data_dir = (
        Path(configured_data) if configured_data else Path(tempfile.mkdtemp(prefix="cif-e2e-"))
    )
    remove_data = configured_data is None
    try:
        root = Path(__file__).resolve().parents[2]
        settings = Settings(
            app_title="E2E Image Appliance",
            data_dir=data_dir,
            database_path=data_dir / "app.db",
            session_secret="e2e-session-secret-material-0123456789abcdef",
            bootstrap_admin_username="admin",
            bootstrap_admin_temporary_password="E2EAdminTemporary123!",
            comfyui_instances=[
                {
                    "id": "default",
                    "label": "Original · RTX 3090",
                    "description": "24 GB VRAM",
                    "base_url": primary.base_url,
                    "ws_url": primary.ws_url,
                },
                {
                    "id": "worker-2",
                    "label": "Worker 1 · RTX 3080",
                    "description": "10 GB VRAM",
                    "base_url": worker.base_url,
                    "ws_url": worker.ws_url,
                },
            ],
            comfyui_default_instance_id="default",
            comfyui_workflow_directory="workflows",
            ollama_base_url=primary.base_url,
            speech_to_text_url=f"{primary.base_url}/v1/audio/transcriptions",
            speech_to_text_api_key="e2e-whisper-secret",
            frontend_dist=root / "frontend" / "dist",
            dispatch_poll_seconds=0.02,
            external_health_interval_seconds=0.05,
            reconciliation_grace_seconds=0.05,
            log_level="WARNING",
        )
        uvicorn.run(
            create_app(settings),
            host="127.0.0.1",
            port=int(os.getenv("CIF_E2E_PORT", "8765")),
            log_config=None,
            timeout_graceful_shutdown=settings.graceful_shutdown_timeout_seconds,
        )
    finally:
        worker.stop()
        primary.stop()
        if remove_data:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
