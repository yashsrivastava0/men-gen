"""Two stakeholders disagree (spec 9, Enterprise): record both, resolve by the policy's role
precedence or flag the decision owner, never overwrite silently.

Pure functions over slot records. The flow calls :func:`settle_writes` after every Discover result
with the slots before and after the engine ran and the person who spoke; it gets back the slots to
keep, the updated bookkeeping (who last answered each slot, and every disagreement) and one
:class:`Finding` per disagreement, which it turns into a plain-language notice.

What counts as a stakeholder answer: a value a person tapped or typed (``source`` tap or text, state
``volunteered`` or ``confirmed``). Defaults, research, uploads and the organisation profile are not
answers, and a person changing their own mind is not a disagreement. A disagreement is a different
person giving a different value for a slot someone else already answered:

* the role with the higher place in ``Policy.role_precedence`` wins (a role the list does not
  mention ranks below every listed one); the other answer stays on record;
* when precedence cannot decide (the list is empty, neither role is on it, or both hold the same
  place) the earlier value is kept, the slot becomes ``disputed`` and the decision owner (the
  session owner, the requester) is told to choose;
* a disputed slot is settled by the decision owner writing it again; anyone else's write leaves it
  disputed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from pydantic import TypeAdapter, ValidationError

from mde_contracts.common import Frozen
from mde_contracts.slots import SlotData, SlotRecord

MAX_STAKES: Final = 12  # positions kept per slot (the oldest drop first)
_ANSWER_SOURCES: Final = frozenset({"tap", "text"})
_ANSWER_STATES: Final = frozenset({"volunteered", "confirmed"})
_USER_GIVEN: Final = frozenset({"volunteered", "confirmed", "disputed"})


class Actor(Frozen):
    """Who spoke: the authenticated user and the roles they spoke under."""

    user: str
    roles: list[str]


class Stake(Frozen):
    """One person's answer for one slot."""

    user: str
    roles: list[str]
    value: SlotData


class Conflict(Frozen):
    """Every position taken on one slot, in order (the record the spec asks for)."""

    slot_id: str
    stakes: list[Stake]
    status: Literal["resolved", "open"]
    how: Literal["precedence", "decision_owner"] | None = None  # how it was (or will be) settled


FindingKind = Literal["resolved_new", "resolved_old", "disputed", "still_disputed", "settled"]


@dataclass(frozen=True)
class Finding:
    """One disagreement the user should hear about."""

    kind: FindingKind
    slot_id: str
    kept: Stake  # the answer that stands
    other: Stake | None = None  # the answer that does not (or the earlier one)


@dataclass(frozen=True)
class Settlement:
    slots: dict[str, SlotRecord]
    actors: dict[str, Stake]
    conflicts: dict[str, Conflict]
    findings: list[Finding] = field(default_factory=list[Finding])
    reverted: frozenset[str] = frozenset()  # slots whose new value was not applied


_ACTORS: Final = TypeAdapter(dict[str, Stake])
_CONFLICTS: Final = TypeAdapter(dict[str, Conflict])


def load_actors(raw: str | None) -> dict[str, Stake]:
    if not raw:
        return {}
    try:
        return _ACTORS.validate_json(raw)
    except ValidationError:
        return {}


def load_conflicts(raw: str | None) -> dict[str, Conflict]:
    if not raw:
        return {}
    try:
        return _CONFLICTS.validate_json(raw)
    except ValidationError:
        return {}


def dump_actors(actors: Mapping[str, Stake]) -> str | None:
    return _ACTORS.dump_json(dict(actors)).decode() if actors else None


def dump_conflicts(conflicts: Mapping[str, Conflict]) -> str | None:
    return _CONFLICTS.dump_json(dict(conflicts)).decode() if conflicts else None


def open_disputes(conflicts: Mapping[str, Conflict]) -> list[str]:
    """Slot ids still waiting for the decision owner."""
    return [sid for sid, c in conflicts.items() if c.status == "open"]


def rank(precedence: Sequence[str], roles: Iterable[str]) -> int | None:
    """The best (lowest) place any of ``roles`` holds in ``precedence``; None when none is listed."""
    places = [precedence.index(r) for r in roles if r in precedence]
    return min(places) if places else None


def winner(
    precedence: Sequence[str], new_roles: Iterable[str], old_roles: Iterable[str]
) -> str | None:
    """``"new"`` or ``"old"`` when precedence decides between the two answers, else None."""
    new, old = rank(precedence, new_roles), rank(precedence, old_roles)
    if new is None and old is None:
        return None
    if new is None:
        return "old"
    if old is None:
        return "new"
    if new == old:
        return None
    return "new" if new < old else "old"


