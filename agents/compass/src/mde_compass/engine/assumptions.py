"""Assumptions: propose, then approve (prompt 6.6).

Every slot the engine filled without the user is an :class:`AssumptionRecord` (claim, reason, basis, impact,
confidence, theme). At Gate 1 the "We assumed" review groups them by theme: low-impact ones can be accepted in
bulk, high-impact ones (catalog impact >= 4 or risk high) must each be confirmed or changed. Silence never
approves: only an explicit decision (``decide`` / ``accept_all_low``) moves ``decision`` off ``proposed``.

Ids are stable: an assumption keeps its ``ASM-###`` id and its decision while its slot stays assumed with the
same claim; when the slot becomes user-given a still-``proposed`` assumption is dropped (tombstoned in the
ledger); a decided one is kept as the audit trail of the decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog, SlotDef, load_catalog
from mde_compass.domain.brief import DICT_SECTIONS, AssumptionRecord, Brief, Theme
from mde_compass.domain.changes import PatchOp
from mde_compass.domain.common import HistoryEntry, Provenance, Strict
from mde_compass.domain.ids import IdLedger
from mde_compass.domain.slots import SlotRecord

TEXT_PATH = Path(__file__).resolve().parent / "assumption_text.yaml"


@lru_cache(maxsize=1)
def _text() -> dict[str, Any]:
    return yaml.safe_load(TEXT_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


THEME_BY_GROUP: dict[str, Theme] = dict(_text()["themes"])
THEME_ORDER: tuple[Theme, ...] = tuple(_text()["theme_order"])
HIGH_IMPACT_AT = 4


class AssumptionReview(Strict):
    """The "We assumed" review: groups by theme plus what is still undecided."""

    groups: dict[str, list[AssumptionRecord]]
    low_impact_ids: list[str]
    unreviewed_high_ids: list[str]


def _lang(language: str) -> str:
    return language if language in ("en", "hi") else "en"


def _value_text(value: JsonValue) -> str:
    if isinstance(value, list):
        return ", ".join(_value_text(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{_value_text(v)}" for v in value.values() if v not in (None, "", [], {}))
    return "" if value is None else str(value)


def _basis_of(record: SlotRecord) -> Literal["pattern", "model_knowledge", "assumed"]:
    for prov in record.provenance:
        if prov.type == "pattern" or prov.type == "model_knowledge":
            return prov.type
    return "assumed"


def is_high_impact(slot: SlotDef) -> bool:
    return slot.impact >= HIGH_IMPACT_AT or slot.risk == "high" or slot.must_confirm


def assumption_from_slot(
    slot_def: SlotDef, record: SlotRecord, ids: IdLedger, *, language: str, catalog: Catalog
) -> AssumptionRecord:
    """A proposed assumption for an assumed slot (allocates a new ``ASM-###`` id)."""
    del catalog  # reserved: strings may move into the catalog later
    lang = _lang(language)
    text = _text()
    basis = _basis_of(record)
    consumers = text["consumers"]
    names = ", ".join(
        dict.fromkeys(consumers[c][lang] for c in slot_def.consumers if c in consumers)
    )
    return AssumptionRecord(
        id=ids.allocate("ASM"),
        claim=text["claim"][lang].format(
            label=slot_def.label.get(lang), value=_value_text(record.value)
        ),
        reason=text["reason"][basis][lang],
        basis=basis,
        impact="high" if is_high_impact(slot_def) else "low",
        theme=THEME_BY_GROUP.get(slot_def.group, "Business"),
        slot_id=slot_def.id,
        decision="proposed",
        what_changes=text["what_changes"][lang].format(consumers=names or consumers["all"][lang]),
        tier="Default" if record.value not in (None, "", [], {}) else "Open",
        provenance=[Provenance(type=basis, ref="")],
        confidence=record.confidence,
        status="assumed",
    )


def _user_given(record: SlotRecord) -> bool:
    return record.status in ("confirmed", "volunteered")


def sync_assumptions(
    brief: Brief,
    store_records: Mapping[str, SlotRecord],
    catalog: Catalog | None = None,
    *,
    language: str = "en",
) -> Brief:
    """A copy of ``brief`` whose assumptions mirror the assumed slots (stable ids, decisions kept)."""
    cat = catalog or load_catalog()
    out = brief.model_copy(deep=True)
    by_slot = {a.slot_id: a for a in out.assumptions if a.slot_id}
    kept: list[AssumptionRecord] = [a for a in out.assumptions if not a.slot_id]
    seen: set[str] = set()
    for slot_id, record in store_records.items():
        slot = cat.slots.get(slot_id)
        if slot is None:
            continue
        existing = by_slot.get(slot_id)
        if record.status == "assumed" and record.value not in (None, "", [], {}):
            seen.add(slot_id)
            if existing is None:
                kept.append(
                    assumption_from_slot(slot, record, out.ids, language=language, catalog=cat)
                )
                continue
            fresh = assumption_from_slot(slot, record, IdLedger(), language=language, catalog=cat)
            same = fresh.claim == existing.claim
            kept.append(
                existing.model_copy(
                    update={
                        "claim": fresh.claim,
                        "reason": fresh.reason,
                        "basis": fresh.basis,
                        "impact": fresh.impact,
                        "theme": fresh.theme,
                        "confidence": fresh.confidence,
                        "what_changes": fresh.what_changes,
                        "decision": existing.decision if same else "proposed",
                    }
                )
            )
        elif existing is not None:
            # the slot is user-given, empty, disputed or has no value any more
            if existing.decision == "proposed":
                out.ids.tombstone(existing.id)
            else:
                kept.append(existing)  # a decided assumption stays as the audit trail
    for slot_id, existing in by_slot.items():
        if slot_id not in seen and existing not in kept and slot_id not in store_records:
            kept.append(existing)  # a slot not in the store snapshot: leave its assumption alone
    out.assumptions = sorted(kept, key=lambda a: a.id)
    return out


DERIVED_BASES: tuple[str, ...] = ("typical", "correction", "review")
"""Bases of assumptions that stand for no slot (ADR 0016): a typical detail nobody asked about, a correction we
made, a review finding. Code rebuilds the ``proposed`` ones at every compile; decided ones are kept."""


def claim_key(claim: str) -> str:
    return " ".join(claim.casefold().split())


def merge_derived(
    brief: Brief, fresh: Sequence[AssumptionRecord], bases: Iterable[str] = DERIVED_BASES
) -> Brief:
    """A copy of ``brief`` whose slot-less assumptions of ``bases`` are exactly ``fresh``, matched by claim text.

    A claim that is already there keeps its ``ASM-###`` id and its decision (a rejected typical detail is not
    proposed again; a still-``proposed`` one gets the fresh wording); a new claim gets a new id; a ``proposed``
    one that no longer applies is tombstoned; a decided one that no longer applies stays as the audit trail.
    Slot assumptions and assumptions of other bases are untouched.
    """
    wanted = set(bases)
    out = brief.model_copy(deep=True)
    mine = [a for a in out.assumptions if not a.slot_id and a.basis in wanted]
    others = [a for a in out.assumptions if a.slot_id or a.basis not in wanted]
    by_key = {claim_key(a.claim): a for a in mine}
    kept: list[AssumptionRecord] = []
    seen: set[str] = set()
    for item in fresh:
        key = claim_key(item.claim)
        if key in seen:
            continue
        seen.add(key)
        old = by_key.get(key)
        if old is None:
            kept.append(item.model_copy(update={"id": out.ids.allocate("ASM")}))
        elif old.decision == "proposed":
            kept.append(
                old.model_copy(
                    update={
                        "reason": item.reason,
                        "impact": item.impact,
                        "theme": item.theme,
                        "what_changes": item.what_changes,
                        "confidence": item.confidence,
                    }
                )
            )
        else:
            kept.append(old)
    for key, old in by_key.items():
        if key in seen:
            continue
        if old.decision == "proposed":
            out.ids.tombstone(old.id)
        else:
            kept.append(old)  # decided: stays as the audit trail
    out.assumptions = sorted([*others, *kept], key=lambda a: a.id)
    return out


def review(brief: Brief) -> AssumptionReview:
    """Group the assumptions by theme; list the low-impact ids and the unreviewed high-impact ids."""
    groups: dict[str, list[AssumptionRecord]] = {}
    for theme in THEME_ORDER:
        items = [a for a in brief.assumptions if a.theme == theme]
        if items:
            groups[theme] = items
    return AssumptionReview(
        groups=groups,
        low_impact_ids=[
            a.id for a in brief.assumptions if a.impact == "low" and a.decision == "proposed"
        ],
        unreviewed_high_ids=[
            a.id for a in brief.assumptions if a.impact == "high" and a.decision == "proposed"
        ],
    )


def _brief_value(brief: Brief, slot: SlotDef) -> JsonValue:
    section, _, key = slot.brief_path.partition(".")
    if section in DICT_SECTIONS:
        item = getattr(brief, section, {}).get(key)
        return item.value if item is not None else None
    return None


def _mark(
    asm: AssumptionRecord, decision: str, turn_id: str, now: datetime, quote: str
) -> AssumptionRecord:
    return asm.model_copy(
        update={
            "decision": decision,
            "history": [
                *asm.history,
                HistoryEntry(
                    value=asm.claim,
                    status=asm.status,
                    turn_id=turn_id,
                    at=now,
                    reason=f"was {asm.decision}",
                ),
            ],
            "provenance": [
                *asm.provenance,
                Provenance(type="user_tap", ref=turn_id, quote=quote, date=now),
            ],
        }
    )


def decide(
    brief: Brief,
    asm_id: str,
    decision: Literal["accept", "change"],
    value: JsonValue = None,
    *,
    turn_id: str,
    now: datetime,
    catalog: Catalog | None = None,
) -> tuple[Brief, list[PatchOp]]:
    """An explicit decision on one assumption.

    ``accept`` marks it accepted and returns a ``set`` op that re-affirms the slot value with ``user_tap``
    provenance (the row turns from assumed to confirmed); ``change`` marks it ``changed`` and returns the op
    that sets the user's new value. The returned brief only carries the updated assumption; the caller applies
    the ops through the patch engine (which bumps the version). Unknown id raises ``KeyError``.
    """
    cat = catalog or load_catalog()
    out = brief.model_copy(deep=True)
    index = next(
        (i for i, a in enumerate(out.assumptions) if a.id == out.ids.resolve(asm_id)), None
    )
    if index is None:
        raise KeyError(f"unknown assumption {asm_id}")
    asm = out.assumptions[index]
    slot = cat.slots.get(asm.slot_id or "")
    ops: list[PatchOp] = []
    evidence = [
        Provenance(
            type="user_tap",
            ref=turn_id,
            quote=asm.claim if decision == "accept" else _value_text(value),
            date=now,
        )
    ]
    if decision == "accept":
        out.assumptions[index] = _mark(asm, "accepted", turn_id, now, asm.claim)
        current = _brief_value(out, slot) if slot else None
        if slot is not None and current is not None:
            ops.append(PatchOp(op="set", path=slot.brief_path, value=current, provenance=evidence))
    else:
        out.assumptions[index] = _mark(asm, "changed", turn_id, now, _value_text(value))
        if slot is not None and value is not None:
            ops.append(
                PatchOp(
                    op="set",
                    path=slot.brief_path,
                    value=value,
                    old_value=_brief_value(out, slot),
                    provenance=evidence,
                )
            )
    return out, ops


def accept_all_low(brief: Brief, *, turn_id: str, now: datetime) -> Brief:
    """Bulk-accept every low-impact proposed assumption (high-impact ones are never touched)."""
    out = brief.model_copy(deep=True)
    out.assumptions = [
        _mark(a, "accepted", turn_id, now, "accepted all low-impact")
        if a.impact == "low" and a.decision == "proposed"
        else a
        for a in out.assumptions
    ]
    return out
