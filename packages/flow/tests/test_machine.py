"""State machine: every row of the transition table and every illegal (state, transition) pair."""

from __future__ import annotations

import ast
import itertools
from collections import deque
from pathlib import Path
from typing import get_args

import pytest

from mde_contracts.gates import PlanPhase, RunOrigin, SessionState, TransitionName
from mde_contracts.records import SessionRecord
from mde_core.errors import InvalidTransition
from mde_flow import machine
from mde_flow.machine import (
    ALL_STATES,
    ALL_TRANSITIONS,
    TABLE,
    FlowTransition,
    MachineState,
    advance_phase,
    apply,
    apply_to_record,
    legal_transitions,
)


def ms(
    state: SessionState,
    *,
    phase: PlanPhase | None = None,
    origin: RunOrigin | None = None,
    paused_from: SessionState | None = None,
) -> MachineState:
    return MachineState(state, phase, origin, paused_from)


def test_every_contract_transition_name_is_in_the_table() -> None:
    assert set(get_args(TransitionName)) <= set(ALL_TRANSITIONS)
    assert set(TABLE) == set(ALL_TRANSITIONS)


# --------------------------------------------------------------------------- the table rows


def test_brief_complete() -> None:
    assert apply(ms("DISCOVERING"), "brief_complete") == ms("BRIEF_REVIEW")


def test_approve_brief_starts_research_with_origin_brief_review() -> None:
    out = apply(ms("BRIEF_REVIEW"), "approve_brief")
    assert out == ms("PLANNING", phase="research", origin="BRIEF_REVIEW")


def test_approve_brief_always_starts_at_research() -> None:
    with pytest.raises(InvalidTransition):
        apply(ms("BRIEF_REVIEW"), "approve_brief", start_phase="build")


def test_change_brief() -> None:
    assert apply(ms("BRIEF_REVIEW"), "change_brief") == ms("DISCOVERING")


@pytest.mark.parametrize("origin", ["BRIEF_REVIEW", "PLAN_REVIEW", "HANDED_OFF"])
@pytest.mark.parametrize("phase", ["research", "build"])
def test_plan_ready_heavy_or_normal_stops_at_plan_review(
    origin: RunOrigin, phase: PlanPhase
) -> None:
    out = apply(ms("PLANNING", phase=phase, origin=origin), "plan_ready")
    assert out == ms("PLAN_REVIEW", origin=origin)


@pytest.mark.parametrize("phase", ["research", "build"])
def test_plan_ready_light_after_handoff_returns_to_handed_off(phase: PlanPhase) -> None:
    out = apply(ms("PLANNING", phase=phase, origin="HANDED_OFF"), "plan_ready", light=True)
    assert out == ms("HANDED_OFF", origin="HANDED_OFF")


@pytest.mark.parametrize("origin", ["BRIEF_REVIEW", "PLAN_REVIEW"])
def test_light_plan_ready_needs_a_post_handoff_run(origin: RunOrigin) -> None:
    with pytest.raises(InvalidTransition):
        apply(ms("PLANNING", phase="build", origin=origin), "plan_ready", light=True)


def test_light_only_applies_to_plan_ready() -> None:
    with pytest.raises(InvalidTransition):
        apply(ms("DISCOVERING"), "brief_complete", light=True)


def test_needs_answer_suspends_the_run_and_answer_given_resumes_it() -> None:
    planning = ms("PLANNING", phase="build", origin="PLAN_REVIEW")
    suspended = apply(planning, "needs_answer")
    assert suspended == ms("DISCOVERING", phase="build", origin="PLAN_REVIEW")
    assert machine.awaiting_answer(suspended)
    assert apply(suspended, "answer_given") == planning


def test_answer_given_may_override_the_resume_phase() -> None:
    suspended = ms("DISCOVERING", phase="build", origin="BRIEF_REVIEW")
    assert apply(suspended, "answer_given", start_phase="research").plan_phase == "research"


def test_answer_given_without_a_suspended_run_is_illegal() -> None:
    with pytest.raises(InvalidTransition):
        apply(ms("DISCOVERING"), "answer_given")


def test_rediscover_cancels_the_run_for_good() -> None:
    out = apply(ms("PLANNING", phase="research", origin="BRIEF_REVIEW"), "rediscover")
    assert out.state == "DISCOVERING" and out.plan_phase is None
    assert not machine.awaiting_answer(out)


