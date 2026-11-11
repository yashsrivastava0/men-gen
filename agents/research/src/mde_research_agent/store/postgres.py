"""``PostgresStore``: the Research ``Store`` over SQLAlchemy Core (async, psycopg 3).

Rules kept (the shared contract suite in ``store/contract.py`` checks the behaviour; this docstring says how):

* Every method is ONE short transaction (``engine.begin()``); nothing here spans a model or search call.
* Every run read or update filters by ``tenant_id``; a foreign tenant gets ``None`` / ``NotFound``.
* One active run per ``(tenant_id, program_id)`` is a rule of the DATABASE (partial unique index). ``create_run``
  inserts with ``ON CONFLICT DO NOTHING`` (a conflict never aborts the transaction) and then names the run in the
  way: ``RunActive(<its id>)``.
* ``append_event`` takes the run row's lock (``FOR NO KEY UPDATE``), reads the last ``seq`` and inserts only
  ``last + 1``; concurrent appends queue up on the lock, so exactly one of two writers of the same ``seq`` wins and
  the other gets ``ValueError``.
* ``save_result`` inserts once (``ON CONFLICT DO NOTHING``; a second save is ``ValueError``) and sets
  ``rsch_runs.content_hash`` in the same transaction. The database also refuses UPDATE and DELETE on the table
  (migration 0001), so a committed result cannot change by any path.
* ``wait_for_events`` polls ``last_seq`` with a growing delay (0.1 s up to 1 s) and always returns at the timeout.
* Timestamps are timezone-aware UTC in and out; NUL characters (which PostgreSQL rejects in ``text`` and ``jsonb``)
  are removed from everything written; the password of the database URL never appears in ``repr`` or messages.

Only ``mde_core`` is imported from the foundation packages (the Research agent may not import ``mde_persistence``),
so the few helpers it would have shared (URL normalising, NUL stripping, UTC) live here.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Row, Table, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

from mde_core.clock import Clock, SystemClock
from mde_core.errors import Conflict, NotFound
from mde_research_agent.domain.events import Event
from mde_research_agent.store import models as m
from mde_research_agent.store.ports import ACTIVE_STATUSES, Decision, RunActive, RunRow

POOL_SIZE = 5
DRIVER = "postgresql+psycopg"
POLL_START_SECONDS = 0.1
POLL_MAX_SECONDS = 1.0
POLL_GROWTH = 1.6
INTERRUPTED_REASON = "server restarted"
RUN_FIELDS = tuple(RunRow.model_fields)
RUN_DATETIMES = ("created_at", "started_at", "finished_at")
CREATE_ATTEMPTS = 3


# --------------------------------------------------------------------------- helpers


def normalize_url(url: str | URL) -> URL:
    """A SQLAlchemy URL using the psycopg 3 driver; PostgreSQL only (never SQLite, never another driver)."""
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
    """psycopg's async mode cannot run on Windows' default ``ProactorEventLoop`` (a no-op elsewhere)."""
    if sys.platform == "win32" and sys.version_info < (3, 14):  # pragma: no cover
        policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy is not None and not isinstance(asyncio.get_event_loop_policy(), policy):
            asyncio.set_event_loop_policy(policy())


def create_store_engine(url: str | URL, *, echo: bool = False) -> AsyncEngine:
    """The async engine of the Research store (psycopg 3, pool of 5, no overflow, pre-ping)."""
    ensure_selector_loop_policy()
    return _create_async_engine(
        normalize_url(url),
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=echo,
    )


def clean_json(value: object) -> Any:
    """A JSON-compatible value without NUL characters (PostgreSQL rejects them in ``jsonb`` and ``text``)."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {
            (k.replace("\x00", "") if isinstance(k, str) else k): clean_json(v)
            for k, v in cast("dict[object, object]", value).items()
        }
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in cast("Sequence[object]", value)]
    return value


def utc(value: datetime) -> datetime:
    """Timezone-aware UTC (a naive value is taken as UTC)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def tbl(model: type[m.Base]) -> Table:
    """The ``Table`` behind a declarative model (Core statements are used for all DML)."""
    return cast(Table, model.__table__)


RUNS = tbl(m.RunModel)
EVENTS = tbl(m.EventModel)
RESULTS = tbl(m.ResultModel)
DECISIONS = tbl(m.DecisionModel)


def _constraint(exc: IntegrityError) -> str | None:
    """The name of the constraint or unique index an ``IntegrityError`` is about."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def _run_value(name: str, value: Any) -> Any:
    if name in RUN_DATETIMES:
        return None if value is None else utc(value)
    return clean_json(value)


def _run_from(row: Row[Any]) -> RunRow:
    data = {name: _run_value(name, row._mapping[name]) for name in RUN_FIELDS}
    return RunRow(**data)


def _active_where(tenant_id: str, program_id: str) -> tuple[Any, ...]:
    return (
        RUNS.c.tenant_id == tenant_id,
        RUNS.c.program_id == program_id,
        RUNS.c.status.in_(ACTIVE_STATUSES),
    )


