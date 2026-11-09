"""From the fact ledger to the Brief (build prompt 02, section 3.3 and 3.4; ADR 0015).

At the start of Brief review the whole active ledger is compiled (``cmp.compile_brief``; in chunks plus one
consistency pass when it is over the size ceiling), then, in code:

* an item fills a slot only if the slot is EMPTY: what the person said, and what was already projected from
  facts, is never overwritten by a compile;
* every active fact ends up in ``domain_facts`` (placed by the compiler), referenced by a slot's evidence, or
  marked ``not_used`` with a reason. A fact that is none of these is a defect: it is placed anyway, flagged and
  counted (``meta.interview.facts_unreferenced``), so nothing the person said is ever lost;
* a Locked item needs a verified quote (``Fact.supports_locked``); anything else is Default.

Nothing here is chosen by a model beyond the values and the placements it proposes; tiers, coverage and the
final shape of the section are computed by code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mde_compass.domain.brief import DomainFact
from mde_compass.domain.common import Provenance, ProvenanceType
from mde_compass.domain.interview import Fact
from mde_compass.engine import fact_projector
from mde_compass.engine import facts as facts_mod
from mde_compass.engine.context_pack import estimate_tokens, full_ledger
from mde_compass.engine.interview_support import text_of
from mde_compass.engine.ports import (
    BriefCompilation,
    CompiledItem,
    CompileRequest,
    FactPlacement,
    NotUsedFact,
)

if TYPE_CHECKING:  # pragma: no cover
    from mde_compass.engine.interview import InterviewController
    from mde_compass.engine.service import _Turn

PROVENANCE_BY_ORIGIN: dict[str, ProvenanceType] = {
    "stated": "user_text",
    "tap": "user_tap",
    "document": "document",
    "assumption": "assumed",
}


def _chunks(
    active: list[Fact], ceiling_tokens: int, chars_per_token: int, size: int
) -> list[list[Fact]]:
    """One chunk when the ledger fits, else consecutive chunks of at most ``size`` facts."""
    if estimate_tokens(full_ledger(active), chars_per_token) <= ceiling_tokens:
        return [active]
    return [active[i : i + size] for i in range(0, len(active), max(1, size))]


def _merge(parts: list[BriefCompilation]) -> BriefCompilation:
    """Combine chunk results: the newest item for a path wins, a fact is placed or not-used once."""
    items: dict[str, CompiledItem] = {}
    placements: dict[str, FactPlacement] = {}
    not_used: dict[str, NotUsedFact] = {}
    for part in parts:
        for item in part.items:
            items[item.path] = item
        for p in part.placements:
            placements.setdefault(p.fact_id, p)
        for n in part.not_used:
            not_used.setdefault(n.fact_id, n)
    return BriefCompilation(
        items=list(items.values()),
        placements=list(placements.values()),
        not_used=list(not_used.values()),
        summary=next((p.summary for p in parts if p.summary), ""),
        pitch=next((p.pitch for p in parts if p.pitch), ""),
        used_fallback=any(p.used_fallback for p in parts),
    )


def _qa_compact(turn: _Turn) -> str:
    lines = []
    for qa in turn.memory.qa:
        if qa.status in ("answered", "skipped", "you_decide"):
            answer = text_of(qa.answer) or (qa.custom_text or "")
            lines.append(
                f"{qa.card_id} {qa.card.intent or qa.card.question} -> {qa.status}: {answer}"[:240]
            )
    return "\n".join(lines)


def provenance_of(fact: Fact) -> Provenance:
    """A domain fact's own provenance: where it came from (the event, the card or the source), with its quote."""
    kind = PROVENANCE_BY_ORIGIN.get(fact.origin, "user_text")
    if fact.unverified_quote and kind != "assumed":
        kind = "model_knowledge"
    return Provenance(
        type=kind,
        ref=fact.source_ref or fact.id,
        quote=fact.quote[:300],
        date=fact.created_at,
    )


def domain_fact(fact: Fact, topic: str, consumers: list[str]) -> DomainFact:
    verified = fact.supports_locked()
    return DomainFact(
        id=fact.id,
        type=fact.type,
        statement=fact.statement,
        topics=[topic] if topic else list(fact.topics),
        entities=list(fact.entities),
        consumers=consumers or list(fact.consumers) or ["all"],
        source=fact.qa_card_id or fact.source_ref,
        quote=fact.quote,
        tier="Locked" if verified else "Default",
        provenance=[provenance_of(fact)],
        confidence=fact.confidence,
        status="assumed"
        if fact.origin == "assumption"
        else ("confirmed" if fact.origin == "tap" else "volunteered"),
    )


