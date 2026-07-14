from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from app.models import Base
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    # Migrations run inside the application lifespan. Do not disable application/Uvicorn
    # loggers while temporarily applying Alembic's own logging configuration.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # SQLite cannot batch-rebuild a referenced parent table while immediate foreign-key
        # enforcement is enabled. Migrations run on this dedicated connection with checks off,
        # then validate the finished schema/data before runtime connections re-enable them.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
        # SQLite DDL is non-transactional, but Alembic's version-row write
        # participates in SQLAlchemy's implicit transaction. Commit it
        # explicitly so subsequent appliance starts do not replay the schema.
        connection.commit()
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("Database migration left foreign-key violations.")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
