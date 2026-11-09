"""``CompileAdapter``: the engine's ``BriefCompiler`` port over the ``cmp.compile_brief`` call.

The model's output is never trusted as it stands. The adapter

1. offers only catalog slots as targets (restricted to ``sections`` in a per-section compile, org slots only in
   enterprise mode, generated slots never) and redacts secrets from everything that carries the user's words;
2. drops every item whose value does not fit its slot's ``value_schema`` (``understand_turn.values``), whose path is
   not a target, or that cites no fact of the ledger; unknown fact ids are removed everywhere;
3. COMPLETES coverage: every fact of the ledger that no item, placement or not-used entry mentions is placed (topic
   from its ``[type|topic]`` tag, consumers from the line when it names some, else ``all``), so a gap in the model's
   answer never loses a fact. ``to_compilation`` also returns the ids it had to place this way; the engine counts
   them as unreferenced (they were not read by the model).

Tier is not decided here: items carry fact ids only, and the engine derives Locked / Default / Open from the facts'
verified quotes (``Fact.supports_locked``).
"""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.compile_brief.ledger import (
    ALL,
    CONSUMERS,
    LedgerFact,
    chunk_text,
    join_text,
    parse_ledger,
)
from mde_compass.calls.compile_brief.schema import (
    CompileInput,
    CompileOutput,
    ItemWire,
    NeedWire,
    PlacementWire,
    TargetWire,
)
from mde_compass.calls.compile_brief.spec import SPEC
from mde_compass.calls.understand_turn.values import ValueError_, parse_json, validate_value
from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.engine.facts import fact_number
from mde_compass.engine.ports import (
    BriefCompilation,
    CompiledItem,
    CompileRequest,
    FactPlacement,
    NotUsedFact,
    TurnContext,
)
from mde_compass.engine.voi import ENTERPRISE_GROUPS
from mde_core.logging import get_logger
from mde_core.redact import redact_text

log = get_logger(__name__)

_SHAPED_BY_FIELDS = ("object_list", "object", "geo")
_SHAPED_BY_OPTIONS = ("enum", "enum_list")


def _section_of(slot: SlotDef) -> str:
    return slot.brief_path.split(".", 1)[0]


def select_targets(catalog: Catalog, sections: list[str], mode: str) -> list[SlotDef]:
    """The slots the call may fill, in dependency order. ``sections`` (empty = all) names Brief sections
    (``audience``, ``scope``), slot groups (``auth``) or slot ids."""
    wanted = {s.strip() for s in sections if s.strip()}
    chosen: list[SlotDef] = []
    for slot_id in catalog.slot_ids_by_order():
        slot = catalog.slots[slot_id]
        if slot.level == "generated":
            continue  # computed by code (research requests), never written from facts
        if slot.group in ENTERPRISE_GROUPS and mode != "enterprise":
            continue
        if wanted and not ({_section_of(slot), slot.group, slot.id} & wanted):
            continue
        chosen.append(slot)
    return chosen


def _target(slot: SlotDef) -> TargetWire:
    kind = slot.value_schema.kind
    return TargetWire(
        path=slot.id,
        label=slot.label.en,
        value_kind=kind,
        options=list(slot.value_schema.options) if kind in _SHAPED_BY_OPTIONS else [],
        fields=list(slot.value_schema.fields) if kind in _SHAPED_BY_FIELDS else [],
    )


def _clean(text: str) -> list[str]:
    return chunk_text(redact_text(text)[0])


def build_input(catalog: Catalog, request: CompileRequest, *, mode: str = "guided") -> CompileInput:
    """The wire input. Everything that carries the user's words is secret-redacted, then cut into pieces."""
    slots = select_targets(catalog, request.sections or [], mode)
    return CompileInput(
        language=request.language,
        ledger=_clean(request.ledger),
        needs_table=_clean(request.needs_table),
        needs=[
            NeedWire(need_id=s.id, label=s.label.en, sufficient=s.answer_spec.sufficient)
            for s in slots
            if s.answer_spec is not None
        ],
        assumptions=[redact_text(a)[0] for a in request.assumptions if a.strip()],
        asked_list=_clean(request.asked_list),
        qa_compact=_clean(request.qa_compact),
        sections=[s.strip() for s in request.sections or [] if s.strip()],
        draft=_clean(request.draft or ""),
        targets=[_target(s) for s in slots],
    )


# ------------------------------------------------------------------------------------- wire to domain
def _confidence(value: float) -> float:
    return 0.5 if value != value else min(1.0, max(0.0, value))  # NaN-safe clamp


