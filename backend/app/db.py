from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings

_MIGRATION_LOCK = threading.Lock()


class Database:
    """SQLite database wrapper tuned for a small concurrent home appliance."""

    def __init__(self, settings: Settings):
        self.settings = settings
        assert settings.database_path is not None
        self.engine: Engine = create_engine(
            f"sqlite:///{settings.database_path}",
            pool_pre_ping=True,
            connect_args={"timeout": 15, "check_same_thread": False},
        )
        self.session_factory = sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=Session,
        )
        self._configure_sqlite()

    def _configure_sqlite(self) -> None:
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session

    def healthcheck(self) -> bool:
        try:
            with self.session_factory() as session:
                session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def close(self) -> None:
        self.engine.dispose()


def _run_alembic(settings: Settings) -> None:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    assert settings.database_path is not None
    config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.database_path}")
    with _MIGRATION_LOCK:
        command.upgrade(config, "head")


async def run_migrations(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_run_alembic, settings)
