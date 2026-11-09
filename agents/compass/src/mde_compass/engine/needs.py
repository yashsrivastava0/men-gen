"""Needs status: what the downstream agents still lack, computed by code each round (build prompt 02, section 2;
ADR 0015 point 2).

``catalog/slots.yaml`` is the Needs Registry; each need may carry an ``answer_spec`` (what a sufficient answer
contains: named ``aspects``, the ``fact_types`` that count, ``min_facts``). The interpreter reports which aspects an
answer covered by tagging its facts ``need_id:aspect`` (``need_id:*`` means a complete answer, which is what a tapped
option is). *Code* turns that into a state per need:

* ``satisfied``: the slot is confirmed or volunteered with a value AND (the need has no aspects, OR every aspect is
  covered, OR a fact covers ``*``, OR the covering facts of the right ``fact_types`` reach ``min_facts``; that last
  path needs ``min_facts >= 2``, because one fact is the default and would make every aspect list moot);
* ``partial``: there is a user value (or a disputed one), or some aspects are covered, but not enough;
* ``assumed``: only an assumed value (or an open assumption) stands in for it, no user fact;
* ``missing``: nothing.

Nothing is mutated. A need whose ``answer_spec`` is ``None`` is treated as having no aspects. Slots of the ``org``
group exist only in enterprise mode; ``ask_if`` ordering is the planner's business and is not applied here.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping, Sequence

from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.config import CompassConfig, QuestionPolicy
from mde_compass.domain.brief import AssumptionRecord
from mde_compass.domain.interview import AnswerSpec, Fact, NeedState, NeedStatus
from mde_compass.domain.slots import SlotRecord
from mde_compass.engine.facts import fact_number
from mde_compass.engine.slots import SlotStore, is_blank
from mde_compass.engine.voi import ENTERPRISE_GROUPS

COMPLETE = "*"
USER_VALUE_STATES = frozenset({"confirmed", "volunteered"})
CLOSED_ASSUMPTION_DECISIONS = frozenset({"rejected"})
VALUE_TEXT_CHARS = 80


def high_impact_at(config: QuestionPolicy | CompassConfig) -> int:
    """The catalog impact from which a need counts as high-impact (accepts the policy or the whole config)."""
    return (
        config.questions.high_impact_at
        if isinstance(config, CompassConfig)
        else config.high_impact_at
    )


def split_aspect(entry: str) -> tuple[str, str] | None:
    """``"audience.segments:goal"`` -> ``("audience.segments", "goal")``; the last colon splits, so
    ``"domain:pricing:*"`` -> ``("domain:pricing", "*")``. Malformed entries give ``None``."""
    need_id, sep, aspect = entry.rpartition(":")
    if not sep or not need_id.strip() or not aspect.strip():
        return None
    return need_id.strip(), aspect.strip()


def _records(slots: SlotStore | Mapping[str, SlotRecord | None]) -> dict[str, SlotRecord | None]:
    return dict(slots.all()) if isinstance(slots, SlotStore) else dict(slots)


def _claims(facts: Iterable[Fact]) -> dict[str, list[tuple[Fact, str]]]:
    """Active facts' aspect entries grouped by need id."""
    out: dict[str, list[tuple[Fact, str]]] = {}
    for fact in facts:
        if fact.status != "active":
            continue
        for entry in fact.aspects:
            parts = split_aspect(entry)
            if parts is not None:
                out.setdefault(parts[0], []).append((fact, parts[1]))
    return out


def _assumption_for(slot_id: str, assumptions: Sequence[AssumptionRecord]) -> str | None:
    for a in assumptions:
        if (
            a.slot_id == slot_id
            and a.decision not in CLOSED_ASSUMPTION_DECISIONS
            and a.claim.strip()
        ):
            return a.claim
    return None


def _min_facts_reached(spec: AnswerSpec, covering: Sequence[Fact]) -> bool:
    if spec.min_facts < 2:
        return False
    right = [f for f in covering if not spec.fact_types or f.type in spec.fact_types]
    return len(right) >= spec.min_facts


