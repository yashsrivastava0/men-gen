"""Consumer views: what Research, Blueprint, Design and Verify receive after Gate 1 (prompt 6.8).

VALUES ONLY. Every view is built field by field from the Brief's values. No provenance, no quotes, no
history, no confidence, no turn ids and no chat text can reach them: the helpers below never touch
those fields, and a property test (``tests/views/test_consumers.py``) checks the serialized result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from pydantic import BaseModel, JsonValue

from mde_compass.catalog.fact_kinds import default_consumers, kind_for, views_of
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import AssetRecord, Brief, DomainFact, FeatureRecord
from mde_compass.domain.common import BriefItem
from mde_compass.engine.brief_builder import target_of
from mde_compass.views.paths import is_empty_value, json_dump

JsonDict = dict[str, JsonValue]

CONSUMER_NAMES: tuple[str, ...] = ("research", "blueprint", "design", "verify")
_META_FIELDS = frozenset({"history", "provenance", "confidence"})
_LATER_OR_IN = ("in", "later", "needs-engineering")


def _get(section: Mapping[str, BriefItem], *names: str) -> JsonValue:
    """Value of the first dict item present under any of ``names`` (also ``a.b`` -> ``a_b``)."""
    for name in names:
        for key in (name, name.replace(".", "_")):
            item = section.get(key)
            if item is not None and item.has_value():
                return item.value
    return None


def _compact(data: Mapping[str, JsonValue]) -> JsonDict:
    """Drop empty values so views stay small and stable."""
    return {k: v for k, v in data.items() if not is_empty_value(v)}


def _record(model: BaseModel, *fields: str) -> JsonDict:
    """Selected fields of a record as JSON values (never the meta fields)."""
    dumped = json_dump(model)
    return {f: dumped[f] for f in fields if f in dumped and f not in _META_FIELDS}


def _features(brief: Brief, statuses: Iterable[str] | None) -> list[JsonValue]:
    wanted = None if statuses is None else set(statuses)
    rows: list[JsonValue] = []
    for f in brief.scope:
        if wanted is None or f.scope_status in wanted:
            rows.append(_feature_row(f))
    return rows


def _feature_row(f: FeatureRecord) -> JsonDict:
    return {
        "id": f.id,
        "name": f.name,
        "intent": f.intent,
        "family": f.family,
        "apps": list(f.apps),
        "status": f.scope_status,
        "priority": f.priority,
        "size": json_dump(f.size),
        "capability_tier": f.capability_tier,
    }


def _assets_for(brief: Brief, consumer: str) -> list[JsonValue]:
    def routed(a: AssetRecord) -> bool:
        return consumer in a.consumers or "all" in a.consumers

    return [_record(a, "id", "kind", "summary", "pointer") for a in brief.assets if routed(a)]


def _idea(brief: Brief) -> JsonDict:
    out: JsonDict = {"summary": brief.summary or _get(brief.idea, "summary"), "pitch": brief.pitch}
    for key in ("category", "subcategory", "business_model", "problem_value", "differentiator"):
        out[key] = _get(brief.idea, key)
    return _compact(out)


def _audience(brief: Brief) -> JsonDict:
    return _compact(
        {
            "segments": _get(brief.audience, "segments"),
            "traits": _get(brief.audience, "traits"),
            "usage_context": _get(brief.audience, "usage_context"),
        }
    )


def _languages(brief: Brief) -> JsonDict:
    return _compact(
        {
            "locales": _get(brief.experience, "locales"),
            "meta_locales": list(brief.meta.locales),
            "search_languages": list(brief.search_hints.languages),
        }
    )


def _references(brief: Brief) -> list[JsonValue]:
    return [_record(r, "label", "borrow", "avoid") for r in brief.references]


def _glossary(brief: Brief) -> list[JsonValue]:
    return [_record(g, "term", "synonyms", "avoid") for g in brief.glossary]


def _declined(brief: Brief) -> list[JsonValue]:
    return [_record(d, "what", "reason", "alternative") for d in brief.declined]


def _reaches(fact: DomainFact, view: str) -> bool:
    """Whether a fact reaches ``view``: any consumer tag it carries maps onto the view (``qa`` and
    ``change_control`` to verify, ``screens`` to design and blueprint, ``frontend`` and ``backend`` to blueprint,
    ``all`` to every view); a fact with no tag gets the default consumers of its kind."""
    tags = fact.consumers or default_consumers(fact.kind or kind_for(fact.type, fact.topics))
    return view in views_of(tags)


_NOT_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def _normalised(statement: str) -> str:
    """A statement as words only, lower case, single spaces: "Delhi only at launch." equals "delhi only at launch"."""
    return " ".join(_NOT_WORD.sub(" ", statement.casefold()).split())


def _deduped(facts: Iterable[DomainFact]) -> list[DomainFact]:
    """One fact per kind and statement. The ledger can hold the same statement several times (it is read again from
    every batch of answers; the two live exports had up to five copies, so about half of the facts in a view were
    repeats). The first copy stays, the refs of the others are merged into it, and nothing else changes: a view is
    values only and is never hashed, so this changes no stored Brief."""
    seen: dict[tuple[str, str], int] = {}
    out: list[DomainFact] = []
    for fact in facts:
        statement = _normalised(fact.statement)
        if not statement:
            out.append(fact)
            continue
        key = (fact.kind or kind_for(fact.type, fact.topics), statement)
        at = seen.get(key)
        if at is None:
            seen[key] = len(out)
            out.append(fact)
            continue
        first = out[at]
        refs = list(dict.fromkeys([*first.refs, *fact.refs]))
        if refs != first.refs:
            out[at] = first.model_copy(update={"refs": refs})
    return out


def _fact_row(fact: DomainFact) -> JsonDict:
    """A domain fact as values only: never the quote, the provenance or any chat text. ``kind`` and ``refs``
    appear only when the fact has them, so views of a Brief without them do not change."""
    row = _record(fact, "id", "type", "statement", "topics", "entities")
    if fact.kind:
        row["kind"] = fact.kind
    if fact.refs:
        row["refs"] = list(fact.refs)
    return row


def _facts(brief: Brief, consumer: str) -> JsonDict:
    """The domain facts that reach the ``consumer`` view. Absent while there are none, so views of a Brief
    without facts do not change."""
    reached = _deduped(f for f in brief.domain_facts if _reaches(f, consumer))
    rows: list[JsonValue] = [_fact_row(f) for f in reached]
    return {"domain_facts": rows} if rows else {}


def _detail_row(fact: DomainFact) -> JsonDict:
    return {"id": fact.id, "statement": fact.statement}


def _by_kind(facts: Iterable[DomainFact]) -> JsonDict:
    """Facts grouped by kind (``rule``, ``number``, ... ``other`` for an untyped one), kinds in first-seen order."""
    grouped: dict[str, list[JsonValue]] = {}
    for f in facts:
        grouped.setdefault(f.kind or kind_for(f.type, f.topics), []).append(_detail_row(f))
    return dict(grouped)


def _linked_details(brief: Brief, view: str) -> tuple[dict[str, JsonDict], JsonDict]:
    """(details by record id, unlinked details): the facts of ``view`` split by the app, role and feature ids their
    ``refs`` resolve to. A fact with no resolving ref is unlinked, never dropped."""
    live = {r.id for r in (*brief.apps, *brief.roles, *brief.scope)}
    linked: dict[str, list[DomainFact]] = {}
    loose: list[DomainFact] = []
    for fact in _deduped(f for f in brief.domain_facts if _reaches(f, view)):
        hits = [r for r in dict.fromkeys(fact.refs) if r in live]
        for ref in hits:
            linked.setdefault(ref, []).append(fact)
        if not hits:
            loose.append(fact)
    return {k: _by_kind(v) for k, v in linked.items()}, _by_kind(loose)


def _with_details(row: JsonDict, details: Mapping[str, JsonDict]) -> JsonDict:
    found = details.get(str(row.get("id")))
    return {**row, "details": found} if found else row


def _org(brief: Brief, view: str) -> JsonDict:
    """The company (``org.*``) slots that belong to ``view``, by the consumers the catalog gives each slot."""
    out: JsonDict = {}
    for slot in load_catalog().slots.values():
        if slot.group != "org" or view not in views_of(slot.consumers):
            continue
        target = target_of(slot)
        if target.kind != "dict":
            continue
        item = getattr(brief, target.section).get(target.key)
        if item is not None and item.has_value():
            out[slot.id.split(".", 1)[1]] = item.value
    return {"org": out} if out else {}


_ASSUMED_BASES = {
    "typical": ("blueprint", "verify"),
    "correction": ("verify",),
    "review": ("verify",),
    "document": ("verify",),
    "gap": ("verify",),
}


def _assumed_details(brief: Brief, view: str) -> JsonDict:
    """Drafted details and corrections that are not tied to a slot (typical for this kind of app, a correction
    we made, a review finding), unless the person rejected them. Values only."""
    rows: list[JsonValue] = [
        {"id": a.id, "claim": a.claim, "basis": a.basis, "decision": a.decision}
        for a in brief.assumptions
        if not a.slot_id and view in _ASSUMED_BASES.get(a.basis, ()) and a.decision != "rejected"
    ]
    return {"assumed_details": rows} if rows else {}


def research_view(brief: Brief) -> dict[str, JsonValue]:
    """Idea, geography, languages, audience, references, in-scope feature intents, glossary, the full
    research requests, search hints and declined items."""
    return {
        "idea": _idea(brief),
        "geography": _get(brief.context, "geo"),
        "languages": _languages(brief),
        "audience": _audience(brief),
        "references": _references(brief),
        "feature_intents": [
            {"name": f.name, "intent": f.intent, "family": f.family}
            for f in brief.scope
            if f.scope_status == "in"
        ],
        "glossary": _glossary(brief),
        "research_requests": [json_dump(r) for r in brief.research_requests],
        "search_hints": json_dump(brief.search_hints),
        "declined": _declined(brief),
        **_org(brief, "research"),
        **_facts(brief, "research"),
    }


def blueprint_view(brief: Brief) -> dict[str, JsonValue]:
    """Apps, roles, features with priority/size/tier, rules and numbers, existing systems, scale and
    launch, success measures, integrations, access and the assets routed to Blueprint. Apps, roles and features carry
    ``details`` (the facts that refer to them, grouped by kind); the facts that refer to none are ``unlinked_details``."""
    details, unlinked = _linked_details(brief, "blueprint")
    return {
        "idea": _idea(brief),
        "geography": _get(brief.context, "geo"),
        "audience": _audience(brief),
        "apps": [
            _with_details(
                _record(a, "id", "name", "purpose", "surface", "audience", "roles"), details
            )
            for a in brief.apps
        ],
        "roles": [
            _with_details(
                _record(r, "id", "name_in_user_words", "goal", "rough_permissions"), details
            )
            for r in brief.roles
        ],
        "features": [
            _with_details(f, details)  # type: ignore[arg-type]
            for f in _features(brief, _LATER_OR_IN)
        ],
        "rules_and_numbers": _compact(
            {
                "constraints": {k: v.value for k, v in brief.constraints.items() if v.has_value()},
                "goals": _get(brief.context, "goals"),
            }
        ),
        "existing_systems": _get(brief.data, "existing_systems"),
        "sensitive_data": _get(brief.data, "sensitive"),
        "scale_launch": _get(brief.context, "scale_launch"),
        "success_measures": _get(brief.context, "success_measures"),
        "integrations": [_record(i, "name", "preference", "notes") for i in brief.integrations],
        "access": {k: v.value for k, v in brief.access.items() if v.has_value()},
        "glossary": _glossary(brief),
        "assets": _assets_for(brief, "blueprint"),
        **_org(brief, "blueprint"),
        **({"unlinked_details": unlinked} if unlinked else {}),
        **_assumed_details(brief, "blueprint"),
        **_facts(brief, "blueprint"),
    }


def design_view(brief: Brief) -> dict[str, JsonValue]:
    """Brand, tone words, visual references, density, locales and right-to-left, devices, audience
    traits, accessibility and the assets routed to Design."""
    return {
        "idea": _compact(
            {
                "summary": brief.summary or _get(brief.idea, "summary"),
                "category": _get(brief.idea, "category"),
                "differentiator": _get(brief.idea, "differentiator"),
            }
        ),
        "geography": _get(brief.context, "geo"),
        "brand": _compact(
            {
                "name": _get(brief.experience, "brand.name", "brand"),
                "assets": _get(brief.experience, "brand.assets"),
            }
        ),
        "tone_words": _get(brief.experience, "tone_words"),
        "visual_references": _get(brief.experience, "visual_references"),
        "density": _get(brief.experience, "density_mode", "density"),
        "locales": _get(brief.experience, "locales"),
        "devices": _get(brief.experience, "devices"),
        "audience": _audience(brief),
        "accessibility": _get(brief.constraints, "accessibility"),
        "references": _references(brief),
        "assets": _assets_for(brief, "design"),
        **_org(brief, "design"),
        **_facts(brief, "design"),
    }


def verify_view(brief: Brief) -> dict[str, JsonValue]:
    """The full scope ledger (every feature, including later, out and unsupported) and the must-not
    list."""
    must_not: list[JsonValue] = []
    for value in (
        _get(brief.constraints, "must_not"),
        _get(brief.constraints, "scope.must_not"),
    ):
        if isinstance(value, list):
            must_not.extend(v for v in value if v not in must_not)
        elif value is not None and value not in must_not:
            must_not.append(value)
    return {
        "scope_ledger": _features(brief, None),
        "must_not": must_not,
        "declined": _declined(brief),
        **_org(brief, "verify"),
        **_assumed_details(brief, "verify"),
        **_facts(brief, "verify"),
    }


def build_handoff(brief: Brief) -> dict[str, dict[str, JsonValue]]:
    """The four consumer views keyed ``research``, ``blueprint``, ``design``, ``verify``."""
    return {
        "research": research_view(brief),
        "blueprint": blueprint_view(brief),
        "design": design_view(brief),
        "verify": verify_view(brief),
    }
