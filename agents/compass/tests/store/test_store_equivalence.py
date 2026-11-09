"""Model-based check: random operation sequences give identical observable results on the in-memory
fake and on PostgreSQL (return values, raised errors, and the final state of every list)."""

from __future__ import annotations

import random
from typing import Any

import pytest

from mde_compass.store import testing as f
from mde_compass.store.memory import InMemoryStore
from mde_compass.store.ports import Store, UnitOfWork
from mde_compass.store.postgres import PostgresStore
from mde_core.clock import FakeClock
from mde_core.errors import MdeError

pytestmark = pytest.mark.integration

A, B = f.TENANT_A, f.TENANT_B
SESSIONS = [f"ses_model_{i}" for i in range(3)]
STATES = ["INTERVIEWING", "PLAYBACK", "PAUSED", "ARCHIVED", "BRIEF_REVIEW"]
OUTCOMES = ["answered", "skipped", "you_decide", "back"]
WORDS = ["pune", "Thali", "menu", "रेस्टोरेंट", "order", "delivery", "100%"]
QA_STATUSES = ["open", "answered", "skipped", "you_decide", "assumed", "superseded", "dropped"]
FACT_STATUSES = ["active", "superseded", "disputed", "not_used"]
FACT_ORIGINS = ["stated", "tap", "assumption", "document"]
MODES = ["initial", "followup", "replan", "deepen", "simplify"]
MEMORY_KINDS = [
    "qa_upsert",
    "qa_upsert",
    "qa_get",
    "qa_list",
    "fact_add",
    "fact_add",
    "fact_update",
    "fact_update",
    "fact_get",
    "fact_list",
    "fact_next",
    "summary_put",
    "summary_get",
    "batch_add",
    "batch_add",
    "batch_find",
    "batch_list",
    "batch_next",
]
PER_SESSION = 10  # entries per session in the final-state dump


def _ops(seed: int, count: int = 360) -> list[tuple[Any, ...]]:
    rng = random.Random(seed)
    ops: list[tuple[Any, ...]] = [("create", i) for i in range(len(SESSIONS))]
    for _ in range(count):
        s = rng.randrange(len(SESSIONS) + 1)  # the last index is an unknown session
        kind = rng.choice(
            [
                "inbound",
                "inbound",
                "outbound",
                "outbound",
                "brief",
                "brief",
                "baseline",
                "ask",
                "answer",
                "spend",
                "state",
                "begin_turn",
                "end_turn",
                "advance",
                "history",
                "list_outbound",
                "list_versions",
                "get_brief",
                "questions",
                "foreign",
                *MEMORY_KINDS,
            ]
        )
        w = rng.choice(WORDS)
        ops.append(
            (
                kind,
                s,
                rng.randrange(1, 5),
                rng.choice(OUTCOMES),
                w,
                rng.choice(STATES),
                rng.choice([None, "B-G1", "B-G2"]),
                rng.randrange(0, 200),
                rng.randrange(1, 6),
            )
        )
    return ops


def _sid(index: int) -> str:
    return SESSIONS[index] if index < len(SESSIONS) else "ses_model_unknown"


def _dump(value: Any) -> Any:
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


