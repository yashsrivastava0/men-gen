"""Alembic environment.

Synchronous on purpose: the same psycopg 3 driver the runtime uses, without an event loop inside
Alembic (so it also works when called from tests). The URL comes from, in order:

1. ``config.attributes["connection"]`` (an open connection; used by tests),
2. the ``sqlalchemy.url`` option set programmatically (tests, tooling),
3. ``mde_core.settings`` (``DATABASE_URL``; default is the private dev cluster on port 54329).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from mde_core.settings import load_settings
from mde_persistence.db import normalize_url
from mde_persistence.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    chosen = explicit or load_settings().database_url
    return normalize_url(chosen).render_as_string(hide_password=False)


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        literal_binds=connection is None,
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )


def run_migrations_offline() -> None:
    _configure(url=_url())
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _configure(connection=supplied)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            _configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
