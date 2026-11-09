"""Pure helpers of the interview controller: what the model is shown, how a request is hashed, how facts are built.

Nothing here talks to a model or a store. The controller (``engine.interview``) calls these and the ports.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import cast

from pydantic import JsonValue

from mde_compass.catalog import Catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.cards import Card
from mde_compass.domain.interview import (
    Fact,
    FactEffect,
    FactType,
    InterviewBlock,
    NeedStatus,
    QaRecord,
)
from mde_compass.engine import quotes
from mde_compass.engine.ports import ExtractedFact, NeedBrief, PlanRequest, TurnUnderstanding
from mde_compass.engine.slots import SlotStore

CLOSED_QA = ("open", "dropped", "superseded")
_FACT_TYPE_BY_PREFIX: tuple[tuple[str, FactType], ...] = (
    ("roles.", "role"),
    ("goals.", "goal"),
    ("constraints.", "constraint"),
    ("glossary.", "glossary"),
    ("scope.must_not", "exclusion"),
    ("scope.references", "reference"),
    ("integrations.", "requirement"),
    ("scope.", "requirement"),
    ("context.", "context"),
    ("audience.", "context"),
    ("locales", "context"),
    ("look.", "preference"),
    ("brand.", "preference"),
)


def text_of(value: JsonValue) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(text_of(v) for v in value if text_of(v))
    if isinstance(value, dict):
        for key in ("name", "label", "text", "city"):
            if isinstance(value.get(key), str):
                return str(value[key])
        return ", ".join(f"{k}: {text_of(v)}" for k, v in value.items() if text_of(v))
    return str(value)


def fact_type_for(slot_id: str) -> FactType:
    for prefix, kind in _FACT_TYPE_BY_PREFIX:
        if slot_id.startswith(prefix):
            return kind
    return "requirement"


def need_briefs(
    statuses: Mapping[str, NeedStatus], catalog: Catalog, language: str
) -> list[NeedBrief]:
    """Open needs first (missing, then partial, by impact), then the rest, as the model sees them."""
    order = {sid: i for i, sid in enumerate(catalog.slot_ids_by_order())}
    rank = {"missing": 0, "partial": 1, "assumed": 2, "satisfied": 3}
    out: list[NeedBrief] = []
    for sid, st in sorted(
        statuses.items(), key=lambda kv: (rank[kv[1].state], -kv[1].impact, order.get(kv[0], 999))
    ):
        slot = catalog.slots.get(sid)
        if slot is None or slot.answer_spec is None or slot.level == "generated":
            continue
        # asked twice and still not answered well: stop offering it (it ends as a visible assumption)
        state = "assumed" if st.state in ("missing", "partial") and st.asked >= 2 else st.state
        out.append(
            NeedBrief(
                need_id=sid,
                label=slot.label.get(language),
                sufficient=slot.answer_spec.sufficient,
                aspects=list(slot.answer_spec.aspects),
                state=state,
                missing_aspects=list(st.missing_aspects),
                impact=slot.impact,
                assumed_value=st.assumed_value,
            )
        )
    return out


def style_notes(catalog: Catalog, need_ids: Iterable[str], limit: int = 5) -> list[str]:
    """Frame style guides and example phrasings for the frames that cover these needs (never a script)."""
    wanted = set(need_ids)
    notes: list[str] = []
    for frame in sorted(catalog.frames.values(), key=lambda f: f.order):
        if not wanted & set(frame.slots) or not frame.style_guide:
            continue
        examples = "; ".join(frame.examples[:2])
        notes.append(f"{frame.id}: {frame.style_guide} Examples: {examples}")
        if len(notes) >= limit:
            break
    return notes


def project_card(brief: Brief, language: str, mode: str) -> dict[str, JsonValue]:
    """The project card sent with every call: the idea, its kind, place, audience, apps, mode and language."""

    def item(section: Mapping[str, object], key: str) -> str:
        rec = section.get(key)
        return text_of(getattr(rec, "value", None)) if rec is not None else ""

    profile = brief.meta.situation_profile
    return {
        "idea": item(brief.idea, "summary"),
        "category": item(brief.idea, "category"),
        "subcategory": item(brief.idea, "subcategory"),
        "geography": item(brief.context, "geo"),
        "audience": item(brief.audience, "segments"),
        "apps": ", ".join(a.name for a in brief.apps),
        "mode": mode,
        "language": language,
        "situation": profile.model_dump(mode="json") if profile is not None else {},
    }


def inputs_hash(request: PlanRequest) -> str:
    """Identical inputs, identical hash: a retried turn finds its stored batch instead of paying again."""
    return hashlib.sha256(
        json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def next_card_number(cards: Iterable[Card], qa: Iterable[QaRecord]) -> int:
    best = 0
    for cid in [c.id for c in cards] + [q.card_id for q in qa]:
        tail = cid.rsplit("-", 1)[-1]
        if tail.isdigit():
            best = max(best, int(tail))
    return best + 1


def counts_by_need(qa: Sequence[QaRecord], *, followups_only: bool = False) -> dict[str, int]:
    """Questions put to the person per need (shown ones only); with ``followups_only`` just the follow-ups."""
    out: dict[str, int] = {}
    for rec in qa:
        if rec.status in CLOSED_QA:
            continue
        if followups_only and rec.card.origin != "followup":
            continue
        for need in rec.card.need_ids:
            out[need] = out.get(need, 0) + 1
    return out


def refresh_block(
    block: InterviewBlock, qa: Sequence[QaRecord], facts: Sequence[Fact], batches: int
) -> InterviewBlock:
    """Recount the audit block from the ledgers (counts only; the fallback count and stop reason are kept)."""
    shown = [q for q in qa if q.status not in CLOSED_QA]
    active = [f for f in facts if f.status == "active"]
    return block.model_copy(
        update={
            "questions_asked": len(shown),
            "followups": sum(1 for q in shown if q.card.origin == "followup"),
            "answered": sum(1 for q in shown if q.status == "answered"),
            "skipped": sum(1 for q in shown if q.status == "skipped"),
            "assumed": sum(1 for q in shown if q.status in ("you_decide", "assumed")),
            "batches": batches,
            "facts_active": len(active),
            "facts_not_used": sum(1 for f in facts if f.status == "not_used"),
            "question_ids": [q.card_id for q in qa if q.status not in ("dropped", "superseded")],
        }
    )


# -------------------------------------------------------------------------------------- building facts
def build_fact(
    ef: ExtractedFact,
    *,
    fact_id: str,
    sources: Sequence[str],
    source_ref: str,
    card_id: str | None,
    origin: str,
    now: datetime,
    min_quote_chars: int,
) -> Fact:
    """An interpreter's statement as a ledger fact. The quote is verified here, in code, against the source text;
    one that is not found is kept but flagged and can never support a Locked item."""
    quote = ef.quote.strip()[:500]
    verified = bool(quote) and quotes.verify_against(quote, sources, min_chars=min_quote_chars)
    return Fact(
        id=fact_id,
        type=ef.type,
        statement=ef.statement.strip()[:1000] or "(empty)",
        topics=list(ef.topics),
        entities=list(ef.entities),
        consumers=list(ef.consumers),
        confidence=ef.confidence,
        status="active",
        origin=origin,  # type: ignore[arg-type]
        source_ref=source_ref,
        qa_card_id=card_id,
        quote=quote,
        unverified_quote=bool(quote) and not verified,
        effects=list(ef.effects),
        aspects=list(ef.aspects),
        created_at=now,
        updated_at=now,
    )


def derived_facts(
    understood: TurnUnderstanding,
    covered_slots: set[str],
    catalog: Catalog,
    language: str,
    *,
    complete: bool,
    close_others_at: float | None = None,
    own_needs: frozenset[str] = frozenset(),
) -> list[ExtractedFact]:
    """Slot updates no extracted fact accounts for become facts too, so the ledger is complete whatever the
    interpreter returned. A tap (``complete``) answers its need fully; a confident, quoted update for a need the
    question did not ask about (``close_others_at``, ``own_needs``) closes that need too: the person said it."""
    out: list[ExtractedFact] = []
    for upd in understood.slot_updates:
        slot = catalog.slots.get(upd.slot_id)
        if slot is None or upd.slot_id in covered_slots:
            continue
        mode = "remove" if upd.remove else "set"
        label = slot.label.get(language)
        statement = f"{label}: {text_of(upd.value)}" if not upd.remove else f"{label}: not wanted"
        out.append(
            ExtractedFact(
                type=fact_type_for(upd.slot_id),
                statement=statement,
                quote=upd.quote,
                topics=[slot.group],
                consumers=[c for c in slot.consumers if c != "all"] or ["all"],
                confidence=upd.confidence,
                aspects=[f"{upd.slot_id}:*"]
                if complete
                or (
                    close_others_at is not None
                    and upd.slot_id not in own_needs
                    and bool(upd.quote.strip())
                    and upd.confidence >= close_others_at
                    and not upd.remove
                )
                else [],
                effects=[
                    FactEffect(
                        slot_id=upd.slot_id,
                        value_json=json.dumps(upd.value, ensure_ascii=False),
                        mode=cast(str, mode),  # type: ignore[arg-type]
                    )
                ],
            )
        )
    return out


def slots_of(facts: Iterable[Fact | ExtractedFact]) -> set[str]:
    return {e.slot_id for f in facts for e in f.effects}


def values_snapshot(slots: SlotStore, slot_ids: Iterable[str]) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {}
    for sid in slot_ids:
        rec = slots.get(sid)
        if rec is not None:
            out[sid] = rec.value
    return out