async def _run(uow: UnitOfWork, store: Store, op: tuple[Any, ...]) -> Any:
    kind, s, n, outcome, word, state, label, number, limit = (*op, *([None] * 9))[:9]
    if kind == "create":
        return _dump(await uow.sessions.create(f.make_session(session_id=SESSIONS[s])))
    sid = _sid(s)
    if kind == "inbound":
        return await uow.events.record_inbound(sid, f"e{n}", "user_message", {"text": word})
    if kind == "outbound":
        return await uow.events.append_outbound(
            sid, "assistant_message", {"text": word}, turn_id=f"e{n}"
        )
    if kind == "brief":
        return _dump(await uow.briefs.save_version(sid, f.make_brief(n, summary=f"s{number}")))
    if kind == "baseline":
        return _dump(await uow.briefs.mark_baseline(sid, n, label or "B-G1"))
    if kind == "ask":
        return await uow.questions.record_asked(
            sid, f.make_card(f"Q-{n:03d}", question=word), {"n": n}
        )
    if kind == "answer":
        return _dump(await uow.questions.record_answer(sid, f"Q-{n:03d}", outcome, {"v": word}))
    if kind == "spend":
        return await uow.sessions.add_spend(sid, number, calls=n)
    if kind == "state":
        return _dump(await uow.sessions.set_state(sid, state))
    if kind == "begin_turn":
        return await uow.sessions.try_begin_turn(sid)
    if kind == "end_turn":
        return await uow.sessions.end_turn(sid)
    if kind == "advance":
        store.clock.advance(float(number))  # type: ignore[attr-defined]
        return None
    if kind == "history":
        page = await uow.events.list_history(
            sid,
            after_idx=number % 7,
            limit=limit,
            q=word if n % 2 else None,
            turn_ids=[f"e{n}", f"e{n + 1}"] if number % 3 == 0 else None,
        )
        return _dump(page)
    if kind == "list_outbound":
        return _dump(await uow.events.list_outbound(sid, after_seq=number % 5, limit=limit))
    if kind == "list_versions":
        return _dump(await uow.briefs.list_versions(sid))
    if kind == "get_brief":
        return _dump(await uow.briefs.get(sid, None if number % 2 else n))
    if kind == "questions":
        return _dump(await uow.questions.list(sid))
    if kind in MEMORY_KINDS:
        return await _run_memory(uow, kind, sid, n, word, number, limit)
    # "foreign": the same session seen from another tenant
    raise AssertionError("handled below")


async def _run_memory(
    uow: UnitOfWork, kind: str, sid: str, n: int, word: str, number: int, limit: int
) -> Any:
    """The Interview Memory operations: ids collide on purpose so Conflict and NotFound paths are hit."""
    card_id, fact_id, batch_id = f"Q-{n:03d}", f"FCT-{n:03d}", f"BAT-{n:03d}"
    mode = MODES[number % len(MODES)]
    if kind == "qa_upsert":
        record = f.make_qa(
            card_id,
            1 + number % 3,
            status=QA_STATUSES[number % len(QA_STATUSES)],
            batch_id=f"BAT-{limit:03d}" if number % 4 else None,
            mode=mode if number % 2 else None,
            answer={"v": word, "n": number} if number % 3 else None,
            custom_text=word if number % 5 == 0 else None,
            input_mode=["tap", "text", "voice"][number % 3],
        )
        return await uow.qa.upsert(sid, record)
    if kind == "qa_get":
        return _dump(await uow.qa.get(sid, card_id, None if number % 2 else 1 + number % 3))
    if kind == "qa_list":
        return _dump(
            await uow.qa.list(
                sid,
                status=QA_STATUSES[number % len(QA_STATUSES)] if number % 3 == 0 else None,  # type: ignore[arg-type]
                batch_id=f"BAT-{limit:03d}" if number % 2 else None,
                latest_only=number % 4 != 0,
            )
        )
    if kind == "fact_add":
        chosen = await uow.facts.next_id(sid) if number % 2 else fact_id
        fact = f.make_fact(
            chosen,
            statement=f"{word} fact {number}",
            quote=word,
            status=FACT_STATUSES[number % len(FACT_STATUSES)],
            origin=FACT_ORIGINS[number % len(FACT_ORIGINS)],
            confidence=(number % 11) / 10,
            topics=[word],
        )
        return await uow.facts.add(sid, fact)
    if kind == "fact_update":
        current = await uow.facts.get(sid, fact_id)
        changed = current.model_copy(
            update={
                "status": FACT_STATUSES[number % len(FACT_STATUSES)],
                "confidence": (number % 7) / 10,
                "topics": [word, f"t{n}"],
                # now and then try to rewrite what was said: a Conflict on both stores
                "statement": word if number % 6 == 0 else current.statement,
            }
        )
        return await uow.facts.update(sid, changed)
    if kind == "fact_get":
        return _dump(await uow.facts.get(sid, fact_id))
    if kind == "fact_list":
        status = FACT_STATUSES[number % len(FACT_STATUSES)] if number % 2 else None
        return _dump(await uow.facts.list(sid, status=status))  # type: ignore[arg-type]
    if kind == "fact_next":
        return await uow.facts.next_id(sid)
    if kind == "summary_put":
        summary = f.make_summary(n, through_fact_id=f"FCT-{number:03d}")
        return await uow.summaries.put(
            sid, summary if number % 3 else summary.model_copy(update={"topics": []})
        )
    if kind == "summary_get":
        return _dump(await uow.summaries.get(sid))
    if kind == "batch_add":
        chosen = await uow.batches.next_id(sid) if number % 2 else batch_id
        record = f.make_batch(
            chosen,
            mode=mode,
            inputs_hash=f"h{limit}",
            used_fallback=number % 5 == 0,
            latency_ms=number,
            cost_micros=number * 1_000_000_000,
            output={"word": word, "n": number},
        )
        return await uow.batches.add(sid, record)
    if kind == "batch_find":
        return _dump(await uow.batches.find(sid, mode, f"h{limit}"))
    if kind == "batch_list":
        return _dump(await uow.batches.list(sid))
    return await uow.batches.next_id(sid)  # "batch_next"


