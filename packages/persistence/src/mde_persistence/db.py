"""Engine factory and URL helpers.

One driver everywhere: psycopg 3 (``postgresql+psycopg://``), for the async runtime and for the
synchronous Alembic ``env.py``. The pool is deliberately small (five connections, no overflow):
the machine and the private cluster are shared, and every unit of work holds one connection.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

from mde_core.settings import Settings, load_settings

POOL_SIZE = 5
DRIVER = "postgresql+psycopg"


def normalize_url(url: str | URL) -> URL:
    """Return a SQLAlchemy URL using the psycopg 3 driver.

    Accepts ``postgres://``, ``postgresql://`` and ``postgresql+psycopg://``; refuses anything else
    (in particular SQLite and other drivers: PostgreSQL only).
    """
    parsed = make_url(url) if isinstance(url, str) else url
    backend, _, driver = parsed.drivername.partition("+")
    if backend not in ("postgresql", "postgres"):
        raise ValueError(f"PostgreSQL only; got database backend {backend!r}")
    if driver not in ("", "psycopg"):
        raise ValueError(f"unsupported PostgreSQL driver {driver!r}; use psycopg 3")
    return parsed.set(drivername=DRIVER)


def redact_url(url: str | URL) -> str:
    """The URL with the password masked: safe for logs and error messages."""
    parsed = make_url(url) if isinstance(url, str) else url
    return parsed.render_as_string(hide_password=True)


def ensure_selector_loop_policy() -> None:
    """psycopg's async mode cannot run on Windows' default ``ProactorEventLoop``.

    Call this once at process start (before the loop exists) on Windows; it is a no-op elsewhere.
    The pytest plugin and ``create_engine`` call it, so tests and CLIs need nothing extra.
    """
    if sys.platform == "win32" and sys.version_info < (3, 14):
        policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy is not None and not isinstance(asyncio.get_event_loop_policy(), policy):
            asyncio.set_event_loop_policy(policy())


def create_engine(
    settings: Settings | None = None, *, url: str | URL | None = None, echo: bool = False
) -> AsyncEngine:
    """Create the async engine (pool of 5, no overflow, pre-ping).

    ``url`` wins over ``settings.database_url``; with neither, settings are loaded from the
    environment (``DATABASE_URL``).
    """
    ensure_selector_loop_policy()
    chosen = url if url is not None else (settings or load_settings()).database_url
    return _create_async_engine(
        normalize_url(chosen),
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=echo,
    )
