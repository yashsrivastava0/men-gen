"""Readiness per consumer, derived generically from the slot catalog (prompt 5.2, 6.8).

A consumer (research, blueprint, design, verify) is ready when none of the ``must`` slots it needs is
empty. Nothing here names a slot: the catalog's ``consumers``, ``level``, ``ask_if`` and ``brief_path``
decide. ``should`` slots are advisory and never a gap.

``ask_if`` is read with three-valued logic. ``filled()`` and ``empty()`` checks are *ordering* hints
("ask this after that"), so they count as unknown and never hide a gap; value comparisons are real
conditions ("business model only where money flows"): they hide the slot when they are false, and a
comparison against an empty value is unknown (so the slot still counts).
"""

from __future__ import annotations

from typing import Any

from mde_compass.catalog.loader import Catalog, SlotDef, parse_ask_if
from mde_compass.domain.brief import Brief, Readiness, ReadinessEntry
from mde_compass.views.paths import is_empty_value, slot_filled, slot_value

CONSUMERS: tuple[str, ...] = ("research", "blueprint", "design", "verify")
ENTERPRISE_GROUP = "org"

_Tri = bool | None


def _and(parts: list[_Tri]) -> _Tri:
    if any(p is False for p in parts):
        return False
    return None if any(p is None for p in parts) else True


def _or(parts: list[_Tri]) -> _Tri:
    if any(p is True for p in parts):
        return True
    return None if any(p is None for p in parts) else False


def _value_test(current: Any, op: str, expected: Any) -> _Tri:
    if is_empty_value(current):
        return None
    values = current if isinstance(current, list) else [current]
    texts = [str(v) for v in values]
    if op == "==":
        return expected in texts
    if op == "!=":
        return expected not in texts
    return any(t in expected for t in texts)  # op == "in"


def _eval(node: tuple[Any, ...], brief: Brief, catalog: Catalog) -> _Tri:
    tag = node[0]
    if tag == "and":
        return _and([_eval(node[1], brief, catalog), _eval(node[2], brief, catalog)])
    if tag == "or":
        return _or([_eval(node[1], brief, catalog), _eval(node[2], brief, catalog)])
    if tag == "not":
        inner = _eval(node[1], brief, catalog)
        return None if inner is None else not inner
    if tag in ("filled", "empty"):
        return None  # ordering hint, not a reason to hide a gap
    slot = catalog.slots.get(node[1])
    current = slot_value(brief, slot) if slot is not None else None
    return _value_test(current, node[2], node[3])


def applies(slot: SlotDef, brief: Brief, catalog: Catalog) -> bool:
    """Whether the slot's ``ask_if`` condition allows it to be a gap (unknown counts as yes)."""
    if not slot.ask_if:
        return True
    return _eval(parse_ask_if(slot.ask_if), brief, catalog) is not False


def needed_by(slot: SlotDef, consumer: str) -> bool:
    return consumer in slot.consumers or "all" in slot.consumers


def gaps_for(brief: Brief, catalog: Catalog, consumer: str) -> list[str]:
    """Slot ids of the ``must`` slots ``consumer`` needs that are still empty (catalog order)."""
    gaps: list[str] = []
    for slot_id in catalog.slot_ids_by_order():
        slot = catalog.slots[slot_id]
        if slot.level != "must" or slot.group == ENTERPRISE_GROUP:
            continue
        if not needed_by(slot, consumer):
            continue
        if slot_filled(brief, slot) or not applies(slot, brief, catalog):
            continue
        gaps.append(slot.id)
    return gaps


def compute_readiness(brief: Brief, catalog: Catalog) -> Readiness:
    """Readiness for research, blueprint, design and verify. ``pass`` iff no gaps."""
    entries: dict[str, ReadinessEntry] = {}
    for consumer in CONSUMERS:
        gaps = gaps_for(brief, catalog, consumer)
        entries[consumer] = ReadinessEntry(status="gaps" if gaps else "pass", gaps=gaps)
    return Readiness(**entries)
