"""Applying compile results to the slots and the details (ADR 0016). Pure with respect to I/O and clocks.

What a compile may and may not do to the Brief, decided here in code whatever the model said:

* it never overwrites a value the person gave (``volunteered``, ``confirmed`` or ``disputed`` slots are left alone);
* a single-value target is filled when empty, or replaced when it only holds a guess AND the cited facts are verified;
* a LIST target is extended: every new element cites at least one active fact, shares a real word with the fact it
  cites (an invented element is dropped) and is new by normalized name; a guess list (``assumed``) is replaced by
  the person's own elements, a user list only grows;
* a Locked tier still needs every cited fact to pass ``Fact.supports_locked()``: elements resting on anything else are
  not written to a slot the person already filled, and in an empty slot they stay Default (``assumed``);
* a fact counts as used only when a write that cites it was actually applied: facts whose element was refused stay
  details (they are placed, never lost).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import JsonValue

from mde_compass.catalog.fact_kinds import FALLBACK_KIND, kind_for, overlap_stopwords, words_of
from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.domain.brief import Brief, DomainFact
from mde_compass.domain.common import ItemMeta, Provenance, ProvenanceType, max_tier
from mde_compass.domain.interview import Fact
from mde_compass.domain.slots import SlotRecord
from mde_compass.engine import fact_projector
from mde_compass.engine.brief_builder import META_FIELDS, target_of
from mde_compass.engine.compile_types import (
    ALL,
    DetailPlacement,
    SectionResult,
    TypicalDetail,
)
from mde_compass.engine.facts import fact_number
from mde_compass.engine.ports import CompiledItem, NotUsedFact
from mde_compass.engine.slots import USER_GIVEN, SlotStore

LIST_KINDS = frozenset({"enum_list", "text_list", "object_list"})
NAME_KEYS = ("name", "term", "label", "name_in_user_words")
MAX_REFS = 4
MAX_DERIVED_REFS = 2
MIN_REF_NAME_CHARS = 4
MIN_TOKEN_CHARS = 4

FactLookup = Callable[[str], Fact | None]

PROVENANCE_BY_ORIGIN: dict[str, ProvenanceType] = {
    "stated": "user_text",
    "tap": "user_tap",
    "document": "document",
    "assumption": "assumed",
}


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


def domain_fact(
    fact: Fact,
    topic: str,
    consumers: Sequence[str],
    *,
    kind: str = "",
    refs: Sequence[str] = (),
) -> DomainFact:
    """The ``domain_facts`` row of a fact (kind and refs are omitted from the dump while empty)."""
    verified = fact.supports_locked()
    return DomainFact(
        id=fact.id,
        type=fact.type,
        statement=fact.statement,
        topics=[topic] if topic else list(fact.topics),
        entities=list(fact.entities),
        consumers=list(consumers) or list(fact.consumers) or ["all"],
        source=fact.qa_card_id or fact.source_ref,
        quote=fact.quote,
        tier="Locked" if verified else "Default",
        provenance=[provenance_of(fact)],
        confidence=fact.confidence,
        status="assumed"
        if fact.origin == "assumption"
        else ("confirmed" if fact.origin == "tap" else "volunteered"),
        kind=kind,
        refs=list(refs),
    )


# ----------------------------------------------------------------------------------------- merge
@dataclass
class Merged:
    """The results of every call of one compile, combined deterministically (group order, then batch order)."""

    items: dict[str, list[CompiledItem]] = field(default_factory=dict)
    additions: dict[str, list[CompiledItem]] = field(default_factory=dict)
    placements: dict[str, DetailPlacement] = field(default_factory=dict)
    not_used: dict[str, NotUsedFact] = field(default_factory=dict)
    typical: list[TypicalDetail] = field(default_factory=list)
    summary: str = ""
    pitch: str = ""
    failed: int = 0  # calls that ended in the floor


def merge_placement(old: DetailPlacement, new: DetailPlacement) -> DetailPlacement:
    """One fact placed by two groups: first topic wins, consumers and refs are united, the first real kind wins."""
    consumers = list(dict.fromkeys([*old.consumers, *new.consumers]))
    if ALL in consumers:
        consumers = [ALL]
    kind = old.kind if old.kind and old.kind != FALLBACK_KIND else (new.kind or old.kind)
    return DetailPlacement(
        fact_id=old.fact_id,
        topic=old.topic or new.topic,
        consumers=consumers,
        kind=kind,
        refs=list(dict.fromkeys([*old.refs, *new.refs]))[:MAX_REFS],
    )


def merge_results(results: Sequence[SectionResult]) -> Merged:
    merged = Merged()
    for result in results:
        merged.failed += 1 if result.used_fallback else 0
        for item in result.items:
            merged.items.setdefault(item.path, []).append(item)
        for item in result.additions:
            merged.additions.setdefault(item.path, []).append(item)
        for p in result.placements:
            old = merged.placements.get(p.fact_id)
            merged.placements[p.fact_id] = p if old is None else merge_placement(old, p)
        for n in result.not_used:
            merged.not_used.setdefault(n.fact_id, n)
        merged.typical.extend(result.typical)
        merged.summary = merged.summary or result.summary
        merged.pitch = merged.pitch or result.pitch
    # a fact placed by one call and called not used by another is placed (nothing with content is dropped)
    for fid in list(merged.not_used):
        if fid in merged.placements:
            del merged.not_used[fid]
    return merged


# ----------------------------------------------------------------------------- names and grounding
def element_name(element: object) -> str:
    if isinstance(element, str):
        return " ".join(element.split())
    if isinstance(element, dict):
        for key in NAME_KEYS:
            value = element.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
    return ""


def norm_name(text: str) -> str:
    """A name as compared for duplicates: lower case, no punctuation, plural folded (``Order tracking`` equals
    ``order-tracking`` and ``orders tracking``)."""
    return " ".join(_fold(w) for w in words_of(text))


def _fold(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss") and word.isascii():
        return word[:-1]
    return word


def _significant(text: str) -> set[str]:
    stop = overlap_stopwords()
    out: set[str] = set()
    for w in words_of(text):
        if w in stop:
            continue
        if w.isascii() and not w.isdigit() and len(w) < MIN_TOKEN_CHARS:
            continue
        if not w.isascii() and len(w) < 2:
            continue
        out.add(_fold(w))
    return out


def fact_text(fact: Fact) -> str:
    return " ".join([fact.statement, fact.quote, *fact.topics, *fact.entities])


def grounded(element: object, facts: Iterable[Fact]) -> bool:
    """The element's name shares a real word with the text of a fact it cites: an invented element has no overlap."""
    name = element_name(element)
    if not name:
        return False
    haystack = " ".join(fact_text(f) for f in facts)
    wanted = _significant(name)
    if wanted:
        return bool(wanted & _significant(haystack))
    return norm_name(name) in norm_name(
        haystack
    )  # a very short name (``QR``) must appear as written


