"""SlotStore: the slot table with provenance and the write rules of prompt 5.1.

Rules enforced here and nowhere else:

* an unknown slot id is rejected (the catalog is the vocabulary);
* an ``assumed`` write never replaces a volunteered, confirmed or disputed value;
* a correction overwrites and appends the previous value to ``history``;
* an explicit negation (``remove=True``) clears the slot (state ``empty``, value None) with history,
  or removes named items from a list slot;
* an equal value with a higher rank is a state upgrade (assumed < volunteered = disputed < confirmed),
  and a volunteered write onto a confirmed slot stays confirmed;
* a slot nobody touched is never reset;
* the tier is computed from the provenance (``max_tier``): ``Locked`` only with evidence;
* enterprise: a write from a different contributor with a different value onto a user-given slot makes
  the slot ``disputed`` (never auto-merged) and the result reason is ``conflict``.

Adapted from ``mde_discovery.slots`` (copy-adapt, not imported).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog
from mde_compass.domain.common import HistoryEntry, Provenance, Status, max_tier
from mde_compass.domain.slots import SlotRecord
from mde_core.clock import Clock

RANK: dict[str, int] = {"empty": 0, "assumed": 1, "volunteered": 2, "disputed": 2, "confirmed": 3}
USER_GIVEN: frozenset[str] = frozenset({"volunteered", "confirmed", "disputed"})


@dataclass(frozen=True)
class WriteResult:
    """Outcome of one write. ``reason``: created, corrected, state_upgraded, unchanged, cleared,
    items_removed, conflict, or a rejection (unknown_slot, empty, nothing_to_remove,
    assumed_never_replaces_user_value)."""

    applied: bool
    reason: str
    record: SlotRecord | None = None
    previous: SlotRecord | None = None


def is_blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _key(item: Any) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)


def _clean(value: JsonValue) -> JsonValue:
    """Trim text, drop blank list items and duplicates (order kept)."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        out: list[JsonValue] = []
        seen: set[str] = set()
        for item in value:
            if isinstance(item, str):
                item = item.strip()
            if is_blank(item) or _key(item) in seen:
                continue
            seen.add(_key(item))
            out.append(item)
        return out
    return value


def plain(value: Any) -> Any:
    """A slot value without the per-record evidence (``_meta``) the Brief round trip carries inside list items. A history
    snapshot or a model call needs the words, not the evidence of every earlier snapshot: carrying it made each rewrite
    of a list nest the history of the one before (a 30-feature list reached 500 KB and every model call sent it)."""
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items() if k != "_meta"}
    return value


def item_name(item: Any) -> str:
    if isinstance(item, dict):
        for k in ("name", "term", "label", "name_in_user_words", "id"):
            if isinstance(item.get(k), str):
                return item[k].strip().casefold()
        return _key(item)
    return str(item).strip().casefold()


def tier_for(status: Status, has_value: bool, provenance: list[Provenance]) -> str:
    """The tier a slot may carry: Open without a value, never above Default when only assumed."""
    if not has_value or status == "empty":
        return "Open"
    tier = max_tier(has_value=True, provenance=provenance)
    return "Default" if status in ("assumed", "disputed") and tier == "Locked" else tier