# --------------------------------------------------------------------------- the store


class PostgresStore:
    """``Store`` over an ``AsyncEngine`` (see ``create_store_engine``).

    ``clock`` supplies the storage timestamps (``created_at`` of events and results); inject a ``FakeClock`` in
    tests. ``aclose`` disposes the engine's pool (disposing twice is harmless).
    """

    def __init__(self, engine: AsyncEngine, *, clock: Clock | None = None) -> None:
        self._engine = engine
        self._clock: Clock = clock or SystemClock()

    def __repr__(self) -> str:
        return f"PostgresStore({redact_url(self._engine.url)})"

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    # --- runs ------------------------------------------------------------------------------------------
    async def create_run(self, row: RunRow) -> None:
        values = {name: _run_value(name, getattr(row, name)) for name in RUN_FIELDS}
        for _ in range(CREATE_ATTEMPTS):
            async with self._engine.begin() as conn:
                stmt = (
                    pg_insert(RUNS)
                    .values(**values)
                    .on_conflict_do_nothing()
                    .returning(RUNS.c.run_id)
                )
                if (await conn.execute(stmt)).first() is not None:
                    return
                # nothing inserted: the run id exists, or another run of the program is active
                same = select(RUNS.c.run_id).where(RUNS.c.run_id == row.run_id)
                if (await conn.execute(same)).first() is not None:
                    raise Conflict(f"run {row.run_id} already exists")
                active = await self._active_id(conn, row.tenant_id, row.program_id)
                if active is not None:
                    raise RunActive(active)
            # the conflicting run finished between the insert and the look-up: try again
        raise Conflict(f"could not create run {row.run_id}: the program's runs keep changing")

    async def update_run(self, tenant_id: str, run_id: str, **fields: Any) -> None:
        unknown = sorted(set(fields) - set(RUN_FIELDS))
        if unknown:
            raise ValueError(f"unknown run field(s): {', '.join(unknown)}")
        values = {name: _run_value(name, value) for name, value in fields.items()}
        where = (RUNS.c.run_id == run_id, RUNS.c.tenant_id == tenant_id)
        try:
            async with self._engine.begin() as conn:
                stmt = (
                    update(RUNS).where(*where).values(**values).returning(RUNS.c.run_id)
                    if values
                    else select(RUNS.c.run_id).where(*where)
                )
                if (await conn.execute(stmt)).first() is None:
                    raise NotFound(f"run {run_id}")
        except IntegrityError as exc:
            if _constraint(exc) != m.ACTIVE_RUN_INDEX:
                raise
            # the new status would make a second active run of the program
            current = await self.get_run(tenant_id, run_id)
            program = current.program_id if current is not None else ""
            async with self._engine.begin() as conn:
                other = await self._active_id(conn, tenant_id, program)
            raise RunActive(other or "unknown") from None

    async def get_run(self, tenant_id: str, run_id: str) -> RunRow | None:
        stmt = select(*RUNS.c).where(RUNS.c.run_id == run_id, RUNS.c.tenant_id == tenant_id)
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else _run_from(row)

    async def active_run(self, tenant_id: str, program_id: str) -> RunRow | None:
        stmt = (
            select(*RUNS.c)
            .where(*_active_where(tenant_id, program_id))
            .order_by(RUNS.c.created_at.desc(), RUNS.c.run_id.desc())
            .limit(1)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else _run_from(row)

    async def latest_committed(self, tenant_id: str, program_id: str) -> RunRow | None:
        stmt = (
            select(*RUNS.c)
            .join(RESULTS, RESULTS.c.run_id == RUNS.c.run_id)
            .where(RUNS.c.tenant_id == tenant_id, RUNS.c.program_id == program_id)
            .order_by(
                func.coalesce(RUNS.c.finished_at, RUNS.c.created_at).desc(),
                RUNS.c.created_at.desc(),
                RUNS.c.run_id.desc(),
            )
            .limit(1)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else _run_from(row)

    async def mark_interrupted(self) -> int:
        stmt = (
            update(RUNS)
            .where(RUNS.c.status.in_(ACTIVE_STATUSES))
            .values(status="interrupted", reason=INTERRUPTED_REASON)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def _active_id(
        self, conn: AsyncConnection, tenant_id: str, program_id: str
    ) -> str | None:
        stmt = (
            select(RUNS.c.run_id)
            .where(*_active_where(tenant_id, program_id))
            .order_by(RUNS.c.created_at.desc(), RUNS.c.run_id.desc())
            .limit(1)
        )
        return (await conn.execute(stmt)).scalar_one_or_none()

    # --- events ----------------------------------------------------------------------------------------
    async def append_event(self, event: Event) -> None:
        payload = clean_json(event.model_dump(mode="json", by_alias=True))
        lock = (
            select(RUNS.c.run_id)
            .where(RUNS.c.run_id == event.run_id)
            .with_for_update(
                key_share=True
            )  # FOR NO KEY UPDATE: appenders of one run queue up here
        )
        last_stmt = select(func.coalesce(func.max(EVENTS.c.seq), 0)).where(
            EVENTS.c.run_id == event.run_id
        )
        try:
            async with self._engine.begin() as conn:
                if (await conn.execute(lock)).first() is None:
                    raise NotFound(f"run {event.run_id}")
                last = int((await conn.execute(last_stmt)).scalar_one())
                if event.seq != last + 1:
                    raise ValueError(
                        f"events must be contiguous: got {event.seq} after {last} "
                        f"(the first event must have seq 1)"
                    )
                await conn.execute(
                    insert(EVENTS).values(
                        run_id=event.run_id,
                        seq=event.seq,
                        event=payload,
                        created_at=utc(self._clock.now()),
                    )
                )
        except IntegrityError:  # unreachable under the run lock; the primary key is the backstop
            raise ValueError(
                f"event {event.seq} of run {event.run_id} was already recorded"
            ) from None

    async def events_after(self, run_id: str, seq: int, limit: int = 500) -> list[Event]:
        if limit <= 0:
            return []
        stmt = (
            select(EVENTS.c.event)
            .where(EVENTS.c.run_id == run_id, EVENTS.c.seq > seq)
            .order_by(EVENTS.c.seq)
            .limit(limit)
        )
        async with self._engine.begin() as conn:
            rows = (await conn.execute(stmt)).scalars().all()
        return [Event.model_validate(r) for r in rows]

    async def last_seq(self, run_id: str) -> int:
        stmt = select(func.coalesce(func.max(EVENTS.c.seq), 0)).where(EVENTS.c.run_id == run_id)
        async with self._engine.begin() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def wait_for_events(
        self,
        run_id: str,
        after_seq: int,
        timeout: float,  # noqa: ASYNC109  (the Store protocol's signature)
    ) -> None:
        """Return when the run has an event after ``after_seq`` or when ``timeout`` seconds have passed
        (whichever is first). Polls with a delay growing from 0.1 s to 1 s; never hangs."""
        deadline = time.monotonic() + max(0.0, timeout)
        delay = POLL_START_SECONDS
        while True:
            if await self.last_seq(run_id) > after_seq:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * POLL_GROWTH, POLL_MAX_SECONDS)

    # --- results ---------------------------------------------------------------------------------------
    async def save_result(
        self, run_id: str, research: dict[str, Any], evidence: dict[str, Any], content_hash: str
    ) -> None:
        # The UPDATE comes first: it takes the run's row lock (concurrent savers queue up) and proves the run
        # exists. Raising after it rolls the hash back with everything else.
        digest = clean_json(content_hash)
        async with self._engine.begin() as conn:
            marked = update(RUNS).where(RUNS.c.run_id == run_id).values(content_hash=digest)
            if (await conn.execute(marked.returning(RUNS.c.run_id))).first() is None:
                raise NotFound(f"run {run_id}")
            stmt = (
                pg_insert(RESULTS)
                .values(
                    run_id=run_id,
                    research=clean_json(research),
                    evidence=clean_json(evidence),
                    content_hash=digest,
                    created_at=utc(self._clock.now()),
                )
                .on_conflict_do_nothing(index_elements=[RESULTS.c.run_id])
                .returning(RESULTS.c.run_id)
            )
            if (await conn.execute(stmt)).first() is None:
                raise ValueError("a committed result is immutable")

    async def get_result(self, run_id: str) -> dict[str, Any] | None:
        return await self._result_column(run_id, RESULTS.c.research)

    async def get_evidence(self, run_id: str) -> dict[str, Any] | None:
        return await self._result_column(run_id, RESULTS.c.evidence)

    async def _result_column(self, run_id: str, column: Any) -> dict[str, Any] | None:
        stmt = select(column).where(RESULTS.c.run_id == run_id)
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).scalar_one_or_none()

    # --- decisions -------------------------------------------------------------------------------------
    async def add_decision(self, decision: Decision) -> None:
        exists = (
            select(RUNS.c.run_id)
            .where(RUNS.c.run_id == decision.run_id)
            .with_for_update(
                read=True, key_share=True
            )  # FOR KEY SHARE: the run cannot vanish meanwhile
        )
        async with self._engine.begin() as conn:
            if (await conn.execute(exists)).first() is None:
                raise NotFound(f"run {decision.run_id}")
            await conn.execute(
                insert(DECISIONS).values(
                    run_id=decision.run_id,
                    item_id=clean_json(decision.item_id),
                    kind=clean_json(decision.kind),
                    reason=clean_json(decision.reason),
                    at=utc(decision.at),
                )
            )

    async def list_decisions(self, run_id: str) -> list[Decision]:
        stmt = select(*DECISIONS.c).where(DECISIONS.c.run_id == run_id).order_by(DECISIONS.c.id)
        async with self._engine.begin() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            Decision(run_id=r.run_id, item_id=r.item_id, kind=r.kind, reason=r.reason, at=utc(r.at))
            for r in rows
        ]

    async def aclose(self) -> None:
        await self._engine.dispose()
