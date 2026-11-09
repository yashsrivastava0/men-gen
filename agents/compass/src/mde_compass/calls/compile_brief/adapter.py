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

v2 (ADR 0016): the same adapter also implements ``engine.compile_types.SectionCompiler``. ``compile_section`` takes the
facts of ONE group (the engine runs the groups in parallel), the Brief items that already exist and the project card;
it returns whole values (``items``), single list elements (``additions``), placements with ``kind`` and ``refs``, and
typical details. Unlike the whole-ledger port it does NOT complete coverage: a fact the model mentioned nowhere comes
back in ``unplaced`` so the engine can count it (the metric stays honest) and ask once more.
"""

from __future__ import annotations

import re

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
    AdditionWire,
    CompileInput,
    CompileOutput,
    ItemWire,
    KindWire,
    NeedWire,
    PlacementWire,
    TargetWire,
    TypicalWire,
)
from mde_compass.calls.compile_brief.spec import SPEC
from mde_compass.calls.understand_turn.values import ValueError_, parse_json, validate_value
from mde_compass.catalog.fact_kinds import load_fact_kinds, normalize_kind
from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.engine.compile_types import (
    MAX_TYPICAL,
    TYPICAL_SECTION,
    DetailPlacement,
    SectionRequest,
    SectionResult,
    TypicalDetail,
)
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


_LIST_KINDS = ("enum_list", "text_list", "object_list")


def _filled(state: str) -> str:
    """The wire word for a slot's state: empty, only a guess so far, or given by the person."""
    if not state or state == "empty":
        return ""
    return "assumed" if state == "assumed" else "user"


