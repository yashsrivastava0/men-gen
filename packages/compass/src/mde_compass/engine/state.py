"""The session state machine: a pure function over an explicit transition table (prompt 6.14).

The table is data (``TRANSITIONS``): state -> trigger -> next state. Rules the property tests pin:

* ``APPROVED`` is entered only by ``gate_approved`` from ``BRIEF_REVIEW`` (or by returning to the
  state a side state came from, when that origin already was ``APPROVED``).
* ``CHANGING`` is reachable from every state after the first Brief draft; ``PAUSED`` from every
  working state. Both return to where they came from: the caller keeps the origin
  (``SessionRecord.resume_state``) and passes it back as ``origin``. The table marks that target
  with the ``ORIGIN`` marker. ``change_applied`` from an approved origin lands in ``BRIEF_REVIEW``
  (the approved Brief was edited, so the affected sections are voided and the gate reopens).
* ``ARCHIVED`` (``start_over``) has no way back: a new session is a new record. Nothing is deleted.
"""

from __future__ import annotations

from typing import cast

from mde_compass.domain.session import SESSION_STATES, SessionState
from mde_core.errors import InvalidTransition

ORIGIN = "@origin"
"""Table marker: the target is the state the side state was entered from."""

_DRAFT_STATES = (
    "INTERVIEWING",
    "PLAYBACK",
    "SEEDING",
    "PROBING",
    "BRIEF_REVIEW",
    "APPROVED",
)
_WORKING_STATES = (
    "WELCOME",
    "INTAKE",
    "UNDERSTANDING",
    "INTERVIEWING",
    "PLAYBACK",
    "SEEDING",
    "PROBING",
    "BRIEF_REVIEW",
    "APPROVED",
)

_FLOW: dict[str, dict[str, str]] = {
    "WELCOME": {
        "start": "INTAKE",
        "idea_received": "UNDERSTANDING",
        "documents_received": "INTAKE",
    },
    "INTAKE": {
        "idea_received": "UNDERSTANDING",
        "documents_received": "UNDERSTANDING",
    },
    "UNDERSTANDING": {"understood": "INTERVIEWING"},
    "INTERVIEWING": {
        "playback_shown": "PLAYBACK",
        "queue_empty": "SEEDING",
    },
    "PLAYBACK": {
        "playback_confirmed": "INTERVIEWING",
        "need_more": "INTERVIEWING",
        "queue_empty": "SEEDING",
    },
    "SEEDING": {"research_seeded": "PROBING", "need_more": "INTERVIEWING"},
    "PROBING": {
        "probes_done": "BRIEF_REVIEW",
        "gate_open": "BRIEF_REVIEW",
        "need_more": "INTERVIEWING",
    },
    "BRIEF_REVIEW": {
        "gate_approved": "APPROVED",
        "need_more": "INTERVIEWING",
        "reopen": "BRIEF_REVIEW",
    },
    "APPROVED": {"reopen": "BRIEF_REVIEW"},
    "CHANGING": {"change_applied": ORIGIN, "change_cancelled": ORIGIN},
    "PAUSED": {"resume": ORIGIN},
    "ARCHIVED": {},
}


def _build_table() -> dict[str, dict[str, str]]:
    table = {state: dict(edges) for state, edges in _FLOW.items()}
    for state in _DRAFT_STATES:
        table[state]["change_requested"] = "CHANGING"
    for state in _WORKING_STATES:
        table[state]["pause"] = "PAUSED"
    for state in table:
        if state != "ARCHIVED":
            table[state]["start_over"] = "ARCHIVED"
    return table


TRANSITIONS: dict[str, dict[str, str]] = _build_table()
"""state -> trigger -> next state (``ORIGIN`` means: back to where the side state was entered)."""

TRIGGERS: tuple[str, ...] = tuple(sorted({t for edges in TRANSITIONS.values() for t in edges}))
SIDE_STATES: frozenset[str] = frozenset({"CHANGING", "PAUSED"})

assert set(TRANSITIONS) == set(SESSION_STATES), "transition table must cover every state"


def can(state: str, trigger: str, *, origin: str | None = None) -> bool:
    """Whether ``trigger`` is allowed in ``state`` (a side state also needs its ``origin``)."""
    target = TRANSITIONS.get(state, {}).get(trigger)
    if target is None:
        return False
    return target != ORIGIN or origin in TRANSITIONS


def next_state(
    state: SessionState, trigger: str, *, origin: SessionState | None = None
) -> SessionState:
    """The state after ``trigger``. Raises ``InvalidTransition`` for an edge that is not in the table.

    ``origin`` is required to leave ``CHANGING`` or ``PAUSED``: it is the state they were entered from.
    """
    target = TRANSITIONS.get(state, {}).get(trigger)
    if target is None:
        raise InvalidTransition(f"{trigger!r} is not allowed in state {state}")
    if target != ORIGIN:
        return cast(SessionState, target)
    if origin is None or origin not in TRANSITIONS:
        raise InvalidTransition(f"{trigger!r} from {state} needs the state it came from")
    if trigger == "change_applied" and origin == "APPROVED":
        return "BRIEF_REVIEW"
    return origin


def reachable_states(start: str = "WELCOME") -> set[str]:
    """States reachable from ``start`` through the table (side states return to any working state)."""
    seen = {start}
    stack = [start]
    while stack:
        state = stack.pop()
        for target in TRANSITIONS[state].values():
            nxt = (
                [s for s in TRANSITIONS if s not in SIDE_STATES and s != "ARCHIVED"]
                if target == ORIGIN
                else [target]
            )
            for s in nxt:
                if s not in seen:
                    seen.add(s)
                    stack.append(s)
    return seen
