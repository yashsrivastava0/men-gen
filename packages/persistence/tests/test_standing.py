"""Memory items, Org Profile, policies and the LLM call sink."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mde_contracts.gateway import LlmCallRecord, LlmCallSink, Usage
from mde_contracts.policy import Policy
from mde_core.clock import FakeClock
from mde_core.errors import TenantViolation, ValidationFailed
from mde_persistence import testing_factories as f
from mde_persistence.llm_calls import PgLlmCallSink
from mde_persistence.store import PostgresStore

pytestmark = pytest.mark.integration

A, B = f.TENANT_A, f.TENANT_B


async def _rows(engine: AsyncEngine, sql: str, **params: object) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql), params)]


# --------------------------------------------------------------------------- memory


async def test_memory_items_are_scoped_by_tenant_scope_and_owner(store: PostgresStore) -> None:
    async with store.begin(A) as uow:
        a = await uow.memory.put(
            "user", "last_style", {"direction": "clean-bright"}, owner_id="usr_1"
        )
        await uow.memory.put("user", "last_style", {"direction": "bold"}, owner_id="usr_2")
        await uow.memory.put("org", "brand", {"name": "Acme", "colors": ["#fff", "#000"]})
        got = await uow.memory.get("user", "last_style", owner_id="usr_1")
        assert got is not None and got == a and got.value == {"direction": "clean-bright"}
        assert got.created_at.tzinfo is not None
        updated = await uow.memory.put(
            "user", "last_style", {"direction": "calm"}, owner_id="usr_1"
        )
        assert updated.value == {"direction": "calm"} and updated.created_at == a.created_at
        listed = await uow.memory.list("user", owner_id="usr_1")
        assert [m.key for m in listed] == ["last_style"]
        assert [m.key for m in await uow.memory.list("org")] == ["brand"]
        assert await uow.memory.get("org", "missing") is None
        assert await uow.memory.delete("user", "last_style", owner_id="usr_1") is True
        assert await uow.memory.delete("user", "last_style", owner_id="usr_1") is False
        assert await uow.memory.get("user", "last_style", owner_id="usr_2") is not None
    async with store.begin(B) as uow:  # another tenant sees nothing
        assert await uow.memory.get("org", "brand") is None
        assert await uow.memory.list("org") == []
        assert await uow.memory.get("user", "last_style", owner_id="usr_2") is None
        assert await uow.memory.delete("user", "last_style", owner_id="usr_2") is False


async def test_memory_owner_rules(store: PostgresStore) -> None:
    async with store.begin(A) as uow:
        with pytest.raises(ValidationFailed):
            await uow.memory.put("org", "k", {}, owner_id="usr_1")
        with pytest.raises(ValidationFailed):
            await uow.memory.put("user", "k", {})
        with pytest.raises(ValidationFailed):
            await uow.memory.get("user", "k")


# --------------------------------------------------------------------------- org profile


async def test_org_profile_is_one_document_per_tenant(store: PostgresStore) -> None:
    async with store.begin(A) as uow:
        assert await uow.org_profiles.get() is None
        saved = await uow.org_profiles.put(
            {"brand": {"name": "Acme"}, "approvers": ["security\x00"]}
        )
        assert saved == {"brand": {"name": "Acme"}, "approvers": ["security"]}
        replaced = await uow.org_profiles.put({"brand": {"name": "Acme Corp"}})
        assert replaced == {"brand": {"name": "Acme Corp"}}
        assert await uow.org_profiles.get() == replaced
    async with store.begin(B) as uow:
        assert await uow.org_profiles.get() is None
        await uow.org_profiles.put({"brand": {"name": "Other"}})
    async with store.begin(A) as uow:
        assert await uow.org_profiles.get() == {"brand": {"name": "Acme Corp"}}


# --------------------------------------------------------------------------- policies


async def test_policies_are_stored_per_tenant(store: PostgresStore) -> None:
    strict = Policy(id="strict", org="acme", mode="enterprise", banned_libraries=["moment"])
    async with store.begin(A) as uow:
        assert await uow.policies.get("strict") is None
        assert await uow.policies.put(strict) == strict
        await uow.policies.put(Policy(id="guided-default"))
        edited = strict.model_copy(update={"banned_libraries": ["moment", "lodash"]})
        await uow.policies.put(edited)
        assert await uow.policies.get("strict") == edited
        assert [p.id for p in await uow.policies.list()] == ["guided-default", "strict"]
    async with store.begin(B) as uow:
        assert await uow.policies.get("strict") is None
        assert await uow.policies.list() == []
        await uow.policies.put(Policy(id="strict", org="other"))
    async with store.begin(A) as uow:
        got = await uow.policies.get("strict")
        assert got is not None and got.org == "acme"


async def test_standing_repositories_refuse_system_scope(store: PostgresStore) -> None:
    async with store.begin(None) as uow:
        with pytest.raises(TenantViolation):
            await uow.memory.list("org")
        with pytest.raises(TenantViolation):
            await uow.org_profiles.get()
        with pytest.raises(TenantViolation):
            await uow.policies.list()


# --------------------------------------------------------------------------- llm_call sink


def _call(call_id: str, **overrides: object) -> LlmCallRecord:
    base: dict[str, Any] = {
        "call_id": call_id,
        "request_id": "req_1",
        "session_id": "ses_1",
        "tenant_id": A,
        "scope": "session:ses_1",
        "spec_id": "C1.understand",
        "spec_version": 2,
        "model": "gpt-test",
        "mode": "mock",
        "status": "ok",
        "usage": Usage(
            input_tokens=120,
            cached_input_tokens=20,
            output_tokens=40,
            reasoning_tokens=10,
            tool_calls=1,
            cost_micros=1234,
        ),
        "latency_ms": 87,
        "prompt_hash": "abc123",
    }
    base.update(overrides)
    return LlmCallRecord.model_validate(base)


async def test_sink_writes_one_row_per_call_without_prompt_text(
    pg_engine: AsyncEngine, fake_clock: FakeClock
) -> None:
    sink: LlmCallSink = PgLlmCallSink(pg_engine, clock=fake_clock)
    await sink.record(_call("call_1", repaired=True, cassette_key="k1"))
    await sink.record(
        _call(
            "call_2",
            session_id=None,
            tenant_id=None,
            scope="eval:run1",
            status="failed",
            error_code="provider_error",
        )
    )
    await sink.record(_call("call_3", at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC)))
    rows = {r["call_id"]: r for r in await _rows(pg_engine, "SELECT * FROM llm_call")}
    assert set(rows) == {"call_1", "call_2", "call_3"}
    one = rows["call_1"]
    assert (one["input_tokens"], one["cached_input_tokens"], one["output_tokens"]) == (120, 20, 40)
    assert (one["reasoning_tokens"], one["tool_calls"], one["cost_micros"]) == (10, 1, 1234)
    assert (
        one["repaired"] is True and one["cassette_key"] == "k1" and one["prompt_hash"] == "abc123"
    )
    assert one["at"] == fake_clock.now() and one["session_id"] == "ses_1" and one["tenant_id"] == A
    assert rows["call_2"]["session_id"] is None and rows["call_2"]["error_code"] == "provider_error"
    assert rows["call_3"]["at"] == datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    columns = set(one)
    assert not {"prompt", "prompt_text", "messages", "output", "text"} & columns


async def test_sink_is_idempotent_by_call_id(pg_engine: AsyncEngine) -> None:
    sink = PgLlmCallSink(pg_engine)
    await sink.record(_call("call_dup"))
    await sink.record(_call("call_dup", model="changed"))  # a replayed record changes nothing
    rows = await _rows(pg_engine, "SELECT model FROM llm_call WHERE call_id = 'call_dup'")
    assert rows == [{"model": "gpt-test"}]
