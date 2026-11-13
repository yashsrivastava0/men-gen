"""Slot coverage: has a topic found what Spec needs? Pure code over verified atoms.

``count`` slot: at least ``min`` distinct items of the slot's kind; an item only counts when its list
attribute is rich enough (``attr_min`` entries, and every ``extra_attrs`` rule), so a thin entity with two
fields does not fill a "six fields" slot. ``distinct_values`` slot: at least ``min`` distinct values across
a list attribute of all items of the kind (eight distinct feature keys across competitors). A topic is
COVERED when every required slot is filled; it then spends no more searches (R19).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from mde_research_agent.catalog.models import SlotSpec
from mde_research_agent.core.norm_keys import clean_key

_VALUE_KEYS = ("feature_key", "screen_key", "name", "key", "value", "state")


class AtomLike(Protocol):
    kind: str
    norm_key: str
    data: dict[str, Any]


@dataclass(frozen=True)
class SlotFill:
    key: str
    required: bool
    needed: int
    count: int  # qualifying items (count slot) or distinct values (distinct_values slot)
    filled: bool
    thin: bool  # items exist but none is rich enough yet


@dataclass(frozen=True)
class Coverage:
    fills: tuple[SlotFill, ...]

    @property
    def covered(self) -> bool:
        return all(f.filled for f in self.fills if f.required)

    @property
    def required_total(self) -> int:
        return sum(1 for f in self.fills if f.required)

    @property
    def required_filled(self) -> int:
        return sum(1 for f in self.fills if f.required and f.filled)

    @property
    def unfilled_required(self) -> tuple[SlotFill, ...]:
        return tuple(f for f in self.fills if f.required and not f.filled)


def values_of(value: Any) -> list[str]:
    """Strings found in an attribute: a string, a list of strings, or a list of small objects."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        for k in _VALUE_KEYS:
            if isinstance(value.get(k), str) and value[k].strip():
                return [value[k]]
        # A small object of a shape nobody listed above (D-160: ``{state, content}`` was read as empty, so the
        # Screens slot could never fill): its first non-empty text field names it.
        for v in value.values():
            if isinstance(v, str) and v.strip():
                return [v]
        return []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for v in value:
            out.extend(values_of(v))
        return out
    return []


def _distinct_key(slot: SlotSpec, atom: AtomLike) -> str:
    raw = (
        atom.norm_key
        if slot.distinct_by == "norm_key"
        else values_of(atom.data.get(slot.distinct_by))
    )
    text = raw if isinstance(raw, str) else (raw[0] if raw else atom.norm_key)
    return clean_key(text)


def _rich_enough(slot: SlotSpec, atom: AtomLike) -> bool:
    if (
        slot.attr
        and len({clean_key(v) for v in values_of(atom.data.get(slot.attr))}) < slot.attr_min
    ):
        return False
    return all(
        len({clean_key(v) for v in values_of(atom.data.get(x.attr))}) >= x.min
        for x in slot.extra_attrs
    )


def evaluate_slot(slot: SlotSpec, atoms: Iterable[AtomLike]) -> SlotFill:
    mine = [a for a in atoms if a.kind == slot.kind]
    if slot.type == "distinct_values":
        support: dict[str, set[str]] = {}
        for a in mine:
            who = _distinct_key(slot, a)
            for v in values_of(a.data.get(slot.attr or "")):
                if clean_key(v):
                    support.setdefault(clean_key(v), set()).add(who)
        count = sum(1 for who in support.values() if len(who) >= slot.min_support)
        return SlotFill(
            slot.key,
            slot.required,
            slot.min,
            count,
            count >= slot.min,
            bool(support) and count < slot.min,
        )
    qualifying = {_distinct_key(slot, a) for a in mine if _rich_enough(slot, a)}
    qualifying.discard("")
    filled = len(qualifying) >= slot.min
    return SlotFill(
        slot.key, slot.required, slot.min, len(qualifying), filled, bool(mine) and not filled
    )


def evaluate_target(slots: Sequence[SlotSpec], atoms: Sequence[AtomLike]) -> Coverage:
    return Coverage(tuple(evaluate_slot(s, atoms) for s in slots))