def test_rediscover_also_drops_a_suspended_run() -> None:
    suspended = ms("DISCOVERING", phase="build", origin="PLAN_REVIEW")
    out = apply(suspended, "rediscover")
    assert out.state == "DISCOVERING" and out.plan_phase is None
    assert "rediscover" in legal_transitions(suspended)
    plain = ms("DISCOVERING")
    assert "rediscover" not in legal_transitions(plain)
    with pytest.raises(InvalidTransition):
        apply(plain, "rediscover")


@pytest.mark.parametrize("origin", ["BRIEF_REVIEW", "PLAN_REVIEW", "HANDED_OFF"])
@pytest.mark.parametrize("phase", ["research", "build"])
def test_stop_returns_to_origin(origin: RunOrigin, phase: PlanPhase) -> None:
    out = apply(ms("PLANNING", phase=phase, origin=origin), "stop")
    assert out.state == origin
    assert out.plan_phase is None


def test_approve_plan_change_plan_and_post_handoff_change() -> None:
    assert apply(ms("PLAN_REVIEW", origin="BRIEF_REVIEW"), "approve_plan").state == "HANDED_OFF"
    assert apply(ms("PLAN_REVIEW"), "change_plan", start_phase="build") == ms(
        "PLANNING", phase="build", origin="PLAN_REVIEW"
    )
    assert apply(ms("PLAN_REVIEW"), "change_plan").plan_phase == "research"
    assert apply(ms("HANDED_OFF"), "change_after_handoff", start_phase="build") == ms(
        "PLANNING", phase="build", origin="HANDED_OFF"
    )


@pytest.mark.parametrize("state", sorted(machine.PAUSABLE))
def test_pause_and_resume_round_trip(state: SessionState) -> None:
    start = ms(state)
    paused = apply(start, "pause")
    assert paused == ms("PAUSED", paused_from=state)
    assert apply(paused, "resume") == start


def test_pause_keeps_a_suspended_run() -> None:
    suspended = ms("DISCOVERING", phase="build", origin="PLAN_REVIEW")
    assert apply(apply(suspended, "pause"), "resume") == suspended


@pytest.mark.parametrize("state", [s for s in ALL_STATES if s != "ARCHIVED"])
def test_archive_from_everywhere_but_archived(state: SessionState) -> None:
    if state == "PLANNING":
        start = ms(state, phase="build", origin="BRIEF_REVIEW")
    elif state == "PAUSED":
        start = ms(state, paused_from="DISCOVERING")
    else:
        start = ms(state)
    assert apply(start, "archive").state == "ARCHIVED"


# --------------------------------------------------------------------------- every pair


def _representative(state: SessionState) -> MachineState:
    if state == "PLANNING":
        return ms(state, phase="build", origin="PLAN_REVIEW")
    if state == "PAUSED":
        return ms(state, paused_from="BRIEF_REVIEW")
    if state == "DISCOVERING":
        return ms(state, phase="build", origin="BRIEF_REVIEW")  # also covers answer_given
    return ms(state)


@pytest.mark.parametrize(
    ("state", "name"), list(itertools.product(ALL_STATES, ALL_TRANSITIONS)), ids=str
)
def test_every_state_transition_pair_matches_the_table(
    state: SessionState, name: FlowTransition
) -> None:
    start = _representative(state)
    legal = state in TABLE[name].sources
    kwargs: dict[str, object] = {}
    if name in {"change_plan", "change_after_handoff"}:
        kwargs["start_phase"] = "build"
    if not legal:
        with pytest.raises(InvalidTransition):
            apply(start, name)  # type: ignore[arg-type]
        assert name not in legal_transitions(start)
        return
    out = apply(start, name, **kwargs)  # type: ignore[arg-type]
    assert out.state in TABLE[name].targets
    machine.check(out)
    assert name in legal_transitions(start)


def test_archived_is_terminal_and_stopped_only_archives() -> None:
    assert legal_transitions(ms("ARCHIVED")) == frozenset()
    assert legal_transitions(ms("STOPPED")) == frozenset({"archive"})


def test_stop_without_a_known_origin_lands_in_stopped() -> None:
    legacy = MachineState("PLANNING", "build", None)
    out = apply(legacy, "stop")
    assert out.state == "STOPPED" and out.plan_phase is None
    with pytest.raises(InvalidTransition):
        apply(legacy, "plan_ready")


def test_planning_cannot_be_paused() -> None:
    with pytest.raises(InvalidTransition):
        apply(ms("PLANNING", phase="research", origin="BRIEF_REVIEW"), "pause")


def test_unknown_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransition):
        apply(ms("DISCOVERING"), "teleport")  # type: ignore[arg-type]


