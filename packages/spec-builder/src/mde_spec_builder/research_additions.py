"""Research additions: what the Research Note adds to the spec, with toggles (spec 4.3).

Research never edits the spec silently. Every suggestion of the Note is listed under
``assumptions.research_additions`` with its sources; ``included`` is False for the ones the user
toggled off at Gate 2 (``excluded_research_ids``). ``affects`` lists the spec ids a suggestion
touched (filled after the proposals are applied, see ``with_affects``).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from mde_contracts.calls import ResearchAdditionLite
from mde_contracts.research import ResearchNote, ResearchSuggestion, SourceRef
from mde_contracts.spec import AppSpec, ResearchAddition
from mde_spec_builder.assemble import spec_ids

MAX_SOURCES = 3
FEATURE_PREFIX = "scope.features:"


def usable(note: ResearchNote | None) -> bool:
    """A Note whose content may be used (an unavailable Note has no content)."""
    return note is not None and note.status != "unavailable"


def _sources(note: ResearchNote, suggestion: ResearchSuggestion) -> list[SourceRef]:
    facts = {f.id: f for f in note.facts}
    out: list[SourceRef] = []
    seen: set[str] = set()
    for fact_id in suggestion.fact_ids:
        fact = facts.get(fact_id)
        for source in fact.sources if fact is not None else []:
            if source.url not in seen:
                seen.add(source.url)
                out.append(source)
    return out[:MAX_SOURCES]


def build_research_additions(
    note: ResearchNote | None,
    excluded: Collection[str],
    affects: dict[str, list[str]] | None = None,
) -> list[ResearchAddition]:
    """One addition per suggestion of a usable Note (excluded ones stay listed, switched off).
    ``affects`` lists what code already attached to a suggestion (a pack feature's data)."""
    if note is None or not usable(note):
        return []
    return [
        ResearchAddition(
            id=s.id,
            title=s.title,
            kind=s.kind,
            sources=_sources(note, s),
            included=s.id not in excluded,
            affects=sorted((affects or {}).get(s.id, [])),
            verified=s.verified,
        )
        for s in note.suggestions
    ]


def included_suggestions(
    note: ResearchNote | None, excluded: Collection[str]
) -> list[ResearchSuggestion]:
    if note is None or not usable(note):
        return []
    return [s for s in note.suggestions if s.id not in excluded]


def feature_target(suggestion: ResearchSuggestion, pack_features: Collection[str]) -> str | None:
    """The Domain Pack feature a suggestion switches on, from ``applies_to``.

    The research engine writes ``scope.features:<pack feature id>`` (a bare ``scope.features``, a
    section name, a slot id or nothing mean "no particular feature"). An id that is not a feature
    of the selected pack is ignored: the suggestion then stays a proposal for the section writers.
    """
    target = suggestion.applies_to
    if target is None or not target.startswith(FEATURE_PREFIX):
        return None
    feature = target[len(FEATURE_PREFIX) :].strip()
    return feature if feature in pack_features else None


def research_feature_map(
    note: ResearchNote | None,
    excluded: Collection[str],
    pack_features: Collection[str],
    already_on: Collection[str],
) -> dict[str, str]:
    """Feature id -> the suggestion that adds it, for included suggestions that name a pack
    feature the person had not already selected (the first suggestion wins per feature)."""
    found: dict[str, str] = {}
    for suggestion in included_suggestions(note, excluded):
        feature = feature_target(suggestion, pack_features)
        if feature is not None and feature not in already_on and feature not in found:
            found[feature] = suggestion.id
    return found


def brand_applies(note: ResearchNote | None, excluded: Collection[str]) -> bool:
    """Is the brand finding of the Note applied? Yes unless every brand suggestion is excluded."""
    if note is None or not usable(note) or note.brand is None:
        return False
    brand_suggestions = [s for s in note.suggestions if s.kind == "brand"]
    return not brand_suggestions or any(s.id not in excluded for s in brand_suggestions)


def lite(suggestions: Iterable[ResearchSuggestion]) -> list[ResearchAdditionLite]:
    """The trusted summaries a call receives (already validated by the research engine)."""
    return [
        ResearchAdditionLite(
            id=s.id, kind=s.kind, title=s.title, rationale=s.rationale, applies_to=s.applies_to
        )
        for s in suggestions
    ]


def with_affects(spec: AppSpec, touched: dict[str, list[str]]) -> AppSpec:
    """``spec`` with each addition's ``affects`` = what it already listed, the items tagged with
    its id and the ids ``touched`` by proposals that cited it; only ids that still exist, in a
    stable order."""
    tagged: dict[str, list[str]] = {}
    for screen in spec.screens:
        if screen.research_ref:
            tagged.setdefault(screen.research_ref, []).append(screen.id)
    for item in spec.integrations.items:
        if item.research_ref:
            tagged.setdefault(item.research_ref, []).append(item.id)
    exists = set(spec_ids(spec))
    additions: list[ResearchAddition] = []
    for addition in spec.assumptions.research_additions:
        ids = [*addition.affects, *tagged.get(addition.id, []), *touched.get(addition.id, [])]
        kept = sorted(dict.fromkeys(i for i in ids if i in exists))
        additions.append(addition.model_copy(update={"affects": kept}))
    block = spec.assumptions.model_copy(update={"research_additions": additions})
    return spec.model_copy(update={"assumptions": block})
