"""What becomes of a handoff-probe gap that was not fixed (ADR 0016): it is recorded, never a silent "degraded".

A probe (``cmp.probe_handoff``) fails when the downstream planner it stands for would have to guess. The engine first
tries to fix each gap (an assumed value, or one more question). A gap still open after the repair rounds used to end as
``meta.degraded = True``, which dropped one Gate 1 blocker and recorded nothing. Now every unfixed gap leaves a trace
the person sees at Gate 1:

* a gap with a suggested value becomes an assumption without a slot (basis ``gap``; ``high`` impact only when the touched
  slot has impact 4 or more or must be confirmed, so it then needs a decision);
* any other gap becomes a NON-BLOCKING open question carrying the probe's own question.

``gap_to_assumption`` is pure; ``record_gaps`` adds the rows to a copy of the Brief with stable ids and no repeats.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import AssumptionRecord, Brief, OpenQuestion
from mde_compass.domain.common import Provenance
from mde_compass.engine.assumptions import merge_derived
from mde_compass.engine.derived_assumptions import (
    PLACEHOLDER_ID,
    lang_of,
    theme_of,
    touches_important_slot,
)
from mde_compass.engine.ports import ProbeGap

BASIS = "gap"
QUESTION_CHARS = 300
PLACEHOLDER_QUESTION_ID = "Q-000"


def _text(value: JsonValue) -> str:
    if isinstance(value, list):
        return ", ".join(t for t in (_text(v) for v in value) if t)
    if isinstance(value, dict):
        return ", ".join(t for t in (_text(v) for v in value.values()) if t)
    return "" if value is None else str(value)


def gap_to_assumption(
    gap: ProbeGap, catalog: Catalog, *, language: str = "en"
) -> AssumptionRecord | OpenQuestion:
    """An assumption row for a gap with a suggested value, else a non-blocking open question (ids are placeholders)."""
    lang = lang_of(language)
    slot = catalog.slots.get(gap.slot_id or "")
    value = _text(gap.suggested_value)
    if gap.fix == "assume" and value.strip() and slot is not None:
        slots = [slot.id]
        return AssumptionRecord(
            id=PLACEHOLDER_ID,
            claim=catalog.t(
                "brief.v2.gap.assume", lang, what=slot.label.get(lang), value=value[:200]
            ),
            reason=catalog.t("brief.v2.gap.reason", lang),
            basis=BASIS,
            impact="high" if touches_important_slot(slots, catalog) else "low",
            theme=theme_of(slots, catalog),
            slot_id=None,
            decision="proposed",
            what_changes=catalog.t("brief.v2.gap.what_changes", lang),
            tier="Default",
            provenance=[Provenance(type="model_knowledge", ref=f"probe:{slot.id}")],
            confidence=0.5,
            status="assumed",
        )
    return OpenQuestion(
        id=PLACEHOLDER_QUESTION_ID,
        text=" ".join(gap.question.split())[:QUESTION_CHARS],
        blocking=False,
        owner="user",
        slot_id=gap.slot_id if slot is not None else None,
    )


def record_gaps(
    brief: Brief, gaps: Iterable[ProbeGap], catalog: Catalog, *, language: str = "en"
) -> Brief:
    """A copy of ``brief`` that records every gap: assumptions (basis ``gap``, rebuilt from ``gaps``: one that is no
    longer a gap and is still undecided goes away) and open questions (never repeated, never blocking)."""
    rows = [gap_to_assumption(g, catalog, language=language) for g in gaps]
    out = merge_derived(brief, [r for r in rows if isinstance(r, AssumptionRecord)], (BASIS,))
    known = {q.text.strip().casefold() for q in out.open_questions}
    for row in rows:
        if (
            isinstance(row, OpenQuestion)
            and row.text.strip()
            and row.text.strip().casefold() not in known
        ):
            known.add(row.text.strip().casefold())
            out.open_questions.append(row.model_copy(update={"id": out.ids.allocate("Q")}))
    return out