def test_start_phase_is_rejected_where_it_does_not_apply() -> None:
    with pytest.raises(InvalidTransition):
        apply(ms("DISCOVERING"), "brief_complete", start_phase="build")


def test_advance_phase_only_from_research() -> None:
    out = advance_phase(ms("PLANNING", phase="research", origin="BRIEF_REVIEW"))
    assert out.plan_phase == "build"
    with pytest.raises(InvalidTransition):
        advance_phase(out)
    with pytest.raises(InvalidTransition):
        advance_phase(ms("PLAN_REVIEW"))


@pytest.mark.parametrize(
    "bad",
    [
        MachineState("PLANNING", None, "BRIEF_REVIEW"),
        MachineState("PLANNING", None, None),
        MachineState("PAUSED", None, None, None),
        MachineState("PAUSED", None, None, "PLANNING"),
        MachineState("PLAN_REVIEW", "build", "PLAN_REVIEW"),
        MachineState("BRIEF_REVIEW", None, None, "DISCOVERING"),
    ],
)
def test_check_rejects_corrupt_states(bad: MachineState) -> None:
    with pytest.raises(InvalidTransition):
        machine.check(bad)


def test_reachable_machine_states_all_satisfy_the_invariants() -> None:
    """Breadth-first walk of every state reachable from a fresh session."""
    start = ms("DISCOVERING")
    seen = {start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        machine.check(cur)
        for name in legal_transitions(cur):
            variants: list[dict[str, object]] = [{}]
            if name in {"change_plan", "change_after_handoff", "answer_given"}:
                variants = [{"start_phase": "research"}, {"start_phase": "build"}]
            if name == "plan_ready":
                variants = [{}, {"light": True}] if cur.origin == "HANDED_OFF" else [{}]
            for kw in variants:
                nxt = apply(cur, name, **kw)  # type: ignore[arg-type]
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
    assert {s.state for s in seen} == set(ALL_STATES) - {"STOPPED"}


# --------------------------------------------------------------------------- the record adapters


def test_apply_to_record_writes_all_four_fields() -> None:
    rec = SessionRecord(id="ses_x", tenant_id="t", owner_id="u", app_id="app_x")
    rec = apply_to_record(rec, "brief_complete")
    rec = apply_to_record(rec, "approve_brief")
    assert (rec.state, rec.plan_phase, rec.origin) == ("PLANNING", "research", "BRIEF_REVIEW")
    rec = machine.advance_phase_record(rec)
    assert rec.plan_phase == "build"
    rec = apply_to_record(rec, "stop")
    assert (rec.state, rec.plan_phase, rec.origin) == ("BRIEF_REVIEW", None, "BRIEF_REVIEW")
    rec = apply_to_record(rec, "pause")
    assert rec.paused_from == "BRIEF_REVIEW"
    rec = apply_to_record(rec, "resume")
    assert rec.state == "BRIEF_REVIEW" and rec.paused_from is None


# --------------------------------------------------------------------------- static guard

_FORBIDDEN_KEYS = {"state", "plan_phase", "origin", "paused_from"}
_SESSION_ONLY_KEYS = {"plan_phase", "paused_from"}  # no other record has these fields


def _src_files() -> list[Path]:
    root = Path(machine.__file__).parent
    return [p for p in root.rglob("*.py") if p.name != "machine.py"]


def _receiver(func: ast.expr) -> str:
    return ast.unparse(func.value) if isinstance(func, ast.Attribute) else ""


def test_no_state_writes_outside_machine() -> None:
    """Hard rule 6: only ``machine`` writes state/plan_phase/origin/paused_from on a record.

    ``state`` and ``origin`` also exist on other models (a job's state, the discovery state), so
    they are only flagged on a receiver that looks like a session record.
    """
    offenders: list[str] = []
    for path in _src_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "model_copy":
                recv = _receiver(func)
                for kw in node.keywords:
                    if kw.arg != "update":
                        continue
                    if not isinstance(kw.value, ast.Dict):
                        offenders.append(f"{path.name}:{node.lineno} model_copy(update=<dynamic>)")
                        continue
                    keys = {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
                    session_like = "rec" in recv or "session" in recv
                    if keys & _SESSION_ONLY_KEYS or (session_like and keys & _FORBIDDEN_KEYS):
                        offenders.append(f"{path.name}:{node.lineno} model_copy on {recv or '?'}")
            if name == "SessionRecord":
                bad = [kw.arg for kw in node.keywords if kw.arg in _FORBIDDEN_KEYS]
                if bad:
                    offenders.append(f"{path.name}:{node.lineno} SessionRecord({bad})")
    assert offenders == [], offenders