async def _replay(store: Store, ops: list[tuple[Any, ...]]) -> list[Any]:
    results: list[Any] = []
    for op in ops:
        try:
            if op[0] == "foreign":
                async with store.begin(B) as uow:
                    results.append(_dump(await uow.sessions.get(_sid(op[1]))))
                continue
            async with store.begin(A) as uow:
                results.append(await _run(uow, store, op))
        except MdeError as exc:
            results.append(f"error:{type(exc).__name__}")
    async with store.begin(A) as uow:  # the final state of everything
        for sid in SESSIONS:
            results.append(_dump(await uow.sessions.get(sid)))
            results.append(_dump((await uow.events.list_history(sid, limit=500)).items))
            results.append(_dump(await uow.events.list_outbound(sid, limit=500)))
            results.append(_dump(await uow.briefs.list_versions(sid)))
            results.append(_dump(await uow.questions.list(sid)))
            results.append(_dump(await uow.qa.list(sid, latest_only=False)))
            results.append(_dump(await uow.facts.list(sid)))
            results.append(_dump(await uow.summaries.get(sid)))
            results.append(_dump(await uow.batches.list(sid)))
            results.append(await uow.facts.next_id(sid))
    return results


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
async def test_memory_and_postgres_agree(seed: int, make_postgres_store: Any) -> None:
    ops = _ops(seed)
    memory = InMemoryStore(FakeClock())
    postgres: PostgresStore = make_postgres_store(clock=FakeClock())
    expected = await _replay(memory, ops)
    actual = await _replay(postgres, ops)
    assert len(expected) == len(actual)
    for step, (want, got) in enumerate(zip(expected, actual, strict=True)):
        assert want == got, f"seed {seed}: step {step} differs: {want!r} != {got!r}"
    # the run was not trivial: it hit successes and errors
    assert any(isinstance(r, str) and r.startswith("error:") for r in expected)
    assert any(r is True for r in expected)
    # ... and the Interview Memory ended up with real content in at least one session of each kind
    finals = [expected[-PER_SESSION * len(SESSIONS) + i :: PER_SESSION] for i in range(PER_SESSION)]
    for index in (5, 6, 8):  # qa, facts, batches
        assert any(finals[index]), f"seed {seed}: nothing stored at final position {index}"
