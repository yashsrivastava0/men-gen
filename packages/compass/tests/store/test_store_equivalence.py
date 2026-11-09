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


def _ops(seed: int, count: int = 220) -> list[tuple[Any, ...]]:
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
    # "foreign": the same session seen from another tenant
    raise AssertionError("handled below")


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