def _newest(fact_ids: list[str]) -> int:
    return max((fact_number(f) for f in fact_ids), default=0)


def _items(
    slots: dict[str, SlotDef], known: dict[str, LedgerFact], wire: list[ItemWire]
) -> list[CompiledItem]:
    """Valid items only; one item per path (the one resting on the newest fact wins)."""
    best: dict[str, CompiledItem] = {}
    for w in wire:
        slot = slots.get(w.path)
        if slot is None:
            continue
        fact_ids = [f for f in dict.fromkeys(w.fact_ids) if f in known]
        if not fact_ids:
            continue  # a value with no fact behind it is an invented value
        try:
            value = validate_value(slot, parse_json(w.value_json))
        except ValueError_:
            continue
        item = CompiledItem(
            path=w.path, value=value, fact_ids=fact_ids, confidence=_confidence(w.confidence)
        )
        old = best.get(w.path)
        if old is None or _newest(item.fact_ids) >= _newest(old.fact_ids):
            best[w.path] = item
    return list(best.values())


def _consumers(names: list[str], fallback: tuple[str, ...]) -> list[str]:
    """The valid consumer names (the model's, else the line's), ``all`` swallowing the rest; never empty."""
    chosen = list(dict.fromkeys(n for n in names if n in CONSUMERS)) or list(fallback)
    return [ALL] if ALL in chosen or not chosen else chosen


def _placements(known: dict[str, LedgerFact], wire: list[PlacementWire]) -> list[FactPlacement]:
    """Placements of real facts, one per fact (repeats are merged), with a topic and valid consumers."""
    merged: dict[str, tuple[str, list[str]]] = {}
    for w in wire:
        fact = known.get(w.fact_id)
        if fact is None:
            continue
        topic, consumers = merged.get(w.fact_id, ("", []))
        merged[w.fact_id] = (topic or w.topic.strip(), [*consumers, *w.consumers])
    return [
        FactPlacement(
            fact_id=fid,
            topic=topic or known[fid].topic,
            consumers=_consumers(consumers, known[fid].consumers),
        )
        for fid, (topic, consumers) in merged.items()
    ]


def to_compilation(
    catalog: Catalog, inp: CompileInput, out: CompileOutput
) -> tuple[BriefCompilation, tuple[str, ...]]:
    """Wire output to the domain compilation, plus the ids of facts the model mentioned nowhere (placed by code)."""
    facts = parse_ledger(join_text(inp.ledger))
    known = {f.fact_id: f for f in facts}
    slots = {t.path: catalog.slots[t.path] for t in inp.targets if t.path in catalog.slots}

    items = _items(slots, known, out.items)
    placements = _placements(known, out.placements)
    cited = {fid for item in items for fid in item.fact_ids}
    placed = {p.fact_id for p in placements}

    not_used: dict[str, NotUsedFact] = {}
    for n in out.not_used:
        reason = " ".join(n.reason.split())
        if n.fact_id in known and reason and n.fact_id not in cited | placed:
            not_used.setdefault(n.fact_id, NotUsedFact(fact_id=n.fact_id, reason=reason))

    covered = cited | placed | set(not_used)
    leftovers = [f for f in facts if f.fact_id not in covered]
    placements.extend(
        FactPlacement(fact_id=f.fact_id, topic=f.topic, consumers=_consumers([], f.consumers))
        for f in leftovers
    )
    compilation = BriefCompilation(
        items=items,
        placements=placements,
        not_used=list(not_used.values()),
        summary=" ".join(out.summary.split()),
        pitch=out.pitch.strip(),
    )
    return compilation, tuple(f.fact_id for f in leftovers)


class CompileAdapter:
    """Implements ``mde_compass.engine.ports.BriefCompiler``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def compile_brief(self, ctx: TurnContext, request: CompileRequest) -> BriefCompilation:
        wire = build_input(self._catalog, request, mode=ctx.mode)
        if not parse_ledger(join_text(wire.ledger)):
            return BriefCompilation()  # no facts to compile (none yet): no call is spent
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        # on a fallback the runner's output IS the deterministic floor (``wire_fallback``): same conversion
        compilation, leftovers = to_compilation(self._catalog, wire, result.output)
        if leftovers and not result.used_fallback:
            log.warning(
                "compile_brief_completed_coverage", count=len(leftovers), fact_ids=leftovers[:20]
            )
        return (
            compilation.model_copy(update={"used_fallback": True})
            if result.used_fallback
            else compilation
        )


__all__ = ["CompileAdapter", "build_input", "select_targets", "to_compilation"]