# ------------------------------------------------------------------------------------ slot writes
@dataclass
class ApplyReport:
    applied: set[str] = field(
        default_factory=set
    )  # fact ids whose write was applied (they are used)
    written: list[str] = field(default_factory=list)  # slot ids written
    extended: list[str] = field(default_factory=list)  # list slots that gained elements
    refused: list[str] = field(default_factory=list)  # ``path: why`` for what code refused


def _newest(item: CompiledItem) -> int:
    return max((fact_number(f) for f in item.fact_ids), default=0)


def _active(lookup: FactLookup, ids: Iterable[str]) -> list[Fact]:
    found = [lookup(i) for i in dict.fromkeys(ids)]
    return [f for f in found if f is not None and f.status == "active"]


def _as_list(value: JsonValue) -> list[JsonValue]:
    return list(value) if isinstance(value, list) else [value]


def _confidence(items: Sequence[CompiledItem]) -> float:
    return round(sum(i.confidence for i in items) / len(items), 4) if items else 0.5


def _write(
    slots: SlotStore,
    path: str,
    value: JsonValue,
    cited: Fact,
    *,
    verified: bool,
    confidence: float,
    now: datetime,
    turn_id: str | None,
) -> bool:
    if verified:
        prov = fact_projector.provenance_for(cited, now=now)
        state = "volunteered"
    else:
        prov = Provenance(type="model_knowledge", ref=cited.id, quote="", date=now)
        state = "assumed"
    res = slots.write(
        path, value, state=state, provenance=prov, confidence=confidence, turn_id=turn_id
    )
    return res.applied


