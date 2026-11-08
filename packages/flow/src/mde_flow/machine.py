"""The session state machine: pure functions and one explicit transition table (spec 2.1).

This module is the **only** place in the codebase that computes a session's ``state``,
``plan_phase``, ``origin`` or ``paused_from`` (hard rule 6: no state change outside the flow
controller, and inside it only through :func:`apply` / :func:`apply_to_record`). A static test
(``test_no_state_writes_outside_machine``) scans the package to keep it that way.

States (spec 2.1)::

    DISCOVERING  --brief_complete--> BRIEF_REVIEW        (Gate 1 is open in BRIEF_REVIEW)
    BRIEF_REVIEW --approve_brief---> PLANNING (research) | --change_brief--> DISCOVERING
    PLANNING     --plan_ready------> PLAN_REVIEW         (origin BRIEF_REVIEW / PLAN_REVIEW /
                                                          heavy HANDED_OFF)
                 --plan_ready------> HANDED_OFF          (light post-handoff change: no Gate 2)
                 --needs_answer----> DISCOVERING         (run suspended, resumes via answer_given)
                 --stop-----------> origin               (STOPPED only if origin is unknown)
    PLAN_REVIEW  --approve_plan----> HANDED_OFF | --change_plan--> PLANNING     (Gate 2)
    HANDED_OFF   --change_after_handoff--> PLANNING
    pause / resume / archive: side states PAUSED, ARCHIVED (and STOPPED, the defensive fallback)

Two transitions go beyond the names listed in ``mde_contracts.gates.TransitionName`` and are
logged in the flow docs: ``answer_given`` (the user answered a ``needs_answer`` question, the
suspended run resumes) and ``rediscover`` (a message changed the app type or ``scope.apps``
during PLANNING, or while a ``needs_answer`` question waits: the run is dropped and Discover
restarts at that card).

Run bookkeeping carried on the state:

* ``origin`` is where the *current or last* PLANNING run started. It is set when a run starts and
  is what ``stop`` returns to. A PLANNING record without an origin (legacy data) can only be
  stopped, and ``stop`` then lands in STOPPED.
* ``plan_phase`` is set only while a run is active: in PLANNING, or in DISCOVERING while a
  ``needs_answer`` question is pending (the suspended-run marker), or while PAUSED from there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, get_args

from mde_contracts.gates import PlanPhase, RunOrigin, SessionState
from mde_contracts.records import SessionRecord
from mde_core.errors import InvalidTransition

FlowTransition = Literal[
    "brief_complete",
    "approve_brief",
    "change_brief",
    "plan_ready",
    "needs_answer",
    "stop",
    "approve_plan",
    "change_plan",
    "change_after_handoff",
    "pause",
    "resume",
    "archive",
    # flow-only additions (see module docstring)
    "answer_given",
    "rediscover",
]

ALL_STATES: Final[tuple[SessionState, ...]] = get_args(SessionState)
ALL_TRANSITIONS: Final[tuple[FlowTransition, ...]] = get_args(FlowTransition)

#: States from which a conversation can be paused (a running PLANNING is stopped, not paused).
PAUSABLE: Final[frozenset[SessionState]] = frozenset(
    {"DISCOVERING", "BRIEF_REVIEW", "PLAN_REVIEW", "HANDED_OFF"}
)
REVIEW_STATES: Final[frozenset[SessionState]] = frozenset({"BRIEF_REVIEW", "PLAN_REVIEW"})
_ORIGINS: Final[tuple[str, ...]] = get_args(RunOrigin)


@dataclass(frozen=True, slots=True)
class MachineState:
    """Everything the machine needs to decide the next state."""

    state: SessionState = "DISCOVERING"
    plan_phase: PlanPhase | None = None
    origin: RunOrigin | None = None
    paused_from: SessionState | None = None


@dataclass(frozen=True, slots=True)
class Row:
    """One row of the transition table."""

    name: FlowTransition
    sources: frozenset[SessionState]
    targets: frozenset[SessionState]  # every state this transition can lead to
    doc: str


def _row(
    name: FlowTransition, sources: set[SessionState], targets: set[SessionState], doc: str
) -> Row:
    return Row(name, frozenset(sources), frozenset(targets), doc)


#: The explicit transition table. ``apply`` refuses everything that is not a row, and refuses a
#: source state that is not listed. Tests walk every (state, transition) pair against this table.
TABLE: Final[dict[FlowTransition, Row]] = {
    r.name: r
    for r in (
        _row(
            "brief_complete",
            {"DISCOVERING"},
            {"BRIEF_REVIEW"},
            "No required slot is left: compile the Brief and open Gate 1.",
        ),
        _row(
            "approve_brief",
            {"BRIEF_REVIEW"},
            {"PLANNING"},
            "Gate 1 approved by every required role on the same hash: start the run (research).",
        ),
        _row(
            "change_brief",
            {"BRIEF_REVIEW"},
            {"DISCOVERING"},
            "Change something at Gate 1: re-enter the loop for the affected slot.",
        ),
        _row(
            "plan_ready",
            {"PLANNING"},
            {"PLAN_REVIEW", "HANDED_OFF"},
            "The build finished. Light post-handoff change: back to HANDED_OFF without Gate 2; "
            "everything else stops at PLAN_REVIEW (Gate 2).",
        ),
        _row(
            "needs_answer",
            {"PLANNING"},
            {"DISCOVERING"},
            "The builder needs one plain answer: back to Discover for that slot, run suspended.",
        ),
        _row(
            "answer_given",
            {"DISCOVERING"},
            {"PLANNING"},
            "The suspended run's question was answered: resume the run (re-run the failing writer).",
        ),
        _row(
            "rediscover",
            {"PLANNING", "DISCOVERING"},
            {"DISCOVERING"},
            "App type or scope.apps changed during the run (or while its question waits): "
            "drop the run and re-enter Discover.",
        ),
        _row(
            "stop",
            {"PLANNING"},
            {"BRIEF_REVIEW", "PLAN_REVIEW", "HANDED_OFF", "STOPPED"},
            "Stop cancels the run and returns to where it started (STOPPED if origin is unknown).",
        ),
        _row(
            "approve_plan",
            {"PLAN_REVIEW"},
            {"HANDED_OFF"},
            "Gate 2 approved by every required role on the same hash: hand off.",
        ),
        _row(
            "change_plan",
            {"PLAN_REVIEW"},
            {"PLANNING"},
            "Change something at Gate 2: patch, only the affected writers re-run.",
        ),
        _row(
            "change_after_handoff",
            {"HANDED_OFF"},
            {"PLANNING"},
            "A change after hand-off: a new version is built (light or heavy, decided at plan_ready).",
        ),
        _row(
            "pause",
            set(PAUSABLE),
            {"PAUSED"},
            "Save state and wait; PLANNING is stopped, never paused.",
        ),
        _row(
            "resume",
            {"PAUSED"},
            set(PAUSABLE),
            "Return to the state the session was paused from.",
        ),
        _row(
            "archive",
            {s for s in ALL_STATES if s != "ARCHIVED"},
            {"ARCHIVED"},
            "Start over / done: the draft is archived, never deleted. ARCHIVED is terminal.",
        ),
    )
}


def check(ms: MachineState) -> None:
    """Raise :class:`InvalidTransition` if ``ms`` breaks a structural invariant."""
    if ms.state == "PLANNING":
        if ms.plan_phase is None:
            raise InvalidTransition("PLANNING needs a plan_phase")
    elif ms.state == "PAUSED":
        if ms.paused_from is None or ms.paused_from not in PAUSABLE:
            raise InvalidTransition("PAUSED needs a pausable paused_from state")
    elif ms.plan_phase is not None and ms.state != "DISCOVERING":
        raise InvalidTransition(f"{ms.state} cannot carry a plan_phase")
    if ms.state != "PAUSED" and ms.paused_from is not None:
        raise InvalidTransition(f"{ms.state} cannot carry paused_from")
    if ms.origin is not None and ms.origin not in _ORIGINS:
        raise InvalidTransition(f"bad origin {ms.origin!r}")


def legal_transitions(ms: MachineState) -> frozenset[FlowTransition]:
    """Transition names that ``apply`` would accept from ``ms`` (suspended-run rules included)."""
    out: set[FlowTransition] = set()
    for name, row in TABLE.items():
        if ms.state not in row.sources:
            continue
        if (
            name in {"answer_given", "rediscover"}
            and ms.state == "DISCOVERING"
            and ms.plan_phase is None
        ):
            continue  # only a suspended run can be resumed or dropped from DISCOVERING
        out.add(name)
    return frozenset(out)


def _invalid(ms: MachineState, name: str, why: str = "") -> InvalidTransition:
    msg = f"cannot {name} from {ms.state}" + (f": {why}" if why else "")
    return InvalidTransition(msg, details={"state": ms.state, "transition": name})


def apply(
    ms: MachineState,
    name: FlowTransition,
    *,
    start_phase: PlanPhase | None = None,
    light: bool = False,
) -> MachineState:
    """Return the next :class:`MachineState` or raise :class:`InvalidTransition`.

    ``start_phase`` picks the phase a run starts in (``change_plan`` / ``change_after_handoff``;
    ``approve_brief`` always starts at ``research``). ``light`` is only meaningful for
    ``plan_ready`` of a run whose origin is HANDED_OFF.
    """
    row = TABLE.get(name)
    if row is None:
        raise InvalidTransition(f"unknown transition {name!r}", details={"transition": name})
    check(ms)
    if ms.state not in row.sources:
        raise _invalid(ms, name)
    if light and name != "plan_ready":
        raise _invalid(ms, name, "'light' only applies to plan_ready")
    if start_phase is not None and name not in {
        "approve_brief",
        "change_plan",
        "change_after_handoff",
        "answer_given",
    }:
        raise _invalid(ms, name, "'start_phase' does not apply")

    result: MachineState
    if name == "brief_complete":
        result = MachineState("BRIEF_REVIEW", None, ms.origin)
    elif name == "approve_brief":
        if start_phase not in (None, "research"):
            raise _invalid(ms, name, "Gate 1 approval always starts at research")
        result = MachineState("PLANNING", "research", "BRIEF_REVIEW")
    elif name == "change_brief":
        result = MachineState("DISCOVERING", None, ms.origin)
    elif name == "plan_ready":
        if ms.origin is None:  # a legacy run whose origin is unknown can only be stopped
            raise _invalid(ms, name, "run has no origin")
        if light and ms.origin != "HANDED_OFF":
            raise _invalid(ms, name, "only a post-handoff run can finish without Gate 2")
        target: SessionState = "HANDED_OFF" if light else "PLAN_REVIEW"
        result = MachineState(target, None, ms.origin)
    elif name == "needs_answer":
        result = MachineState("DISCOVERING", ms.plan_phase, ms.origin)
    elif name == "answer_given":
        if ms.plan_phase is None:
            raise _invalid(ms, name, "no suspended run is waiting for an answer")
        result = MachineState("PLANNING", start_phase or ms.plan_phase, ms.origin)
    elif name == "rediscover":
        if ms.state == "DISCOVERING" and ms.plan_phase is None:
            raise _invalid(ms, name, "no suspended run to drop")
        result = MachineState("DISCOVERING", None, ms.origin)
    elif name == "stop":
        result = MachineState(ms.origin if ms.origin is not None else "STOPPED", None, ms.origin)
    elif name == "approve_plan":
        result = MachineState("HANDED_OFF", None, ms.origin)
    elif name == "change_plan":
        result = MachineState("PLANNING", start_phase or "research", "PLAN_REVIEW")
    elif name == "change_after_handoff":
        result = MachineState("PLANNING", start_phase or "research", "HANDED_OFF")
    elif name == "pause":
        result = MachineState("PAUSED", ms.plan_phase, ms.origin, paused_from=ms.state)
    elif name == "resume":
        if ms.paused_from is None:  # pragma: no cover - check() guarantees it
            raise _invalid(ms, name, "nothing to resume")
        result = MachineState(ms.paused_from, ms.plan_phase, ms.origin)
    else:  # archive
        result = MachineState("ARCHIVED", None, ms.origin)

    if result.state not in row.targets:  # pragma: no cover - guards the table against edits
        raise _invalid(ms, name, f"table does not allow target {result.state}")
    check(result)
    return result


def advance_phase(ms: MachineState) -> MachineState:
    """PLANNING phase ``research`` -> ``build`` (the only phase move; not a state transition)."""
    check(ms)
    if ms.state != "PLANNING" or ms.plan_phase != "research":
        raise _invalid(ms, "advance_phase", "only a PLANNING run in research can move to build")
    return MachineState("PLANNING", "build", ms.origin)


# --------------------------------------------------------------------------- record adapters


def state_of(rec: SessionRecord) -> MachineState:
    return MachineState(rec.state, rec.plan_phase, rec.origin, rec.paused_from)


def _write(rec: SessionRecord, ms: MachineState) -> SessionRecord:
    return rec.model_copy(
        update={
            "state": ms.state,
            "plan_phase": ms.plan_phase,
            "origin": ms.origin,
            "paused_from": ms.paused_from,
        }
    )


def apply_to_record(
    rec: SessionRecord,
    name: FlowTransition,
    *,
    start_phase: PlanPhase | None = None,
    light: bool = False,
) -> SessionRecord:
    """The one function that turns a transition into a new :class:`SessionRecord`."""
    return _write(rec, apply(state_of(rec), name, start_phase=start_phase, light=light))


def advance_phase_record(rec: SessionRecord) -> SessionRecord:
    return _write(rec, advance_phase(state_of(rec)))


def awaiting_answer(ms: MachineState) -> bool:
    """True in DISCOVERING while a ``needs_answer`` question of a suspended run is pending."""
    return ms.state == "DISCOVERING" and ms.plan_phase is not None
