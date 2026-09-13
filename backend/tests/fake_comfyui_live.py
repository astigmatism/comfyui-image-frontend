"""Standalone deterministic fake ComfyUI/Ollama/speech service for the TLS edge.

The loopback Playwright suite runs the application in-process against two
:class:`tests.fake_services.LiveFakeServer` instances started by
``backend/tests/e2e_server.py``. The TLS-edge suite instead brings up the real
Compose stack (see ``frontend/e2e/tls-edge.overlay.yml``), whose application
container reaches a *separate* container that runs this script. It publishes the
exact same deterministic fixture catalog so both suites present identical
published sources, and it binds only to the container's network namespace
(Compose never publishes the port).

Mirrors ``e2e_server.py``'s ``FakeServiceState`` seeding so the source picker,
prompt-assistant, and speech surfaces behave identically to the loopback run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the backend package importable regardless of the working directory or
# the image-level PYTHONPATH (the production image sets PYTHONPATH=/app/backend,
# but this also runs under a plain repository checkout).
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import uvicorn  # noqa: E402

from tests.fake_services import FakeServiceState, create_fake_services_app  # noqa: E402
from tests.publication_fixtures import build_publication_bundle  # noqa: E402


def main() -> None:
    # FakeServiceState() seeds the default catalog (build_workflow_files ->
    # krea + generic), which already contains "Generic Landscape". The two
    # explicit bundles below match e2e_server.py's primary fake exactly.
    state = FakeServiceState()
    state.workflow_files.update(build_publication_bundle("image").files)
    state.workflow_files.update(build_publication_bundle("moody").files)
    state.slow_stage_delay = 2.0
    state.stage_delay_overrides["slow cancellation sample"] = 10.0

    app = create_fake_services_app(state)
    port = int(os.getenv("CIF_E2E_FAKE_COMFYUI_PORT", "8188"))
    # Bind to 0.0.0.0 only so the *container* can reach the fake over the
    # Compose network; Compose does not publish this port to the host.
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 - container-internal, never host-published
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
