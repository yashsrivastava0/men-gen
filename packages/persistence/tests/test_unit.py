"""Unit tests (no database): URL handling, JSON helpers, engine settings, static conformance."""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from mde_contracts.ports import Store, UnitOfWork
from mde_core.settings import Settings
from mde_persistence import db, jsonutil
from mde_persistence.models import ALL_MODELS, Base
from mde_persistence.store import PgUnitOfWork, PostgresStore
from mde_persistence.testing_plugin import _ulid_ms, admin_dsn

# --------------------------------------------------------------------------- urls


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://u:p@h:1/d",
        "postgres://u:p@h:1/d",
        "postgresql+psycopg://u:p@h:1/d",
    ],
)
def test_normalize_url_always_selects_psycopg_3(raw: str) -> None:
    url = db.normalize_url(raw)
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == ("h", 1, "d", "u")
    assert db.normalize_url(url) == url


@pytest.mark.parametrize(
    "raw",
    [
        "sqlite:///x.db",
        "sqlite+aiosqlite:///x.db",
        "mysql://u@h/d",
        "postgresql+asyncpg://u@h/d",
        "postgresql+psycopg2://u@h/d",
    ],
)
def test_normalize_url_refuses_other_databases_and_drivers(raw: str) -> None:
    with pytest.raises(ValueError):
        db.normalize_url(raw)


def test_redact_url_hides_the_password() -> None:
    shown = db.redact_url("postgresql+psycopg://mde:s3cret@127.0.0.1:54329/mde")
    assert "s3cret" not in shown and "mde" in shown and "***" in shown


def test_create_engine_has_a_small_pool_and_uses_settings() -> None:
    settings = Settings(database_url="postgresql://u:p@127.0.0.1:1/unused")
    engine = db.create_engine(settings)
    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == "postgresql+psycopg" and engine.url.database == "unused"
    pool = engine.sync_engine.pool
    assert getattr(pool, "size")() == db.POOL_SIZE == 5  # noqa: B009
    assert getattr(pool, "_max_overflow") == 0  # noqa: B009
    explicit = db.create_engine(settings, url="postgresql://u:p@127.0.0.1:1/other")
    assert explicit.url.database == "other"


def test_selector_loop_policy_is_applied_on_windows() -> None:
    db.ensure_selector_loop_policy()
    if sys.platform == "win32":
        assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy)


# --------------------------------------------------------------------------- json helpers


def test_strip_nul_cleans_strings_keys_and_nested_values() -> None:
    dirty = {"a\x00": ["x\x00y", {"k": "v\x00"}, 3, None, True], "n": 1.5}
    assert jsonutil.strip_nul(dirty) == {"a": ["xy", {"k": "v"}, 3, None, True], "n": 1.5}
    assert jsonutil.clean_text("a\x00b") == "ab" and jsonutil.clean_text(None) is None


def test_utc_normalises_database_timestamps() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 11, 8, 17, 30, tzinfo=ist)
    assert jsonutil.utc(local) == datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    assert jsonutil.utc(local).utcoffset() == timedelta(0)  # type: ignore[union-attr]
    assert jsonutil.utc(datetime(2026, 11, 8, 12, 0)) == datetime(  # noqa: DTZ001
        2026, 11, 8, 12, 0, tzinfo=UTC
    )
    assert jsonutil.utc(None) is None
    assert jsonutil.utc_required(local).tzinfo is not None


# --------------------------------------------------------------------------- schema shape


def test_every_model_is_registered_and_tenant_scoped() -> None:
    tables = {m.__tablename__ for m in ALL_MODELS}
    assert tables == set(Base.metadata.tables)
    global_tables = {"tenant", "llm_call", "research_job", "research_fact"}
    for name, table in Base.metadata.tables.items():
        if name in global_tables - {"research_job", "research_fact"}:
            continue
        assert "tenant_id" in table.c or name == "tenant", f"{name} has no tenant_id"


def test_no_money_or_timestamp_without_the_right_type() -> None:
    llm = Base.metadata.tables["llm_call"]
    assert llm.c.cost_micros.type.python_type is int  # integer micro-dollars, never float
    for table in Base.metadata.tables.values():
        for column in table.c:
            if column.name.endswith("_at") or column.name == "at":
                assert getattr(column.type, "timezone", False), f"{table.name}.{column.name}"


# --------------------------------------------------------------------------- plugin helpers


def test_ulid_timestamp_decoding_for_stale_database_cleanup() -> None:
    from mde_core.ids import new_ulid

    before = int(time.time() * 1000)
    decoded = _ulid_ms(new_ulid().lower())
    assert decoded is not None and before - 10 <= decoded <= int(time.time() * 1000) + 10
    assert _ulid_ms("not-a-ulid") is None and _ulid_ms("u" * 26) is None


def test_admin_dsn_honours_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDE_TEST_PG_URL", "postgresql+psycopg://x:y@db.example:5555/postgres")
    assert admin_dsn() == "postgresql://x:y@db.example:5555/postgres"
    monkeypatch.delenv("MDE_TEST_PG_URL")
    assert "54329" in admin_dsn()  # the private cluster, never 5432


# --------------------------------------------------------------------------- static conformance


def test_postgres_store_satisfies_the_ports() -> None:
    """Checked by pyright: the assignments below only type-check if the protocols are met."""

    def check(store: PostgresStore, uow: PgUnitOfWork) -> tuple[Store, UnitOfWork]:
        return store, uow

    assert callable(check)
