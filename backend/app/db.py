from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

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
            pool_size=15,
            max_overflow=0,
            pool_timeout=1,
            connect_args={"timeout": 15, "check_same_thread": False},
        )
        self.session_factory = sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=Session,
        )
        self._configure_sqlite()
        self._health_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cif-health")
        self._health_future: Future[bool] | None = None
        self._health_async_future: asyncio.Future[bool] | None = None
        self._metrics_lock = threading.Lock()
        self._checkouts = self._checked_out = self._peak_checked_out = 0
        self._transaction_ms = self._max_transaction_ms = 0.0
        self._configure_metrics()

    def _configure_metrics(self) -> None:
        @event.listens_for(self.engine, "checkout")
        def checkout(_connection: Any, record: Any, _proxy: Any) -> None:
            record.info["checkout_started"] = time.monotonic()
            with self._metrics_lock:
                self._checkouts += 1
                self._checked_out += 1
                self._peak_checked_out = max(self._peak_checked_out, self._checked_out)

        @event.listens_for(self.engine, "checkin")
        def checkin(_connection: Any, record: Any) -> None:
            started = record.info.pop("checkout_started", None)
            if started is None:
                return
            duration = (time.monotonic() - started) * 1000
            with self._metrics_lock:
                self._checked_out -= 1
                self._transaction_ms += duration
                self._max_transaction_ms = max(self._max_transaction_ms, duration)

    def metrics(self) -> dict[str, int | float]:
        with self._metrics_lock:
            return {
                "database_checkouts": self._checkouts,
                "database_checked_out": self._checked_out,
                "database_peak_checked_out": self._peak_checked_out,
                "transaction_ms": round(self._transaction_ms),
                "max_transaction_ms": round(self._max_transaction_ms),
            }

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

    def _readonly_healthcheck(self) -> bool:
        assert self.settings.database_path is not None
        try:
            with closing(
                sqlite3.connect(
                    self.settings.database_path.as_uri() + "?mode=ro", uri=True, timeout=0.5
                )
            ) as connection:
                connection.execute("SELECT version_num FROM alembic_version").fetchone()
            return True
        except (OSError, sqlite3.Error):
            return False

    async def healthcheck_async(self) -> bool:
        # At most one pending probe, even when the storage device is unresponsive.
        if self._health_future is None or self._health_future.done():
            self._health_future = self._health_executor.submit(self._readonly_healthcheck)
            self._health_async_future = None
        if (
            self._health_async_future is None
            or self._health_async_future.get_loop() is not asyncio.get_running_loop()
        ):
            # Reuse the bridge too: timed-out callers must not leave a new
            # completion callback attached to a stalled thread on every probe.
            self._health_async_future = asyncio.wrap_future(self._health_future)
        try:
            return await asyncio.wait_for(asyncio.shield(self._health_async_future), 1)
        except TimeoutError:
            return False

    def close(self) -> None:
        self.engine.dispose()
        self._health_executor.shutdown(wait=False, cancel_futures=True)


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
