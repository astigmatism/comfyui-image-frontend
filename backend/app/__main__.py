from __future__ import annotations

import uvicorn

from .config import get_settings
from .main import create_app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
        timeout_graceful_shutdown=settings.graceful_shutdown_timeout_seconds,
    )


if __name__ == "__main__":
    main()
