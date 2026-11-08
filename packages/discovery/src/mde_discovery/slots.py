"""SlotStore: the slot table with provenance and the rules of spec 3.3.

Rules enforced here, nowhere else:
- a correction overwrites, it never appends (the one exception is ``compliance.flags``, a union
  across the flagged domains);
- an explicit negation clears the slot (or removes the named items from a list);
- an ``assumed`` value never replaces a user-given (``volunteered``, ``confirmed``) one;
- a slot nobody touched is never reset.

The store is a small mutable working copy for one turn; ``snapshot()`` gives the immutable records
that go back into ``DiscoveryState``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from mde_content.expr import holds
from mde_content.questions import QuestionBanks
from mde_contracts.calls import SlotUpdate
from mde_contracts.cards import Card
from mde_contracts.common import Mode
from mde_contracts.slots import SLOT_IDS, SlotData, SlotRecord, SlotSource, SlotState

RANK: dict[str, int] = {"empty": 0, "assumed": 1, "volunteered": 2, "disputed": 2, "confirmed": 3}
USER_GIVEN: frozenset[str] = frozenset({"volunteered", "confirmed", "disputed"})
UNION_SLOTS: frozenset[str] = frozenset({"compliance.flags"})


@dataclass(frozen=True)
class WriteResult:
    applied: bool  # the record changed (value or state)
    value_changed: bool  # the value itself changed
    reason: str = ""


def _clean(value: SlotData) -> SlotData:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return list(dict.fromkeys(str(v).strip() for v in value if str(v).strip()))
    return value


class SlotStore:
    def __init__(self, slots: Mapping[str, SlotRecord] | None, now: Callable[[], datetime]) -> None:
        self._slots: dict[str, SlotRecord] = dict(slots or {})
        self._now = now
        self.changed: list[str] = []  # slot ids whose value changed this turn, in order

    # ----------------------------------------------------------------- reading
    def snapshot(self) -> dict[str, SlotRecord]:
        return dict(self._slots)

    def get(self, slot_id: str) -> SlotRecord | None:
        return self._slots.get(slot_id)

    def state(self, slot_id: str) -> SlotState:
        rec = self._slots.get(slot_id)
        return rec.state if rec else "empty"

    def value(self, slot_id: str) -> SlotData:
        rec = self._slots.get(slot_id)
        return rec.value if rec and rec.state != "empty" else None

    def is_filled(self, slot_id: str) -> bool:
        """A usable value is present (an empty list or empty text counts as unfilled)."""
        return self.value(slot_id) not in (None, "", [])

    def list_value(self, slot_id: str) -> list[str]:
        value = self.value(slot_id)
        return [str(v) for v in value] if isinstance(value, list) else []

    def text_value(self, slot_id: str) -> str | None:
        value = self.value(slot_id)
        return value if isinstance(value, str) and value else None

    def context(self, sensitive: list[str]) -> dict[str, SlotData]:
        """The ``ask_if`` context: slot values plus the read-only ``sensitive.domains``."""
        ctx: dict[str, SlotData] = {k: v.value for k, v in self._slots.items() if self.is_filled(k)}
        ctx["sensitive.domains"] = list(sensitive) if sensitive else None
        return ctx

    # ----------------------------------------------------------------- writing
    def _note(self, slot_id: str) -> None:
        if slot_id not in self.changed:
            self.changed.append(slot_id)

    def _put(
        self,
        slot_id: str,
        value: SlotData,
        state: SlotState,
        source: SlotSource,
        evidence: str | None,
    ) -> None:
        self._slots[slot_id] = SlotRecord.model_validate(
            {
                "slot_id": slot_id,
                "value": value,
                "state": state,
                "source": source,
                "evidence": evidence,
                "updated_at": self._now(),
            }
        )

    def write(
        self,
        slot_id: str,
        value: SlotData,
        *,
        state: SlotState,
        source: SlotSource,
        evidence: str | None = None,
        union: bool | None = None,
    ) -> WriteResult:
        """Store a value with provenance under the spec 3.3 rules."""
        if slot_id not in SLOT_IDS:
            return WriteResult(False, False, "unknown_slot")
        value = _clean(value)
        union = slot_id in UNION_SLOTS if union is None else union
        explicit_empty_list = value == [] and union
        if value in (None, "") or (value == [] and not explicit_empty_list):
            return WriteResult(False, False, "empty")
        existing = self._slots.get(slot_id)
        has_value = existing is not None and existing.state != "empty"
        if (
            state == "assumed"
            and has_value
            and existing is not None
            and existing.state in USER_GIVEN
        ):
            return WriteResult(False, False, "assumed_never_replaces_user_value")
        new_value: SlotData = value
        if union and isinstance(value, list):
            current = existing.value if has_value and existing is not None else []
            base = [str(v) for v in current] if isinstance(current, list) else []
            new_value = list(dict.fromkeys([*base, *value]))
        if has_value and existing is not None and existing.value == new_value:
            if RANK[state] > RANK[existing.state]:
                self._put(slot_id, new_value, state, source, evidence or existing.evidence)
                return WriteResult(True, False, "state_upgraded")
            return WriteResult(False, False, "unchanged")
        new_state = state
        if state == "volunteered" and has_value and existing is not None:
            new_state = "confirmed" if existing.state == "confirmed" else state
        self._put(slot_id, new_value, new_state, source, evidence)
        self._note(slot_id)
        return WriteResult(True, True)

    def clear(self, slot_id: str) -> bool:
        """An explicit negation: the slot becomes empty. A slot nobody named is never cleared."""
        if slot_id in self._slots and self._slots[slot_id].state != "empty":
            del self._slots[slot_id]
            self._note(slot_id)
            return True
        return False

    def remove_items(self, slot_id: str, items: list[str]) -> bool:
        """Negate part of a list slot ("no payments" removes ``payments`` from the features)."""
        rec = self._slots.get(slot_id)
        if rec is None or not isinstance(rec.value, list):
            return False
        kept = [v for v in rec.value if v not in items]
        if kept == rec.value:
            return False
        if not kept:
            return self.clear(slot_id)
        self._put(slot_id, kept, rec.state, rec.source or "text", rec.evidence)
        self._note(slot_id)
        return True

    def mark_disputed(self, slot_id: str) -> None:
        rec = self._slots.get(slot_id)
        if rec is not None and rec.state != "disputed":
            self._put(slot_id, rec.value, "disputed", rec.source or "text", rec.evidence)

    # ----------------------------------------------------------------- updates from C1 / C14
    def apply_update(
        self,
        update: SlotUpdate,
        value: SlotData,
        *,
        state: SlotState,
        source: SlotSource,
    ) -> WriteResult:
        """One validated ``SlotUpdate``: a negation clears or trims, otherwise a write."""
        if update.negate:
            if isinstance(value, list) and value:
                removed = self.remove_items(update.slot_id, [str(v) for v in value])
            elif value not in (None, ""):
                removed = (
                    self.clear(update.slot_id) if self.value(update.slot_id) == value else False
                )
            else:
                removed = self.clear(update.slot_id)
            return WriteResult(removed, removed, "negated" if removed else "nothing_to_negate")
        return self.write(
            update.slot_id, value, state=state, source=source, evidence=update.evidence or None
        )


def detect_contradiction(
    banks: QuestionBanks,
    store: SlotStore,
    archetype: str | None,
    mode: Mode,
    sensitive: list[str],
) -> Card | None:
    """The first resolution card whose ``ask_if`` (a contradiction between answers) holds."""
    ctx = store.context(sensitive)
    for card in banks.all_cards():
        if card.kind != "resolution":
            continue
        if card.archetypes and archetype not in card.archetypes:
            continue
        if card.modes and mode not in card.modes:
            continue
        if holds(card.ask_if, ctx):
            return card
    return None
