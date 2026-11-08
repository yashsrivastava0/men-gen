"""Alembic environment for the Compass tables.

Synchronous on purpose (psycopg 3, NullPool): no event loop inside Alembic, so it also works when
called from tests. Only ``compass_*`` objects are managed: ``include_object`` hides every other table
(for example ``mde_persistence``'s) from autogenerate and drift checks, and the version table is
``compass_alembic_version``. The URL comes from, in order: ``config.attributes["connection"]`` (tests),
the ``sqlalchemy.url`` option set programmatically, ``mde_core.settings`` (``DATABASE_URL``).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from mde_compass.store.models import VERSION_TABLE, Base, include_object
from mde_core.settings import load_settings
from mde_persistence.db import normalize_url

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
        version_table=VERSION_TABLE,
        include_object=include_object,
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
