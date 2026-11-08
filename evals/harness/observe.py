"""Look at everything a session produced or sent: events, records, prompts, logs.

Used by the security tests and the graders to answer "does this literal appear anywhere?".
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import structlog
from psycopg import sql

from evals.harness.driver import FlowDriver
from evals.harness.stack import Stack
from mde_core.logging import configure_logging
from mde_flow.testing import InMemoryStore
from mde_prompts.registry import all_specs

MARKER_MIN_CHARS = 60


async def everything(d: FlowDriver) -> str:
    """Every event, record and artifact of the session as one string."""
    parts: list[str] = [e.model_dump_json() for e in await d.events()]
    parts.append((await d.session_record()).model_dump_json())
    parts += [b.model_dump_json() for b in await d.briefs()]
    parts += [s.model_dump_json() for s in await d.specs()]
    parts += [a.model_dump_json() for a in await d.approvals()]
    parts += [j.model_dump_json() for j in await d.jobs()]
    note = await d.research_note()
    if note is not None:
        parts.append(note.model_dump_json())
    return "\n".join(parts)


def provider_text(stack: Stack) -> str:
    """Every prompt the gateway handed to the provider (what a real model would have seen)."""
    return "\n".join(f"{r.system}\n{r.user}" for r in stack.provider.calls)


def llm_record_text(stack: Stack) -> str:
    """The ``LlmCallRecord`` lines the gateway keeps (these go to the ``llm_call`` table)."""
    return "\n".join(r.model_dump_json() for r in stack.calls.records)


def system_prompt_markers(per_spec: int = 3) -> dict[str, list[str]]:
    """Distinctive lines of every call's system prompt (so a leak is detectable by substring)."""
    markers: dict[str, list[str]] = {}
    for call_id, spec in all_specs(missing_ok=True).items():
        text = (spec.template_root / spec.system_template).read_text(encoding="utf-8")
        lines = [
            line.strip()
            for line in text.splitlines()
            if len(line.strip()) >= MARKER_MIN_CHARS and "{{" not in line and "{%" not in line
        ]
        markers[call_id] = lines[:per_spec]
    return markers


@contextmanager
def capture_logs(level: str = "DEBUG") -> Iterator[io.StringIO]:
    """Route structlog and stdlib logging into a buffer (with the product's own redaction)."""
    buffer = io.StringIO()
    configure_logging(level, json=True, stream=buffer)
    try:
        yield buffer
    finally:
        structlog.reset_defaults()
        logging.basicConfig(level=logging.WARNING, force=True)


def dump_memory_store(store: InMemoryStore, *, include_inbound: bool = True) -> str:
    """Every row of the in-memory "database" (values are the JSON the store keeps)."""
    data = store.data
    parts: list[str] = [
        *data.sessions.values(),
        *(v for log in data.outbound.values() for v in log.values()),
        *data.briefs.values(),
        *data.research.values(),
        *data.specs.values(),
        *data.approvals.values(),
        *data.jobs.values(),
    ]
    if include_inbound:
        parts += [v for events in data.inbound.values() for v in events.values()]
    return "\n".join(parts)


def dump_postgres(
    dsn: str, *, skip_tables: tuple[str, ...] = ("alembic_version",)
) -> dict[str, str]:
    """Every row of every table of a (test) database as JSON text, by table name
    (``session_event`` is split into ``session_event:in`` and ``session_event:out``)."""
    out: dict[str, str] = {}
    with psycopg.connect(dsn) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            ).fetchall()
            if row[0] not in skip_tables
        ]
        for table in tables:
            query = sql.SQL("SELECT row_to_json(t)::text FROM {} t").format(sql.Identifier(table))
            rows = [row[0] for row in conn.execute(query).fetchall()]
            if table == "session_event":
                out["session_event:in"] = "\n".join(r for r in rows if '"direction":"in"' in r)
                out["session_event:out"] = "\n".join(r for r in rows if '"direction":"out"' in r)
            else:
                out[table] = "\n".join(rows)
    return out
