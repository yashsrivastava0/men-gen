"""Flow-owned bookkeeping that rides on ``DiscoveryState.extra`` (the contract's additive escape
hatch, a ``dict[str, str]`` the Discover engine copies through untouched).

Every key starts with ``flow.`` so it can never collide with Discover's own keys. The values are
small JSON strings or counters; nothing here is user-visible text. Pure functions: read a key from a
state, or return a new state with it set. The keys:

* ``flow.answer_tries`` / ``flow.answer_offer``: how many replies to a builder question were not an
  answer, and whether the "use the default" offer was made (BUG-6).
* ``flow.budget_hold``: the session is paused because the budget cap was reached, and what to start
  again when it is lifted (GAP-5).
* ``flow.slot_actors`` / ``flow.slot_conflicts``: who last answered each slot and the disagreements
  between stakeholders (GAP-3, see ``mde_flow.stakeholders``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

from pydantic import TypeAdapter, ValidationError

from mde_contracts.common import Frozen
from mde_contracts.engines import DiscoveryState

ANSWER_TRIES: Final = "flow.answer_tries"
ANSWER_OFFER: Final = "flow.answer_offer"
BUDGET_HOLD: Final = "flow.budget_hold"
SLOT_ACTORS: Final = "flow.slot_actors"
SLOT_CONFLICTS: Final = "flow.slot_conflicts"

HoldKind = Literal["gate1", "answer", "dropped"]
_NOTES: Final = TypeAdapter(dict[str, str])


class BudgetHold(Frozen):
    """Why a PAUSED session is waiting and what resumes it.

    ``gate1``: Gate 1 is approved on the current Brief but the run never started (or was halted
    before it finished): resuming starts it again. ``answer``: a builder question was answered but
    the run that continues from it never started (``answers`` are the slots it was answered with).
    ``dropped``: a change request at Gate 2 or after hand-off was not started; nothing resumes, the
    user asks again."""

    kind: HoldKind
    answers: list[str] = []


def with_extra(state: DiscoveryState, **changes: str | None) -> DiscoveryState:
    """A copy of ``state`` with the given ``extra`` keys set (``None`` removes a key)."""
    extra = dict(state.extra)
    for key, value in changes.items():
        if value is None:
            extra.pop(key, None)
        else:
            extra[key] = value
    return state.model_copy(update={"extra": extra})


def without_flow_keys(state: DiscoveryState, *keys: str) -> DiscoveryState:
    """``state`` without the named keys (a no-op when none of them is set)."""
    if not any(k in state.extra for k in keys):
        return state
    return state.model_copy(
        update={"extra": {k: v for k, v in state.extra.items() if k not in keys}}
    )


def count(extra: Mapping[str, str], key: str) -> int:
    try:
        return max(0, int(extra.get(key, "0")))
    except ValueError:
        return 0


def read_hold(state: DiscoveryState) -> BudgetHold | None:
    raw = state.extra.get(BUDGET_HOLD)
    if not raw:
        return None
    try:
        return BudgetHold.model_validate_json(raw)
    except ValidationError:
        return BudgetHold(kind="dropped")  # unreadable: still a hold, nothing to restart


def set_hold(state: DiscoveryState, hold: BudgetHold) -> DiscoveryState:
    return with_extra(state, **{BUDGET_HOLD: hold.model_dump_json()})


def clear_hold(state: DiscoveryState) -> DiscoveryState:
    return without_flow_keys(state, BUDGET_HOLD)


def screen_notes(state: DiscoveryState) -> dict[str, str]:
    """Discover's screen details as the builder's ``screen_notes`` map.

    Discover keeps what the user said about individual screens as one text in
    ``extra["screen_notes"]`` (notes joined with ``"; "``) because the user rarely says which screen
    id they mean. Each note becomes one entry, keyed ``note-1``, ``note-2``, ... in the order the
    user gave them. When Discover (or a later version of it) stores a JSON object instead (screen
    id or route to note), it is passed through unchanged."""
    raw = state.extra.get("screen_notes", "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            parsed = _NOTES.validate_json(raw)
        except ValidationError:
            parsed = None
        if parsed is not None:
            return {k: v for k, v in parsed.items() if v.strip()}
    notes = [part.strip() for part in raw.split(";") if part.strip()]
    return {f"note-{i}": note for i, note in enumerate(notes, start=1)}
