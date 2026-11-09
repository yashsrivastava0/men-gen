"""Programmatic Alembic access for the Compass schema (tests, tooling, dev start-up).

``uv run python -m mde_compass.store.migrate`` upgrades the dev database (``DATABASE_URL`` from the
settings; default: the private cluster on port 54329) to head. The configuration is built in code, so no
``alembic.ini`` and no working-directory assumption are needed. The version table is
``compass_alembic_version``, so this history is independent of ``mde_persistence``'s.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL

from mde_persistence.db import normalize_url, redact_url

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


def main(argv: Sequence[str] | None = None) -> int:
    from mde_core.settings import load_settings

    parser = argparse.ArgumentParser(description="Migrate the Compass tables (compass_*).")
    parser.add_argument("action", nargs="?", choices=("upgrade", "downgrade"), default="upgrade")
    parser.add_argument("revision", nargs="?", default=None)
    args = parser.parse_args(argv)
    url = load_settings().database_url
    if args.action == "downgrade":
        downgrade(url, args.revision or "-1")
    else:
        upgrade(url, args.revision or "head")
    print(f"compass migrations: {args.action} done on {redact_url(url)}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