def _apply_single(
    slot: SlotDef,
    items: list[CompiledItem],
    slots: SlotStore,
    lookup: FactLookup,
    report: ApplyReport,
    *,
    now: datetime,
    turn_id: str | None,
) -> None:
    best = max(items, key=_newest)  # the newest explicit statement wins
    cited = _active(lookup, best.fact_ids)
    if not cited:
        return
    record = slots.get(slot.id)
    filled = slots.filled(slot.id)
    if filled and record is not None and record.status in USER_GIVEN:
        report.refused.append(f"{slot.id}: the person already gave it")
        return
    verified = all(f.supports_locked() for f in cited)
    if filled and not verified:
        report.refused.append(f"{slot.id}: a guess is replaced only by verified evidence")
        return
    if _write(
        slots,
        slot.id,
        best.value,
        cited[0],
        verified=verified,
        confidence=best.confidence,
        now=now,
        turn_id=turn_id,
    ):
        report.written.append(slot.id)
        report.applied.update(f.id for f in cited)


@dataclass
class _Candidate:
    element: JsonValue
    facts: list[Fact]
    confidence: float
    verified: bool
    source: CompiledItem


def _candidates(
    slot: SlotDef,
    items: list[CompiledItem],
    additions: list[CompiledItem],
    *,
    filled: bool,
    lookup: FactLookup,
) -> list[_Candidate]:
    """Elements the compile proposes for a list slot, each with the active facts that cite it. Elements of a whole
    value for an EMPTY slot are taken as they are (the model read the ledger for them); every other element must
    share a word with a fact it cites."""
    check = (
        slot.value_schema.kind != "enum_list"
    )  # option codes are checked by the value schema instead
    out: list[_Candidate] = []
    for item in items:
        cited = _active(lookup, item.fact_ids)
        for element in _as_list(item.value):
            if cited and (not filled or not check or grounded(element, cited)):
                out.append(
                    _Candidate(
                        element,
                        cited,
                        item.confidence,
                        all(f.supports_locked() for f in cited),
                        item,
                    )
                )
    for item in additions:
        cited = _active(lookup, item.fact_ids)
        element = item.value
        if cited and (not check or grounded(element, cited)):
            out.append(
                _Candidate(
                    element, cited, item.confidence, all(f.supports_locked() for f in cited), item
                )
            )
    return out


def _apply_list(
    slot: SlotDef,
    items: list[CompiledItem],
    additions: list[CompiledItem],
    slots: SlotStore,
    lookup: FactLookup,
    report: ApplyReport,
    *,
    now: datetime,
    turn_id: str | None,
) -> None:
    record = slots.get(slot.id)
    filled = slots.filled(slot.id)
    user_given = filled and record is not None and record.status in USER_GIVEN
    base: list[JsonValue] = (
        list(record.value)
        if user_given and record is not None and isinstance(record.value, list)
        else []
    )
    seen = {_key(slot, e) for e in base}
    fresh: list[_Candidate] = []
    for cand in _candidates(slot, items, additions, filled=filled, lookup=lookup):
        key = _key(slot, cand.element)
        if not key or key in seen:
            continue  # already there under the same normalized name
        seen.add(key)
        fresh.append(cand)
    verified = [c for c in fresh if c.verified]
    per_record = (
        target_of(slot).kind == "list"
    )  # records of a list section each carry their own evidence
    if verified:
        if per_record and record is not None and user_given:
            base = [_tagged(e, _meta_of_slot(record)) for e in base]
        added = [
            _tagged(c.element, _meta_of_candidate(c, verified=True, now=now))
            if per_record
            else c.element
            for c in verified
        ]
        value: JsonValue = [*base, *added]
        first = verified[0].facts[0]
        if _write(
            slots,
            slot.id,
            value,
            first,
            verified=True,
            confidence=_confidence([c.source for c in verified]),
            now=now,
            turn_id=turn_id,
        ):
            report.written.append(slot.id)
            if base:
                report.extended.append(slot.id)
            for c in verified:
                report.applied.update(f.id for f in c.facts)
    elif fresh and not filled:
        value = [
            c.element for c in fresh
        ]  # nothing verified, nothing there yet: a guess, Default tier
        if _write(
            slots,
            slot.id,
            value,
            fresh[0].facts[0],
            verified=False,
            confidence=_confidence([c.source for c in fresh]),
            now=now,
            turn_id=turn_id,
        ):
            report.written.append(slot.id)
            for c in fresh:
                report.applied.update(f.id for f in c.facts)
    elif fresh:
        report.refused.append(f"{slot.id}: new elements rest on unverified facts")


def _tagged(element: JsonValue, meta: ItemMeta) -> JsonValue:
    """An object element carrying its OWN evidence (``_meta``, as the Brief round trip writes it), so a record added
    from a new fact is not given the evidence of the older records of the same list, and the other way round."""
    if not isinstance(element, dict) or "_meta" in element:
        return element
    return {**element, "_meta": meta.model_dump(mode="json", include=set(META_FIELDS))}


