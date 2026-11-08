"""``PgLlmCallSink``: mirrors the gateway's per-call records into the ``llm_call`` table.

Implements ``mde_contracts.gateway.LlmCallSink``. Records carry no prompt text (only the hash).
Writes are idempotent by ``call_id`` and use their own short transaction, so a failure here can
never roll back the caller's unit of work.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from mde_contracts.gateway import LlmCallRecord
from mde_core.clock import Clock, SystemClock
from mde_persistence.jsonutil import clean_text, utc
from mde_persistence.models import LlmCallRow
from mde_persistence.repositories.base import tbl

LLM_CALL = tbl(LlmCallRow)


class PgLlmCallSink:
    def __init__(self, engine: AsyncEngine, *, clock: Clock | None = None) -> None:
        self._engine = engine
        self._clock = clock or SystemClock()

    async def record(self, record: LlmCallRecord) -> None:
        usage = record.usage
        stmt = (
            pg_insert(LLM_CALL)
            .values(
                call_id=record.call_id,
                request_id=record.request_id,
                session_id=record.session_id,
                tenant_id=record.tenant_id,
                scope=record.scope,
                spec_id=record.spec_id,
                spec_version=record.spec_version,
                model=record.model,
                mode=record.mode,
                status=record.status,
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                tool_calls=usage.tool_calls,
                cost_micros=usage.cost_micros,
                latency_ms=record.latency_ms,
                repaired=record.repaired,
                cache_hit=record.cache_hit,
                cassette_key=record.cassette_key,
                prompt_hash=record.prompt_hash,
                error_code=clean_text(record.error_code),
                at=utc(record.at) or self._clock.now(),
            )
            .on_conflict_do_nothing(index_elements=[LLM_CALL.c.call_id])
        )
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            await session.execute(stmt)
            await session.commit()
