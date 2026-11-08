"""A read-only view of the slot snapshot bound to the approved Brief (plus ``needs_answer`` replies).

The baseline reads slots through this view so "empty" means the same thing everywhere: an empty
record, ``None``, an empty string and an empty list are all "not set".
"""

from __future__ import annotations

from collections.abc import Mapping

from mde_contracts.engines import BuildRequest
from mde_contracts.slots import SLOT_IDS, SlotData, SlotRecord

_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})


def effective_slots(req: BuildRequest) -> dict[str, SlotRecord]:
    """The slot records of the request; ``answer_slots`` (replies to a question) win."""
    return {**req.slots, **req.answer_slots}


def _is_set(record: SlotRecord | None) -> bool:
    if record is None or record.state == "empty":
        return False
    value = record.value
    return not (value is None or value == "" or value == [])


class SlotView:
    def __init__(self, slots: Mapping[str, SlotRecord]) -> None:
        self._slots = dict(slots)

    @classmethod
    def of(cls, req: BuildRequest) -> SlotView:
        return cls(effective_slots(req))

    def is_set(self, slot_id: str) -> bool:
        return _is_set(self._slots.get(slot_id))

    def raw(self, slot_id: str) -> SlotData:
        record = self._slots.get(slot_id)
        return record.value if _is_set(record) and record is not None else None

    def state(self, slot_id: str) -> str:
        record = self._slots.get(slot_id)
        return record.state if record is not None else "empty"

    def text(self, slot_id: str, default: str = "") -> str:
        value = self.raw(slot_id)
        if value is None:
            return default
        if isinstance(value, list):
            return ", ".join(str(v) for v in value) or default
        if isinstance(value, bool):
            return "yes" if value else "no"
        return str(value).strip() or default

    def items(self, slot_id: str) -> list[str] | None:
        """A list slot as a list of strings (a single string becomes one item); None when unset."""
        value = self.raw(slot_id)
        if value is None:
            return None
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, bool):
            return None
        return [part.strip() for part in str(value).split(",") if part.strip()]

    def flag(self, slot_id: str, default: bool | None = None) -> bool | None:
        value = self.raw(slot_id)
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _TRUE:
                return True
            if lowered in _FALSE:
                return False
        return default

    def assumed(self) -> list[SlotRecord]:
        """Records in state ``assumed`` that hold a value, in the fixed slot order."""
        order = {slot_id: i for i, slot_id in enumerate(SLOT_IDS)}
        found = [r for r in self._slots.values() if r.state == "assumed" and _is_set(r)]
        return sorted(found, key=lambda r: (order.get(r.slot_id, len(order)), r.slot_id))


def render_value(value: SlotData) -> str:
    """A slot value as a short plain string (for assumption lines)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)