def _meta_of_slot(record: SlotRecord) -> ItemMeta:
    return ItemMeta(
        tier=record.tier,
        provenance=list(record.provenance),
        confidence=record.confidence,
        status=record.status,
    )


def _meta_of_candidate(cand: _Candidate, *, verified: bool, now: datetime) -> ItemMeta:
    first = cand.facts[0]
    if verified:
        prov = fact_projector.provenance_for(first, now=now)
        return ItemMeta(
            tier=max_tier(has_value=True, provenance=[prov]),
            provenance=[prov],
            confidence=cand.confidence,
            status="volunteered",
        )
    prov = Provenance(type="model_knowledge", ref=first.id, quote="", date=now)
    return ItemMeta(tier="Default", provenance=[prov], confidence=cand.confidence, status="assumed")


def _key(slot: SlotDef, element: object) -> str:
    if slot.value_schema.kind == "enum_list":
        return str(element).strip().casefold()
    return norm_name(element_name(element))


def apply_values(
    merged: Merged,
    slots: SlotStore,
    catalog: Catalog,
    lookup: FactLookup,
    *,
    now: datetime,
    turn_id: str | None,
) -> ApplyReport:
    """Write the compiled values and elements into the slots under the rules in the module docstring."""
    report = ApplyReport()
    paths = [p for p in catalog.slot_ids_by_order() if p in merged.items or p in merged.additions]
    for path in paths:
        slot = catalog.slots[path]
        items = merged.items.get(path, [])
        additions = merged.additions.get(path, [])
        if slot.value_schema.kind in LIST_KINDS:
            _apply_list(slot, items, additions, slots, lookup, report, now=now, turn_id=turn_id)
        elif items:
            _apply_single(slot, items, slots, lookup, report, now=now, turn_id=turn_id)
    return report


# ---------------------------------------------------------------------------------- the details
def resolve_refs(refs: Iterable[str], brief: Brief) -> list[str]:
    """Refs as ids of live apps, roles and features: an id stays, a name (as the model wrote it) becomes the id of
    the record that carries it. Anything that resolves to nothing is dropped."""
    by_id = {r.id for r in (*brief.apps, *brief.roles, *brief.scope)}
    by_name: dict[str, str] = {}
    for rec_id, name in _record_names(brief):
        by_name.setdefault(norm_name(name), rec_id)
    out: list[str] = []
    for ref in refs:
        text = ref.strip()
        hit = text if text in by_id else by_name.get(norm_name(text), "")
        if hit and hit not in out:
            out.append(hit)
    return out[:MAX_REFS]


def _record_names(brief: Brief) -> list[tuple[str, str]]:
    names = [(a.id, a.name) for a in brief.apps]
    names += [(r.id, r.name_in_user_words) for r in brief.roles]
    names += [(f.id, f.name) for f in brief.scope]
    return [(i, n) for i, n in names if n.strip()]


def derive_refs(fact: Fact, brief: Brief) -> list[str]:
    """Refs found by code when the model gave none: a feature, role or app whose full name appears in the fact's
    own words. Conservative on purpose (long names only, at most two): a wrong link is worse than none."""
    haystack = f" {norm_name(fact_text(fact))} "
    hits = [
        (rec_id, name)
        for rec_id, name in _record_names(brief)
        if len(name.strip()) >= MIN_REF_NAME_CHARS and f" {norm_name(name)} " in haystack
    ]
    hits.sort(key=lambda h: (-len(h[1]), h[0]))
    return list(dict.fromkeys(i for i, _ in hits))[:MAX_DERIVED_REFS]


def detail_row(
    fact: Fact, placement: DetailPlacement | None, row: DomainFact | None, brief: Brief
) -> DomainFact:
    """The ``domain_facts`` row of a fact: the model's placement when there is one, else the row it already has, else
    the deterministic one (kind from the kinds file, everyone as consumer). Kind and refs are never empty by
    accident: a missing kind is derived, a missing ref is looked for in the fact's words."""
    kind = (
        (placement.kind if placement else "")
        or (row.kind if row else "")
        or kind_for(fact.type, fact.topics)
    )
    topic = (placement.topic if placement else "") or (row.topics[0] if row and row.topics else "")
    consumers = list(placement.consumers) if placement else (list(row.consumers) if row else [])
    raw_refs = list(placement.refs) if placement else (list(row.refs) if row else [])
    refs = resolve_refs(raw_refs, brief) or derive_refs(fact, brief)
    return domain_fact(fact, topic, consumers, kind=kind, refs=refs)
