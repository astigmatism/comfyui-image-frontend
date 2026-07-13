from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config(database_path: Path) -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def test_migration_up_down_up_cycle(settings_factory) -> None:
    settings = settings_factory()
    assert settings.database_path is not None
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    config = _config(settings.database_path)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{settings.database_path}")
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "7c9b2d4e6f81"
    assert {"users", "generations", "artifacts", "workflow_profiles", "favorites"}.issubset(
        set(inspect(engine).get_table_names())
    )

    command.downgrade(config, "base")
    assert "users" not in inspect(engine).get_table_names()

    command.upgrade(config, "head")
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "7c9b2d4e6f81"
    engine.dispose()