def referenced_by_slots(turn: _Turn) -> set[str]:
    """Fact ids cited by a slot's evidence (the projector writes ``ref = FCT-###``)."""
    refs: set[str] = set()
    for rec in turn.slots.all().values():
        for prov in rec.provenance:
            if prov.ref.startswith("FCT-"):
                refs.add(prov.ref)
    return refs


async def compile_ledger(ctl: InterviewController, turn: _Turn) -> None:
    """Compile the ledger into the Brief at review (planner engine only)."""
    svc = ctl.svc
    assert svc.ports is not None
    mem = turn.memory
    now = svc.clock.now()
    active = mem.active_facts()
    cfg = svc.config.context
    statuses = ctl.statuses(turn)
    from mde_compass.engine import needs as needs_mod

    base = CompileRequest(
        language=turn.lang,
        ledger="",
        needs_table=needs_mod.needs_table(statuses, svc.catalog, only_open=False),
        assumptions=[a.claim for a in turn.brief.assumptions],
        asked_list="\n".join(f"{q.card_id} {q.card.intent} ({q.status})" for q in mem.qa),
        qa_compact=_qa_compact(turn),
    )
    ctx = svc._ctx(turn)
    parts: list[BriefCompilation] = []
    if active:
        for chunk in _chunks(
            active, cfg.compile_ceiling, cfg.chars_per_token, cfg.compile_chunk_facts
        ):
            parts.append(
                await svc.ports.brief_compiler.compile_brief(
                    ctx, base.model_copy(update={"ledger": full_ledger(chunk)})
                )
            )
        if len(parts) > 1:  # one consistency pass over the assembled draft
            draft = "\n".join(
                f"{i.path} = {text_of(i.value)} ({', '.join(i.fact_ids)})"
                for p in parts
                for i in p.items
            )
            parts.append(
                await svc.ports.brief_compiler.compile_brief(
                    ctx, base.model_copy(update={"ledger": full_ledger(active), "draft": draft})
                )
            )
    compiled = _merge(parts) if parts else BriefCompilation()
    if compiled.used_fallback:
        ctl.count_fallback(turn)

    # 1. values: only into EMPTY slots, with evidence that comes from the facts
    for item in compiled.items:
        slot = svc.catalog.slots.get(item.path)
        if slot is None or turn.slots.filled(item.path):
            continue
        cited = [
            f
            for f in (mem.fact(i) for i in item.fact_ids)
            if f is not None and f.status == "active"
        ]
        if not cited:
            continue
        verified = all(f.supports_locked() for f in cited)
        prov = fact_projector.provenance_for(cited[0], now=now)
        turn.slots.write(
            item.path,
            item.value,
            state="volunteered" if verified else "assumed",
            provenance=prov
            if verified
            else Provenance(type="model_knowledge", ref=cited[0].id, quote="", date=now),
            confidence=item.confidence,
            turn_id=turn.turn_id,
        )

    # 2. not_used: small talk and the like, each with a reason
    for n in compiled.not_used:
        fact = mem.fact(n.fact_id)
        if fact is not None and fact.status == "active" and n.reason.strip():
            mem.update_fact(facts_mod.mark_not_used(fact, n.reason.strip(), now))

    # 3. coverage: every active fact is placed, cited by a slot, or not_used; leftovers are placed and counted
    placed = {p.fact_id: p for p in compiled.placements}
    cited_by_items = {i for item in compiled.items for i in item.fact_ids}
    still_active = mem.active_facts()
    report = facts_mod.fact_coverage(
        still_active, referenced_by_slots(turn) | cited_by_items | set(placed)
    )
    leftovers = set(report.unreferenced)
    rows: list[DomainFact] = []
    for fact in still_active:
        if fact.id in placed:
            rows.append(domain_fact(fact, placed[fact.id].topic, list(placed[fact.id].consumers)))
        elif fact.id in leftovers:
            rows.append(domain_fact(fact, fact.topics[0] if fact.topics else fact.type, []))
    turn.brief.domain_facts = sorted(rows, key=lambda r: r.id)
    block = ctl.block(turn)
    total = max(len(still_active), 1)
    turn.brief.meta.interview = block.model_copy(
        update={
            "facts_unreferenced": len(leftovers),
            "coverage": round(1.0 - len(leftovers) / total, 4) if still_active else 1.0,
        }
    )
    if compiled.summary and not turn.brief.summary:
        turn.brief.summary = compiled.summary
    if compiled.pitch and not turn.brief.pitch:
        turn.brief.pitch = compiled.pitch
    ctl.touch(turn)
    svc._rebuild(turn)