def _status_of(
    slot: SlotDef,
    record: SlotRecord | None,
    claims: Sequence[tuple[Fact, str]],
    effect_fact_ids: Collection[str],
    assumption: str | None,
    asked: int,
    followups_used: int,
) -> NeedStatus:
    spec = slot.answer_spec
    aspects = list(spec.aspects) if spec is not None else []
    claimed = {a for _, a in claims if a != COMPLETE}
    complete = any(a == COMPLETE for _, a in claims)
    covered = [a for a in aspects if a in claimed]
    missing = [a for a in aspects if a not in claimed]
    covering = list({f.id: f for f, _ in claims}.values())

    filled = record is not None and record.status != "empty" and not is_blank(record.value)
    user_value = filled and record is not None and record.status in USER_VALUE_STATES
    disputed = filled and record is not None and record.status == "disputed"

    enough = (
        not aspects
        or not missing
        or complete
        or (spec is not None and _min_facts_reached(spec, covering))
    )
    state: NeedState
    if user_value and enough:
        state = "satisfied"
    elif user_value or disputed or covered or complete:
        state = "partial"
    elif (filled and record is not None and record.status == "assumed") or assumption is not None:
        state = "assumed"
    else:
        state = "missing"

    assumed_value: JsonValue = None
    if filled and record is not None and record.status == "assumed":
        assumed_value = record.value
    elif assumption is not None:
        assumed_value = assumption

    if complete:
        shown_covered, shown_missing = list(aspects), []
    elif state == "satisfied":
        shown_covered, shown_missing = covered, []
    else:
        shown_covered, shown_missing = covered, missing
    fact_ids = sorted({f.id for f in covering} | set(effect_fact_ids), key=fact_number)
    return NeedStatus(
        need_id=slot.id,
        state=state,
        impact=slot.impact,
        level=slot.level,
        covered_aspects=shown_covered,
        missing_aspects=shown_missing,
        fact_ids=fact_ids,
        assumed_value=assumed_value,
        asked=asked,
        followups_used=followups_used,
    )


def compute(
    catalog: Catalog,
    facts: Iterable[Fact],
    slots: SlotStore | Mapping[str, SlotRecord | None],
    *,
    assumptions: Sequence[AssumptionRecord] = (),
    asked: Mapping[str, int] | None = None,
    followups: Mapping[str, int] | None = None,
    mode: str = "guided",
) -> dict[str, NeedStatus]:
    """The status of every need applicable in ``mode`` (org needs only in ``enterprise``), in dependency order."""
    active = [f for f in facts if f.status == "active"]
    records = _records(slots)
    claims = _claims(active)
    effect_ids: dict[str, set[str]] = {}
    for fact in active:
        for effect in fact.effects:
            effect_ids.setdefault(effect.slot_id, set()).add(fact.id)
    out: dict[str, NeedStatus] = {}
    for slot_id in catalog.slot_ids_by_order():
        slot = catalog.slots[slot_id]
        if slot.group in ENTERPRISE_GROUPS and mode != "enterprise":
            continue
        out[slot_id] = _status_of(
            slot,
            records.get(slot_id),
            claims.get(slot_id, ()),
            effect_ids.get(slot_id, set()),
            _assumption_for(slot_id, assumptions),
            (asked or {}).get(slot_id, 0),
            (followups or {}).get(slot_id, 0),
        )
    return out


# ---------------------------------------------------------------------------------------------- views
def _short_value(value: JsonValue) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= VALUE_TEXT_CHARS else text[: VALUE_TEXT_CHARS - 1] + "…"


def _ordered(statuses: Mapping[str, NeedStatus], catalog: Catalog) -> list[NeedStatus]:
    known = [statuses[s] for s in catalog.slot_ids_by_order() if s in statuses]
    extra = sorted(n for n in statuses if n not in catalog.slots)
    return [*known, *(statuses[n] for n in extra)]


def needs_table(
    statuses: Mapping[str, NeedStatus], catalog: Catalog, *, only_open: bool = True
) -> str:
    """Compact lines for the prompt: ``id | level | impact | state | sufficient answer | missing | assumed``.

    ``generated`` needs are never asked, so they are left out. ``only_open`` hides the satisfied ones.
    """
    lines: list[str] = []
    for status in _ordered(statuses, catalog):
        if status.level == "generated" or (only_open and status.state == "satisfied"):
            continue
        parts = [status.need_id, status.level or "-", f"impact {status.impact}", status.state]
        slot = catalog.slots.get(status.need_id)
        if slot is not None and slot.answer_spec is not None:
            parts.append("sufficient: " + " ".join(slot.answer_spec.sufficient.split()))
        if status.missing_aspects:
            parts.append("missing: " + ", ".join(status.missing_aspects))
        if status.assumed_value is not None:
            parts.append("assumed: " + _short_value(status.assumed_value))
        if status.asked:
            parts.append(f"asked: {status.asked}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def expert_gap_obligations(
    statuses: Mapping[str, NeedStatus], catalog: Catalog, config: QuestionPolicy | CompassConfig
) -> list[str]:
    """Needs the interview owes an answer or a visible assumption: ``must`` level or high impact, not yet
    satisfied or assumed. These are what the planner must cover, and what ``coverage_issues`` checks."""
    threshold = high_impact_at(config)
    return [
        s.need_id
        for s in _ordered(statuses, catalog)
        if s.level != "generated"
        and (s.level == "must" or s.impact >= threshold)
        and s.state not in ("satisfied", "assumed")
    ]
