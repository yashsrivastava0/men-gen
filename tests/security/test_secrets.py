"""Pasted secrets never survive (spec section 9, hard rule 1): slots, events, prompts, the spec, logs
and every database-bound record.

A user pastes keys, tokens, passwords and connection strings into the chat, into an upload and
into a gate comment. After the whole session (to hand-off) none of those literals may appear in:
the slot table, any outbound event, any Brief, Plan, spec or approval, any prompt sent to the
provider, any ``llm_call`` record, any log line, the hand-off repository, or any stored row.

BUG-7 (pinned ``xfail`` below): the inbound-event journal (``session_event``, direction ``in``)
stores the event exactly as the user sent it, before Discover redacts it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.driver import FlowDriver
from evals.harness.observe import (
    capture_logs,
    dump_memory_store,
    dump_postgres,
    everything,
    llm_record_text,
    provider_text,
)
from evals.harness.stack import NetworkGuard, Stack, StackOptions, StoreFactory, build_stack
from mde_core.redact import find_secrets
from mde_flow.testing import InMemoryStore

FAKE_KEY = "sk-test-FAKEFAKEFAKEFAKEFAKEFAKEFAKE0123456789"  # secret-scan: allow

#: label -> the literal the user pastes (all fake; each is matched by mde_core.redact patterns)
SECRETS: dict[str, str] = {
    "openai": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",  # secret-scan: allow
    "aws": "AKIAIOSFODNN7EXAMPLE",  # secret-scan: allow
    "github": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",  # secret-scan: allow
    "password": "Tr0ub4dor&3xyz-Q9",  # secret-scan: allow
    "connection": "S3cretPw99zz",  # secret-scan: allow  (the password part of the URL below)
    "bearer": "abcdefghijklmnopqrstuvwxyz0123456789",  # secret-scan: allow
}

MESSAGES: list[str] = [
    f"online shop, my key is {SECRETS['openai']} please use it",  # secret-scan: allow
    f"aws access key {SECRETS['aws']} for the images",  # secret-scan: allow
    f"github token {SECRETS['github']} for the repo",  # secret-scan: allow
    f"admin password = {SECRETS['password']} for the dashboard",  # secret-scan: allow
    f"database is postgresql://shopadmin:{SECRETS['connection']}@db.internal:5432/shop",  # secret-scan: allow
    f"call the api with Authorization: Bearer {SECRETS['bearer']} please",  # secret-scan: allow
]
UPLOAD = f"Shop notes. Our payment key is {SECRETS['openai']}. We sell candles online."  # secret-scan: allow
COMMENT = f"also remember the token {SECRETS['github']} for later"  # secret-scan: allow


async def _session_with_secrets(stack: Stack) -> FlowDriver:
    d = FlowDriver.for_user(stack)
    await d.start()
    for text in MESSAGES:
        await d.say(text)
    await d.upload("notes.md", UPLOAD)
    await d.say("finish with smart defaults")
    await d.decide("brief")
    await d.run_plan()
    await d.say(COMMENT)  # a note during Gate 2 (a change request path)
    await d.run_plan()
    return d


def _where(secret_free: dict[str, str]) -> list[str]:
    hits: list[str] = []
    for label, value in SECRETS.items():
        for where, text in secret_free.items():
            if value in text:
                hits.append(f"{label} in {where}")  # never print the value itself
    return hits


@pytest.mark.e2e
async def test_pasted_secrets_never_reach_any_output_prompt_record_or_log(
    tmp_path: Path, guard: NetworkGuard
) -> None:
    with capture_logs() as logs:
        stack = build_stack(tmp_path / "projects", StackOptions(api_key=FAKE_KEY))
        d = await _session_with_secrets(stack)
        snap = await d.snapshot()
        if snap.state == "PLAN_REVIEW":
            await d.decide("plan")
    inner = stack.store.inner
    assert isinstance(inner, InMemoryStore)
    spec_text = (await d.specs())[-1].spec_text
    rec = await d.session_record()
    secret_free = {
        "an event, record, Brief, spec, approval or job": await everything(d),
        "a prompt sent to the provider": provider_text(stack),
        "an llm_call record": llm_record_text(stack),
        "a stored row": dump_memory_store(inner, include_inbound=False),
        "the slot table": rec.discovery.model_dump_json(),
        "a log line": logs.getvalue(),
        "APP_SPEC.md": spec_text,
    }
    assert logs.getvalue(), "no log was captured: the log check would be vacuous"
    assert _where(secret_free) == []
    assert FAKE_KEY not in "\n".join(secret_free.values())
    # the redaction really happened (it is not that nothing was said)
    assert "[REDACTED]" in rec.discovery.model_dump_json()
    # and the file itself would not pass a secret scan
    assert find_secrets(spec_text) == []
    repo_files = [
        p for p in (tmp_path / "projects").rglob("*") if p.is_file() and ".git" not in p.parts
    ]
    assert repo_files, "the hand-off wrote nothing"
    for path in repo_files:
        body = path.read_text(encoding="utf-8")
        assert _where({str(path.name): body}) == []


async def test_the_inbound_event_journal_never_holds_a_pasted_secret(
    tmp_path: Path, guard: NetworkGuard
) -> None:
    stack = build_stack(tmp_path / "projects", StackOptions())
    d = FlowDriver.for_user(stack)
    await d.start()
    for text in MESSAGES:
        await d.say(text)
    await d.upload("notes.md", UPLOAD)
    inner = stack.store.inner
    assert isinstance(inner, InMemoryStore)
    inbound = "\n".join(v for events in inner.data.inbound.values() for v in events.values())
    assert inbound, "nothing was journalled: the check would be vacuous"
    assert _where({"the inbound journal": inbound}) == []


@pytest.mark.integration
async def test_pasted_secrets_never_reach_a_postgres_row_except_the_inbound_journal(
    tmp_path: Path, guard: NetworkGuard, postgres_store_factory: StoreFactory, pg_dsn: str
) -> None:
    stack = build_stack(
        tmp_path / "projects", StackOptions(store_factory=postgres_store_factory, api_key=FAKE_KEY)
    )
    d = await _session_with_secrets(stack)
    snap = await d.snapshot()
    if snap.state == "PLAN_REVIEW":
        await d.decide("plan")
    tables = dump_postgres(pg_dsn)
    assert tables["session_event:out"] and tables["session"] and tables["spec_version"]
    stored = {t: text for t, text in tables.items() if t != "session_event:in"}
    assert _where(stored) == []
    assert FAKE_KEY not in "\n".join(tables.values())


@pytest.mark.integration
async def test_postgres_inbound_journal_never_holds_a_pasted_secret(
    tmp_path: Path, guard: NetworkGuard, postgres_store_factory: StoreFactory, pg_dsn: str
) -> None:
    stack = build_stack(tmp_path / "projects", StackOptions(store_factory=postgres_store_factory))
    d = FlowDriver.for_user(stack)
    await d.start()
    for text in MESSAGES:
        await d.say(text)
    inbound = dump_postgres(pg_dsn)["session_event:in"]
    assert inbound, "nothing was journalled: the check would be vacuous"
    assert _where({"the inbound journal": inbound}) == []