def _target(slot: SlotDef, state: str = "") -> TargetWire:
    kind = slot.value_schema.kind
    return TargetWire(
        path=slot.id,
        label=slot.label.en,
        value_kind=kind,
        options=list(slot.value_schema.options) if kind in _SHAPED_BY_OPTIONS else [],
        fields=list(slot.value_schema.fields) if kind in _SHAPED_BY_FIELDS else [],
        filled=_filled(state),
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


def kind_wires() -> list[KindWire]:
    return [
        KindWire(key=k.key, description=k.description) for k in load_fact_kinds().kinds.values()
    ]


def build_section_input(
    catalog: Catalog, request: SectionRequest, *, mode: str = "guided"
) -> CompileInput:
    """The wire input of one per-group call. Everything that carries the user's words is secret-redacted and cut
    into pieces; the typical task has no targets (it drafts assumptions, it fills nothing)."""
    typical = request.task == "typical"
    slots = [] if typical else select_targets(catalog, request.sections or [], mode)
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
        sections=[TYPICAL_SECTION]
        if typical
        else [s.strip() for s in request.sections if s.strip()],
        draft=[],
        targets=[_target(s, request.slot_state.get(s.id, "")) for s in slots],
        task="typical" if typical else "fill",
        project=_clean(request.project),
        existing=_clean(request.existing),
        kinds=kind_wires(),
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


_EXISTING = re.compile(r"^([A-Z]{2,4}-\d+) \| ([^|]*) \| (.*)$")


def existing_index(lines: list[str]) -> tuple[set[str], dict[str, str]]:
    """(ids, casefolded name -> id) of the ``existing`` lines (``FTR-001 | scope | Order tracking``)."""
    ids: set[str] = set()
    names: dict[str, str] = {}
    for line in join_text(lines).split("\n"):
        match = _EXISTING.match(line.strip())
        if match:
            ids.add(match.group(1))
            names.setdefault(match.group(3).strip().casefold(), match.group(1))
    return ids, names


def element_name(element: object) -> str:
    """The name of a list element: a string, or the first of the name-like keys of an object."""
    if isinstance(element, str):
        return element.strip()
    if isinstance(element, dict):
        for key in ("name", "term", "label", "name_in_user_words"):
            value = element.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _additions(
    slots: dict[str, SlotDef], known: dict[str, LedgerFact], wire: list[AdditionWire]
) -> list[CompiledItem]:
    """Valid single elements for list targets, each citing at least one fact of the ledger, one per name."""
    out: list[CompiledItem] = []
    seen: set[tuple[str, str]] = set()
    for w in wire:
        slot = slots.get(w.path)
        if slot is None or slot.value_schema.kind not in _LIST_KINDS:
            continue
        fact_ids = [f for f in dict.fromkeys(w.fact_ids) if f in known]
        if not fact_ids:
            continue  # an element with no fact behind it is an invented element
        try:
            element = parse_json(w.value_json)
            value = validate_value(slot, [element])
        except ValueError_:
            continue
        name = element_name(value[0])
        if not name or (w.path, name.casefold()) in seen:
            continue
        seen.add((w.path, name.casefold()))
        out.append(
            CompiledItem(
                path=w.path, value=value[0], fact_ids=fact_ids, confidence=_confidence(w.confidence)
            )
        )
    return out


def _detail_placements(
    known: dict[str, LedgerFact],
    wire: list[PlacementWire],
    ids: set[str],
    names: dict[str, str],
    added: set[str],
) -> list[DetailPlacement]:
    """Placements of real facts, one per fact (repeats are merged): topic, valid consumers, a valid kind (else the
    one the kinds file derives) and refs that are real ids or names (of existing items or of elements added by
    this answer)."""
    merged: dict[str, DetailPlacement] = {}
    for w in wire:
        fact = known.get(w.fact_id)
        if fact is None:
            continue
        refs = [
            r.strip()
            for r in w.refs
            if r.strip()
            and (r.strip() in ids or r.strip().casefold() in names or r.strip().casefold() in added)
        ]
        old = merged.get(w.fact_id)
        kind = normalize_kind(w.kind, fact.type, fact.topics or (fact.topic,))
        if old is None:
            merged[w.fact_id] = DetailPlacement(
                fact_id=w.fact_id,
                topic=w.topic.strip() or fact.topic,
                consumers=_consumers(list(w.consumers), fact.consumers),
                kind=kind,
                refs=list(dict.fromkeys(refs)),
            )
        else:
            merged[w.fact_id] = old.model_copy(
                update={
                    "consumers": _consumers([*old.consumers, *w.consumers], fact.consumers),
                    "refs": list(dict.fromkeys([*old.refs, *refs])),
                }
            )
    return list(merged.values())


def _typical(
    wire: list[TypicalWire], texts: list[str], request_assumptions: list[str]
) -> list[TypicalDetail]:
    """At most 12 distinct, grounded typical details: one plain sentence each, no number the person never gave."""
    allowed = _digits(" ".join(texts))
    seen = {" ".join(a.casefold().split()) for a in request_assumptions}
    out: list[TypicalDetail] = []
    for w in wire:
        claim = " ".join(w.claim.split())[:240]
        key = claim.casefold()
        if not claim or key in seen or not _digits(claim) <= allowed:
            continue
        seen.add(key)
        out.append(
            TypicalDetail(
                claim=claim,
                reason=" ".join(w.reason.split())[:240],
                kind=normalize_kind(w.kind, "context"),
                risky=bool(w.risky),
                what_changes=" ".join(w.what_changes.split())[:240],
            )
        )
        if len(out) >= MAX_TYPICAL:
            break
    return out


def _digits(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def to_section_result(catalog: Catalog, inp: CompileInput, out: CompileOutput) -> SectionResult:
    """Wire output to the v2 result. Never completes coverage: facts the model mentioned nowhere are ``unplaced``."""
    if inp.task == "typical":  # nothing is filled or placed: only the proposals count
        texts = [join_text(inp.ledger), join_text(inp.project), join_text(inp.existing)]
        return SectionResult(typical=_typical(out.typical, texts, inp.assumptions))
    facts = parse_ledger(join_text(inp.ledger))
    known = {f.fact_id: f for f in facts}
    slots = {t.path: catalog.slots[t.path] for t in inp.targets if t.path in catalog.slots}
    items = _items(slots, known, out.items)
    additions = _additions(slots, known, out.additions)
    ids, names = existing_index(inp.existing)
    added = {element_name(a.value).casefold() for a in additions} | {
        element_name(v).casefold()
        for i in items
        for v in (i.value if isinstance(i.value, list) else [i.value])
    }
    placements = _detail_placements(known, out.placements, ids, names, added)
    cited = {fid for item in (*items, *additions) for fid in item.fact_ids}
    placed = {p.fact_id for p in placements}
    not_used: dict[str, NotUsedFact] = {}
    for n in out.not_used:
        reason = " ".join(n.reason.split())
        if n.fact_id in known and reason and n.fact_id not in cited | placed:
            not_used.setdefault(n.fact_id, NotUsedFact(fact_id=n.fact_id, reason=reason))
    covered = cited | placed | set(not_used)
    return SectionResult(
        items=items,
        additions=additions,
        placements=placements,
        not_used=list(not_used.values()),
        summary=" ".join(out.summary.split()),
        pitch=out.pitch.strip(),
        unplaced=[f.fact_id for f in facts if f.fact_id not in covered],
    )


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

    async def compile_section(self, ctx: TurnContext, request: SectionRequest) -> SectionResult:
        """One v2 call over one group of facts. A failed or malformed answer is the flagged floor (every fact placed
        for everyone, typed by the kinds file): one section failing never affects the others."""
        wire = build_section_input(self._catalog, request, mode=ctx.mode)
        has_facts = bool(parse_ledger(join_text(wire.ledger)))
        if request.task == "fill" and not has_facts:
            return SectionResult()  # no facts: no call is spent
        if request.task == "typical" and not (has_facts or wire.project or wire.existing):
            return SectionResult()
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        section = to_section_result(self._catalog, wire, result.output)
        if result.used_fallback:
            # the runner's output IS the deterministic floor (``wire_fallback``): placed, typed, nothing invented
            return section.model_copy(update={"used_fallback": True, "unplaced": []})
        if section.unplaced:
            log.warning(
                "compile_brief_unplaced",
                group=request.group,
                count=len(section.unplaced),
                fact_ids=section.unplaced[:20],
            )
        return section


__all__ = [
    "CompileAdapter",
    "build_input",
    "build_section_input",
    "select_targets",
    "to_compilation",
    "to_section_result",
]
