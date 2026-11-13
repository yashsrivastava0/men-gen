"""No Locked value rests on a fact that is gone (ADR 0016). Pure with respect to I/O; it changes the slot store it is given.

``fact_projector.reproject`` rebuilds the slots that facts WROTE (those with an effect). A value the compile wrote has
provenance ``FCT-###`` and no effect, so after a pivot or an edited answer ``reproject`` never looks at it and the slot
can stay Locked while the fact it cites is superseded, disputed, or no longer verified. Two helpers close that gap:

* ``slots_citing(slots, fact_ids)``: the slots whose evidence (the slot's own, or a record's ``_meta``) cites any of
  these facts. Hand them to ``reproject`` together with the slots the facts' effects touched.
* ``downgrade_unsupported_locks(slots, facts)``: the post-pass. A Locked slot (or a Locked record of a list slot) whose
  cited ``FCT-`` facts are not ALL active and verified (``Fact.supports_locked``) becomes ``Default`` (status
  ``assumed``, so it shows up as an assumption and a later compile may replace it with verified evidence); the old state
  goes to the history. What it changed is returned, so the caller can say so. A value the person typed or tapped, which
  cites a turn and no fact, is never touched.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from mde_compass.domain.common import HistoryEntry
from mde_compass.domain.interview import Fact
from mde_compass.domain.slots import SlotRecord
from mde_compass.engine.fact_projector import FACT_REF_PREFIX, fact_refs
from mde_compass.engine.slots import SlotStore

REASON = "evidence no longer active"


@dataclass(frozen=True)
class LockChange:
    """One Locked value that was downgraded: the slot, the facts that no longer support it, and how many of its
    records (list slots carry evidence per record) were downgraded instead of the slot as a whole."""

    slot_id: str
    unsupported: tuple[str, ...]
    records: int = 0


def _meta_refs(meta: Any) -> list[str]:
    """Fact ids a record's ``_meta`` provenance cites."""
    if not isinstance(meta, dict):
        return []
    prov = meta.get("provenance")
    if not isinstance(prov, list):
        return []
    return [
        str(p["ref"])
        for p in prov
        if isinstance(p, dict) and str(p.get("ref", "")).startswith(FACT_REF_PREFIX)
    ]


def _record_metas(value: JsonValue) -> list[dict[str, JsonValue]]:
    """The ``_meta`` dicts of the records of a list slot value."""
    if not isinstance(value, list):
        return []
    return [
        m for item in value if isinstance(item, dict) and isinstance(m := item.get("_meta"), dict)
    ]


def slots_citing(slots: SlotStore, fact_ids: Collection[str]) -> list[str]:
    """Ids of the slots whose evidence cites any of ``fact_ids`` (the slot's own or a record's), in slot order."""
    wanted = set(fact_ids)
    out: list[str] = []
    for slot_id, record in slots.all().items():
        cited = set(fact_refs(record))
        for meta in _record_metas(record.value):
            cited.update(_meta_refs(meta))
        if cited & wanted:
            out.append(slot_id)
    return out


def _unsupported(refs: Iterable[str], by_id: Mapping[str, Fact]) -> list[str]:
    bad = [r for r in dict.fromkeys(refs) if (f := by_id.get(r)) is None or not f.supports_locked()]
    return bad


def downgrade_unsupported_locks(slots: SlotStore, facts: Iterable[Fact]) -> list[LockChange]:
    """Downgrade every Locked slot or record whose cited facts are not all active and verified; see the module docstring.
    ``facts`` is the whole ledger (a fact that is not in it counts as gone). Returns what was changed."""
    by_id = {f.id: f for f in facts}
    changes: list[LockChange] = []
    records = slots.dump()
    touched = False
    for rec in records:
        bad_slot = _unsupported(fact_refs(rec), by_id) if rec.tier == "Locked" else []
        bad_records: list[str] = []
        downgraded = 0
        for meta in _record_metas(rec.value):
            refs = _meta_refs(meta)
            if meta.get("tier") == "Locked" and refs:
                bad = _unsupported(refs, by_id)
                if bad:
                    meta["tier"] = "Default"
                    meta["status"] = "assumed"
                    bad_records.extend(bad)
                    downgraded += 1
        if not bad_slot and not downgraded:
            continue
        touched = True
        update: dict[str, Any] = {"value": rec.value}
        if bad_slot:
            update.update(
                tier="Default",
                status="assumed",
                history=[
                    *rec.history,
                    HistoryEntry(
                        value=rec.value, status=rec.status, turn_id=rec.turn_id, reason=REASON
                    ),
                ],
            )
        slots_record: SlotRecord = rec.model_copy(update=update)
        _replace(records, slots_record)
        changes.append(
            LockChange(
                slot_id=rec.slot_id,
                unsupported=tuple(dict.fromkeys([*bad_slot, *bad_records])),
                records=downgraded,
            )
        )
    if touched:
        slots.load(records)
    return changes


def _replace(records: list[SlotRecord], new: SlotRecord) -> None:
    for i, old in enumerate(records):
        if old.slot_id == new.slot_id:
            records[i] = new
            return
