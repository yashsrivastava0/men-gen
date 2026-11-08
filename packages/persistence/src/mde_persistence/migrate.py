"""Programmatic Alembic access (tests, tooling, dev start-up). The CLI is ``just migrate``."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL

from mde_persistence.db import normalize_url

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str | URL) -> Config:
    """An Alembic ``Config`` for ``url`` that does not depend on ``alembic.ini`` or the CWD."""
    rendered = normalize_url(url).render_as_string(hide_password=False)
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade(url: str | URL, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str | URL, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)
