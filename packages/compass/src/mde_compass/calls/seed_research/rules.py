"""Code rules of ``cmp.seed_research``: repair or drop invalid requests, carry the exclusions, never invent.

``normalize`` is the single place that decides what a request must look like. The runner validator and the
adapter both use it, so a request without geography, entity and terms never reaches the Brief, and a
competitor the user did not name never reaches ``hints.local_competitors`` or a request's ``entity``.
"""

from __future__ import annotations

from mde_compass.calls.compile_text.text_utils import Vocabulary
from mde_compass.calls.seed_research.schema import (
    MAX_REQUESTS,
    MIN_REQUESTS,
    SeedResearchInput,
    SeedResearchOutput,
    WireHints,
    WireRequest,
)

GLOBAL_WORD = "global"


def _clean(items: list[str]) -> list[str]:
    """Stripped, non-empty, unique (case-insensitive) items in the original order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        text = " ".join(raw.split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def geography_text(inp: SeedResearchInput) -> str:
    """"city, region, country" from the Brief, else the locale country, else the word ``global``."""
    g = inp.geography
    parts = _clean([g.city, g.region, g.country])
    if parts:
        return ", ".join(parts)
    return " ".join(inp.locale_country.split()) or GLOBAL_WORD


def vocabulary(inp: SeedResearchInput) -> Vocabulary:
    """Every word the user (or the Brief) used: the only source of names a request may mention."""
    texts: list[str] = [
        inp.category,
        inp.subcategory,
        inp.business_model,
        inp.geography.country,
        inp.geography.city,
        inp.geography.region,
        inp.locale_country,
        *inp.audience,
        *inp.references,
        *inp.exclusions,
        *inp.unknowns,
    ]
    for g in inp.glossary:
        texts += [g.term, *g.synonyms]
    for f in inp.features:
        texts += [f.name, f.intent]
    return Vocabulary(texts)


def named_references(inp: SeedResearchInput) -> dict[str, str]:
    """User-named apps by case-folded name."""
    return {r.casefold(): r for r in _clean(inp.references)}


def _fallback_entity(inp: SeedResearchInput) -> str:
    return " ".join((inp.subcategory or inp.category).split())


def _default_terms(inp: SeedResearchInput) -> list[str]:
    terms = [inp.category, inp.subcategory]
    for g in inp.glossary:
        terms += [g.term, *g.synonyms[:2]]
    return _clean(terms)[:6]


def repair_request(
    inp: SeedResearchInput, req: WireRequest, vocab: Vocabulary
) -> WireRequest | None:
    """The request made valid, or ``None`` when it cannot be repaired from the Brief."""
    topic = " ".join(req.topic.split())
    question = " ".join(req.question.split())
    if not topic or not question:
        return None
    named = named_references(inp)
    geography = " ".join(req.geography.split())
    priority = req.priority
    if not geography:
        geography = geography_text(inp)
        if geography == GLOBAL_WORD:
            priority = "low"
    entity = " ".join(req.entity.split())
    if not entity or vocab.unknown_proper_nouns(entity):
        entity = _fallback_entity(inp)  # empty or an invented name: back to the user's own category
    if not entity:
        return None
    terms = _clean(req.terms) or _default_terms(inp)
    if not terms:
        return None
    avoid = _clean([*inp.exclusions, *req.avoid])  # the user's exclusions always come first
    references = [named[r.casefold()] for r in _clean(req.references) if r.casefold() in named]
    return WireRequest(
        topic=topic,
        question=question,
        decision_unlocked=" ".join(req.decision_unlocked.split()) or topic,
        entity=entity,
        geography=geography,
        source_languages=_clean(req.source_languages) or _clean(inp.languages) or [inp.language],
        terms=terms,
        references=references,
        prefer_sources=_clean(req.prefer_sources),
        avoid=avoid,
        depth=req.depth,
        priority=priority,
        shown_to_user_as=" ".join(req.shown_to_user_as.split()) or topic,
    )


def repair_hints(inp: SeedResearchInput, hints: WireHints) -> WireHints:
    """Hints with ``local_competitors`` limited to the apps the user named."""
    named = named_references(inp)
    competitors = [
        named[c.casefold()] for c in _clean(hints.local_competitors) if c.casefold() in named
    ]
    return WireHints(
        disambiguated_names=_clean(hints.disambiguated_names),
        synonyms=_clean(hints.synonyms),
        local_competitors=competitors,
        languages=_clean(hints.languages) or _clean(inp.languages) or [inp.language],
    )


def _dup_keys(req: WireRequest) -> tuple[str, str]:
    return (
        f"{req.topic.casefold()}|{req.entity.casefold()}|{req.geography.casefold()}",
        req.question.casefold(),
    )


def normalize(inp: SeedResearchInput, out: SeedResearchOutput) -> SeedResearchOutput:
    """Repair or drop every invalid request, drop duplicates, cap the count, fix the hints."""
    vocab = vocabulary(inp)
    seen: set[str] = set()
    requests: list[WireRequest] = []
    for raw in out.requests:
        req = repair_request(inp, raw, vocab)
        if req is None:
            continue
        keys = _dup_keys(req)
        if keys[0] in seen or keys[1] in seen:
            continue
        seen.update(keys)
        requests.append(req)
        if len(requests) >= MAX_REQUESTS:
            break
    return SeedResearchOutput(requests=requests, hints=repair_hints(inp, out.hints))


def validate(inp: SeedResearchInput, out: SeedResearchOutput) -> list[str]:
    """``CallRunner`` validator: fewer than three usable requests means the fallback runs."""
    usable = normalize(inp, out).requests
    if len(usable) < MIN_REQUESTS:
        return [f"only {len(usable)} usable research request(s), need {MIN_REQUESTS}"]
    return []