class SlotStore:
    """Working copy of the slot table. ``load``/``dump`` move records to and from storage."""

    def __init__(self, catalog: Catalog, *, clock: Clock) -> None:
        self._catalog = catalog
        self._clock = clock
        self._records: dict[str, SlotRecord] = {}

    # ----------------------------------------------------------------- reading
    def get(self, slot_id: str) -> SlotRecord | None:
        return self._records.get(slot_id)

    def all(self) -> dict[str, SlotRecord]:
        return dict(self._records)

    def filled(self, slot_id: str) -> bool:
        rec = self._records.get(slot_id)
        return rec is not None and rec.status != "empty" and not is_blank(rec.value)

    def values(self) -> dict[str, JsonValue]:
        return {k: r.value for k, r in self._records.items() if self.filled(k)}

    def history(self, slot_id: str) -> list[HistoryEntry]:
        rec = self._records.get(slot_id)
        return list(rec.history) if rec else []

    def dump(self) -> list[SlotRecord]:
        return [r.model_copy(deep=True) for r in self._records.values()]

    def load(self, records: Iterable[SlotRecord]) -> None:
        """Replace the table with ``records`` (restore from storage or after a patch/undo)."""
        self._records = {r.slot_id: r.model_copy(deep=True) for r in records}

    # ----------------------------------------------------------------- writing
    def _prov(
        self, provenance: Provenance, turn_id: str | None, contributor: str | None
    ) -> Provenance:
        return provenance.model_copy(
            update={
                "ref": provenance.ref or (turn_id or ""),
                "date": provenance.date or self._clock.now(),
                "contributor_id": provenance.contributor_id or contributor,
            }
        )

    def _history(self, prev: SlotRecord, reason: str, turn_id: str | None) -> HistoryEntry:
        return HistoryEntry(
            value=plain(prev.value),
            status=prev.status,
            turn_id=turn_id or prev.turn_id,
            at=self._clock.now(),
            reason=reason,
        )

    def _put(self, rec: SlotRecord) -> SlotRecord:
        has_value = rec.status != "empty" and not is_blank(rec.value)
        rec = rec.model_copy(update={"tier": tier_for(rec.status, has_value, rec.provenance)})
        self._records[rec.slot_id] = rec
        return rec

    def write(
        self,
        slot_id: str,
        value: JsonValue,
        *,
        state: Status,
        provenance: Provenance,
        confidence: float = 0.5,
        turn_id: str | None = None,
        remove: bool = False,
        custom_text: str | None = None,
        contributor_id: str | None = None,
    ) -> WriteResult:
        """Store a value (or an explicit negation) under the rules in the module docstring."""
        if slot_id not in self._catalog.slots:
            return WriteResult(False, "unknown_slot")
        prev = self._records.get(slot_id)
        prev_filled = self.filled(slot_id)
        prov = self._prov(provenance, turn_id, contributor_id)
        value = _clean(value)
        user_given_prev = prev is not None and prev_filled and prev.status in USER_GIVEN
        if state == "assumed" and user_given_prev:
            return WriteResult(False, "assumed_never_replaces_user_value", prev, prev)
        if remove:
            return self._remove(slot_id, value, prev, prev_filled, prov, turn_id)
        if is_blank(value) or state == "empty":
            return WriteResult(False, "empty", prev, prev)
        base = {
            "slot_id": slot_id,
            "value": value,
            "turn_id": turn_id,
            "contributor_id": contributor_id,
            "custom_text": custom_text,
            "needs_confirmation": custom_text is not None and state != "confirmed",
            "confidence": confidence,
        }
        if prev is None or not prev_filled:
            history = list(prev.history) if prev else []
            rec = self._put(SlotRecord(**base, status=state, provenance=[prov], history=history))
            return WriteResult(True, "created", rec, prev)
        assert prev is not None
        if prev.value == value:
            return self._same_value(prev, base, state, prov, custom_text)
        if (
            user_given_prev
            and state in USER_GIVEN
            and contributor_id
            and prev.contributor_id
            and contributor_id != prev.contributor_id
        ):
            return self._conflict(prev, base, prov, turn_id)
        status: Status = (
            "confirmed" if state == "volunteered" and prev.status == "confirmed" else state
        )
        history = [*prev.history, self._history(prev, "corrected", turn_id)]
        rec = self._put(SlotRecord(**base, status=status, provenance=[prov], history=history))
        return WriteResult(True, "corrected", rec, prev)

    def _same_value(
        self,
        prev: SlotRecord,
        base: dict[str, Any],
        state: Status,
        prov: Provenance,
        custom: str | None,
    ) -> WriteResult:
        if RANK[state] <= RANK[prev.status] and custom is None:
            return WriteResult(False, "unchanged", prev, prev)
        status: Status = state if RANK[state] > RANK[prev.status] else prev.status
        provenance = prev.provenance if prov in prev.provenance else [*prev.provenance, prov]
        rec = self._put(
            prev.model_copy(
                update={
                    "status": status,
                    "provenance": provenance,
                    "confidence": max(prev.confidence, base["confidence"]),
                    "custom_text": custom if custom is not None else prev.custom_text,
                    "needs_confirmation": prev.needs_confirmation and status != "confirmed",
                    "turn_id": base["turn_id"] or prev.turn_id,
                }
            )
        )
        return WriteResult(True, "state_upgraded", rec, prev)

    def _conflict(
        self, prev: SlotRecord, base: dict[str, Any], prov: Provenance, turn_id: str | None
    ) -> WriteResult:
        """Two contributors, two values: keep the first, record the other as a proposal, never merge."""
        proposal = HistoryEntry(
            value=base["value"],
            status="disputed",
            turn_id=turn_id,
            at=self._clock.now(),
            reason=f"conflict:{base['contributor_id']}",
        )
        rec = self._put(
            prev.model_copy(
                update={
                    "status": "disputed",
                    "provenance": [*prev.provenance, prov],
                    "history": [*prev.history, proposal],
                }
            )
        )
        return WriteResult(True, "conflict", rec, prev)

    def _remove(
        self,
        slot_id: str,
        value: JsonValue,
        prev: SlotRecord | None,
        prev_filled: bool,
        prov: Provenance,
        turn_id: str | None,
    ) -> WriteResult:
        if prev is None or not prev_filled:
            return WriteResult(False, "nothing_to_remove", prev, prev)
        if isinstance(prev.value, list) and isinstance(value, list) and value:
            drop = {item_name(v) for v in value}
            kept = [v for v in prev.value if item_name(v) not in drop]
            if kept == prev.value:
                return WriteResult(False, "nothing_to_remove", prev, prev)
            if kept:
                history = [*prev.history, self._history(prev, "items_removed", turn_id)]
                rec = self._put(
                    prev.model_copy(
                        update={
                            "value": kept,
                            "history": history,
                            "turn_id": turn_id,
                            "provenance": [*prev.provenance, prov],
                        }
                    )
                )
                return WriteResult(True, "items_removed", rec, prev)
        history = [*prev.history, self._history(prev, "cleared", turn_id)]
        rec = self._put(
            SlotRecord(
                slot_id=slot_id,
                value=None,
                status="empty",
                provenance=[prov],
                confidence=0.0,
                history=history,
                turn_id=turn_id,
                contributor_id=prev.contributor_id,
            )
        )
        return WriteResult(True, "cleared", rec, prev)