def _has_value(rec: SlotRecord | None) -> bool:
    return rec is not None and rec.state != "empty" and rec.value not in (None, "", [])


def _same(old: SlotRecord | None, new: SlotRecord | None) -> bool:
    if old is None or new is None:
        return old is new
    return old.value == new.value and old.state == new.state


def _is_answer(rec: SlotRecord | None) -> bool:
    return (
        rec is not None
        and _has_value(rec)
        and rec.source in _ANSWER_SOURCES
        and rec.state in _ANSWER_STATES
    )


def _push(conflict: Conflict | None, stakes: Sequence[Stake]) -> list[Stake]:
    seen = list(conflict.stakes) if conflict is not None else []
    for stake in stakes:
        if stake not in seen:
            seen.append(stake)
    return seen[-MAX_STAKES:]


def settle_writes(
    *,
    before: Mapping[str, SlotRecord],
    after: Mapping[str, SlotRecord],
    actor: Actor,
    owner_id: str,
    precedence: Sequence[str],
    actors: Mapping[str, Stake],
    conflicts: Mapping[str, Conflict],
    owner_choices: Mapping[str, SlotData] | None = None,
) -> Settlement:
    """Compare the slots before and after one Discover result and settle every disagreement.

    ``owner_choices`` are values the decision owner named explicitly in this turn (``set_slot``
    change ops): naming the value a disputed slot already holds settles it although the engine saw
    nothing to change.
    """
    slots = dict(after)
    ledger = dict(actors)
    book = dict(conflicts)
    findings: list[Finding] = []
    reverted: set[str] = set()
    is_owner = actor.user == owner_id
    for sid in dict.fromkeys([*after, *before]):
        old, new = before.get(sid), after.get(sid)
        if _same(old, new):
            chosen = (owner_choices or {}).get(sid)
            disputed = old is not None and old.state == "disputed"
            if (
                is_owner
                and disputed
                and old is not None
                and chosen is not None
                and chosen == old.value
            ):
                # the owner names the value the slot holds: that is the decision
                slots[sid] = old.model_copy(update={"state": "confirmed", "source": "text"})
                stake = Stake(user=actor.user, roles=list(actor.roles), value=old.value)
                ledger[sid] = stake
                book[sid] = _settle(book.get(sid), sid, stake)
                findings.append(Finding("settled", sid, kept=stake))
            continue
        if not _is_answer(new) or new is None:
            continue
        stake = Stake(user=actor.user, roles=list(actor.roles), value=new.value)
        last = ledger.get(sid)
        prior = old if old is not None and old.state in _USER_GIVEN and _has_value(old) else None
        if prior is not None and prior.state == "disputed":
            if is_owner:
                ledger[sid] = stake
                book[sid] = _settle(book.get(sid), sid, stake)
                findings.append(Finding("settled", sid, kept=stake))
            else:
                slots[sid] = prior
                reverted.add(sid)
                book[sid] = Conflict(
                    slot_id=sid,
                    stakes=_push(book.get(sid), [stake]),
                    status="open",
                    how="decision_owner",
                )
                other = last or Stake(user="", roles=[], value=prior.value)
                findings.append(Finding("still_disputed", sid, kept=other, other=stake))
            continue
        if prior is None or last is None or last.user == actor.user or prior.value == new.value:
            ledger[sid] = stake  # a first answer, or the same person changing their mind
            continue
        who = winner(precedence, actor.roles, last.roles)
        if who == "new":
            ledger[sid] = stake
            book[sid] = Conflict(
                slot_id=sid,
                stakes=_push(book.get(sid), [last, stake]),
                status="resolved",
                how="precedence",
            )
            findings.append(Finding("resolved_new", sid, kept=stake, other=last))
        elif who == "old":
            slots[sid] = prior
            reverted.add(sid)
            book[sid] = Conflict(
                slot_id=sid,
                stakes=_push(book.get(sid), [last, stake]),
                status="resolved",
                how="precedence",
            )
            findings.append(Finding("resolved_old", sid, kept=last, other=stake))
        else:
            slots[sid] = prior.model_copy(update={"state": "disputed"})
            reverted.add(sid)
            book[sid] = Conflict(
                slot_id=sid,
                stakes=_push(book.get(sid), [last, stake]),
                status="open",
                how="decision_owner",
            )
            findings.append(Finding("disputed", sid, kept=last, other=stake))
    return Settlement(slots, ledger, book, findings, frozenset(reverted))


def _settle(conflict: Conflict | None, slot_id: str, stake: Stake) -> Conflict:
    return Conflict(
        slot_id=slot_id,
        stakes=_push(conflict, [stake]),
        status="resolved",
        how="decision_owner",
    )
