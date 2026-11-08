"""Test helpers for the Compass store (a real package module: pytest's importlib mode forbids
test-to-test imports).

* Ephemeral PostgreSQL databases ``mde_test_compass_<ulid>`` (adapted from
  ``mde_persistence.testing_plugin``): create, migrate with ``store.migrate.upgrade``, drop with
  ``WITH (FORCE)``; stale ones (older than six hours, by the time inside the ULID) are dropped on the
  way in. Tests never touch the dev database. Unreachable PostgreSQL raises ``PostgresUnavailable``;
  the pytest fixtures turn it into a FAILURE, never a skip.
* ``truncate_all``: empties the ``compass_*`` tables only.
* Factories for test data (``TENANT_A``, ``make_session``, ``make_brief``, ...).
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from mde_compass.domain.brief import Brief, FeatureRecord
from mde_compass.domain.cards import Card, CardOption
from mde_compass.domain.changes import ChangeSet, PatchOp
from mde_compass.domain.claims import Chunk, Claim, SourceInfo, Statement
from mde_compass.domain.common import BriefItem, Provenance
from mde_compass.domain.session import Contributor, SessionRecord
from mde_compass.store.migrate import upgrade
from mde_compass.store.models import Base
from mde_compass.store.ports import CallLogEntry
from mde_core.ids import new_id, new_ulid
from mde_persistence.db import ensure_selector_loop_policy, redact_url

DEFAULT_ADMIN_URL = "postgresql://mde:mde@127.0.0.1:54329/postgres"
TEST_DB_PREFIX = "mde_test_compass_"
STALE_AFTER_SECONDS = 6 * 3600
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

TENANT_A = "tnt_compass_a"
TENANT_B = "tnt_compass_b"
USER_A = "usr_compass_a"
USER_B = "usr_compass_b"
T0 = datetime(2026, 11, 9, 9, 0, 0, tzinfo=UTC)

ensure_selector_loop_policy()  # psycopg async needs a selector loop on Windows


class PostgresUnavailable(RuntimeError):
    """The test PostgreSQL server cannot be reached (integration tests never skip)."""


# --------------------------------------------------------------------------- databases


def admin_dsn() -> str:
    """libpq-style DSN of the server, from ``MDE_TEST_PG_URL`` or the private cluster default."""
    raw = os.environ.get("MDE_TEST_PG_URL") or DEFAULT_ADMIN_URL
    return make_url(raw).set(drivername="postgresql").render_as_string(hide_password=False)


def ensure_reachable(dsn: str) -> None:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            pass
    except psycopg.Error as exc:
        raise PostgresUnavailable(
            f"PostgreSQL is not reachable at {redact_url(dsn)} ({type(exc).__name__}). "
            "Integration tests never skip: start it with: just db-up"
        ) from exc


def _ulid_ms(text: str) -> int | None:
    head = text[:10].upper()
    if len(text) != 26 or any(ch not in _CROCKFORD for ch in head):
        return None
    value = 0
    for ch in head:
        value = value * 32 + _CROCKFORD.index(ch)
    return value


def _drop_sql(name: str) -> sql.Composed:
    return sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))


def drop_stale_databases(dsn: str) -> None:
    """Drop ``mde_test_compass_*`` databases of crashed runs (best effort, never fails a run)."""
    now_ms = int(time.time() * 1000)
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
            rows = conn.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE %s", (TEST_DB_PREFIX + "%",)
            ).fetchall()
            for (name,) in rows:
                created = _ulid_ms(name[len(TEST_DB_PREFIX) :])
                if created is not None and now_ms - created > STALE_AFTER_SECONDS * 1000:
                    conn.execute(_drop_sql(name))
    except psycopg.Error:
        pass


def drop_database(dsn: str, name: str) -> None:
    assert name.startswith(TEST_DB_PREFIX), f"refusing to drop {name!r}"
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(_drop_sql(name))


@contextlib.contextmanager
def ephemeral_database(admin: str | None = None, *, migrate: bool = True) -> Iterator[URL]:
    """A fresh database ``mde_test_compass_<ulid>`` (migrated to head unless ``migrate=False``);
    yields its SQLAlchemy URL (``postgresql+psycopg``) and drops it afterwards."""
    server = admin or admin_dsn()
    name = TEST_DB_PREFIX + new_ulid().lower()
    with psycopg.connect(server, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_url(server).set(drivername="postgresql+psycopg", database=name)
    try:
        if migrate:
            upgrade(url)
        yield url
    finally:
        drop_database(server, name)


def truncate_all(url: str | URL) -> None:
    """Empty every Compass table of a test database (synchronous, loop-independent)."""
    parsed = make_url(url) if isinstance(url, str) else url
    dsn = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    tables = sql.SQL(", ").join(sql.Identifier(t.name) for t in Base.metadata.sorted_tables)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(tables))


# --------------------------------------------------------------------------- factories


def make_session(
    *,
    tenant_id: str = TENANT_A,
    user_id: str = USER_A,
    session_id: str | None = None,
    **fields: Any,
) -> SessionRecord:
    """A fresh ``WELCOME`` session record (``ses_<ulid>`` id)."""
    base: dict[str, object] = {
        "id": session_id or new_id("ses"),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "user_label": "Test user",
    }
    base.update(fields)
    return SessionRecord.model_validate(base)


def make_brief(
    version: int = 1, *, summary: str = "A food delivery app for Pune", session_id: str = ""
) -> Brief:
    """A small valid Brief: one Locked idea item, one feature, a glossary-free body."""
    brief = Brief(version=version)
    brief.meta.session_id = session_id
    brief.idea["summary"] = BriefItem(
        value=summary,
        status="confirmed",
        tier="Locked",
        provenance=[Provenance(type="user_text", ref="evt_1", quote=summary)],
        confidence=0.9,
    )
    brief.scope.append(
        FeatureRecord(id="FTR-001", name="Order tracking", intent="see where my order is")
    )
    return brief


def make_card(card_id: str = "Q-001", *, question: str = "How will it make money?") -> Card:
    return Card(
        id=card_id,
        frame="business_model",
        slots=["idea.business_model"],
        type="choose_one",
        question=question,
        options=[
            CardOption(id="A", label="A small commission", recommended=True, reason="common"),
            CardOption(id="B", label="Delivery fees"),
            CardOption(id="C", label="A monthly plan"),
        ],
    )


def make_change_set(
    change_id: str = "CHG-001", *, base_version: int = 1, new_version: int = 2
) -> ChangeSet:
    op = PatchOp(op="set", path="idea.summary", value="Updated summary", old_value="Old summary")
    inverse = PatchOp(op="set", path="idea.summary", value="Old summary")
    return ChangeSet(
        id=change_id,
        base_version=base_version,
        new_version=new_version,
        ops=[op],
        inverse_ops=[inverse],
        source="chat",
        summary="Updated: Your idea > Summary",
    )


def make_source(source_id: str = "SRC-001", *, sha256: str | None = None) -> SourceInfo:
    return SourceInfo(
        id=source_id,
        kind="text",
        name=f"{source_id}.txt",
        sha256=sha256 or (source_id.lower().encode().hex() * 8)[:64],
        bytes=120,
        pages=1,
        coverage=1.0,
    )


def make_chunks(source_id: str = "SRC-001", count: int = 2) -> list[Chunk]:
    return [
        Chunk(
            id=f"{source_id}-C{i}",
            source_id=source_id,
            index=i,
            text=f"chunk {i} of {source_id}",
            pointer=f"page {i + 1}",
        )
        for i in range(count)
    ]


def make_claim(claim_id: str = "CLM-001", *, source_id: str = "SRC-001") -> Claim:
    return Claim(
        id=claim_id,
        source_id=source_id,
        statement=Statement(
            type="requirement", text="Customers can track orders", quote="track their orders"
        ),
        slot_hint="scope.features",
    )


def make_contributor(contributor_id: str = "con_1", *, role: str = "requester") -> Contributor:
    return Contributor.model_validate({"id": contributor_id, "name": "Asha", "role": role})


def make_call(call_id: str = "call_1", *, spec_id: str = "cmp.write_card") -> CallLogEntry:
    return CallLogEntry(
        call_id=call_id,
        spec_id=spec_id,
        spec_version="1",
        tokens_in=100,
        tokens_out=50,
        cost_micros=1200,
        latency_ms=800,
    )
