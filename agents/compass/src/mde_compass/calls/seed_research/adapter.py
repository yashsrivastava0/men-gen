"""``SeedResearchAdapter``: the ``ResearchSeeder`` port over the call (domain <-> wire, repair, fallback)."""

from __future__ import annotations

import re

from pydantic import JsonValue, ValidationError

from mde_compass.calls.base import CallRunner
from mde_compass.calls.compile_text.brief_text import slot_facts, split_list, value_text
from mde_compass.calls.seed_research.fallback import fallback
from mde_compass.calls.seed_research.rules import normalize
from mde_compass.calls.seed_research.schema import (
    DETAIL_CHARS,
    MAX_DETAILS,
    MAX_FEATURES,
    MIN_REQUESTS,
    DetailIn,
    FeatureIn,
    GeoIn,
    GlossaryIn,
    SeedResearchInput,
    SeedResearchOutput,
)
from mde_compass.calls.seed_research.spec import SPEC
from mde_compass.catalog.fact_kinds import kind_for
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.research import ResearchRequest, SearchHints
from mde_compass.engine.ports import SeedResult, TurnContext

_SPLIT = re.compile(r"[,;\n]")
_LOCALE_COUNTRY = re.compile(r"^[A-Za-z]{2,3}[-_]([A-Za-z]{2})$")


def _strings(value: JsonValue) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [t for t in (value_text(v) for v in value) if t]
    return [p.strip() for p in _SPLIT.split(value_text(value)) if p.strip()]


def _geo(value: JsonValue) -> GeoIn:
    if isinstance(value, dict):
        return GeoIn(
            country=value_text(value.get("country")),
            city=value_text(value.get("city")),
            region=value_text(value.get("region")),
        )
    return GeoIn(country="", city=value_text(value), region="")


def _locale_country(codes: list[str]) -> str:
    for code in codes:
        match = _LOCALE_COUNTRY.match(code.strip())
        if match:
            return match.group(1).upper()
    return ""


def _language_code(locale: str) -> str:
    return re.split(r"[-_]", locale.strip(), maxsplit=1)[0].lower()


def _working(ctx: TurnContext, slot_id: str) -> JsonValue:
    for s in ctx.slots:
        if s.slot_id == slot_id and s.state != "empty":
            return s.value
    return None


# the detail kinds that decide what is worth looking up (rules that apply, figures, systems to connect, duties)
RESEARCH_KINDS = ("compliance", "integration_detail", "rule", "number", "constraint", "nfr")


def research_details(brief: Brief) -> list[DetailIn]:
    """The Brief's details research needs, values only, compliance and integrations first, at most 30."""
    rows: list[tuple[int, str, DetailIn]] = []
    for f in brief.domain_facts:
        kind = f.kind or kind_for(f.type, f.topics)
        if kind in RESEARCH_KINDS and f.statement.strip():
            text = " ".join(f.statement.split())[:DETAIL_CHARS]
            rows.append((RESEARCH_KINDS.index(kind), f.id, DetailIn(kind=kind, text=text)))
    return [d for _, _, d in sorted(rows, key=lambda r: (r[0], r[1]))[:MAX_DETAILS]]


def build_input(ctx: TurnContext, brief: Brief, catalog: Catalog) -> SeedResearchInput:
    """The compact wire input from the Brief so far and the working slots (never the chat)."""
    facts = slot_facts(ctx, brief, catalog)

    def text(slot_id: str) -> str:
        return facts[slot_id].text if slot_id in facts else ""

    geo_item = brief.context.get("geo")
    geo_value: JsonValue = geo_item.value if geo_item is not None else _working(ctx, "context.geo")
    references = [r.label for r in brief.references] or split_list(text("scope.references"))
    locales = [*brief.meta.locales, ctx.language]
    languages: list[str] = []
    for code in locales:
        lang = _language_code(code)
        if lang and lang not in languages:
            languages.append(lang)
    unknowns = [
        slot.label.get(ctx.language)
        for slot_id, slot in catalog.slots.items()
        if slot_id not in facts and ("research" in slot.consumers or "all" in slot.consumers)
    ]
    live = [f for f in brief.scope if f.scope_status == "in"][:MAX_FEATURES]
    hypothesis = brief.meta.hypothesis
    return SeedResearchInput(
        language=ctx.language,
        category=text("idea.category"),
        subcategory=text("idea.subcategory"),
        geography=_geo(geo_value),
        locale_country=_locale_country(locales),
        audience=split_list(text("audience.segments")),
        business_model=text("idea.business_model"),
        references=references,
        glossary=[GlossaryIn(term=g.term, synonyms=list(g.synonyms)) for g in brief.glossary],
        exclusions=_strings(_working(ctx, "scope.must_not")) or split_list(text("scope.must_not")),
        unknowns=unknowns[:10],
        features=[FeatureIn(name=f.name, intent=f.intent) for f in live],
        families=list(hypothesis.likely_families) if hypothesis else [],
        languages=languages,
        details=research_details(brief),
    )


def to_domain(out: SeedResearchOutput) -> SeedResult:
    """Wire output (already normalized) to domain requests with local ids ``RR-001`` ...

    The service re-allocates the ids; invalid requests (should not exist after ``normalize``) are skipped.
    """
    requests: list[ResearchRequest] = []
    for w in out.requests:
        try:
            requests.append(
                ResearchRequest(
                    id=f"RR-{len(requests) + 1:03d}",
                    topic=w.topic,
                    question=w.question,
                    decision_unlocked=w.decision_unlocked,
                    entity=w.entity,
                    geography=w.geography,
                    source_languages=w.source_languages,
                    terms=w.terms,
                    references=w.references,
                    prefer_sources=w.prefer_sources,
                    avoid=w.avoid,
                    depth=w.depth,
                    priority=w.priority,
                    shown_to_user_as=w.shown_to_user_as,
                )
            )
        except ValidationError:  # pragma: no cover - normalize() already guarantees validity
            continue
    h = out.hints
    return SeedResult(
        requests=requests,
        hints=SearchHints(
            disambiguated_names=h.disambiguated_names,
            synonyms=h.synonyms,
            local_competitors=h.local_competitors,
            languages=h.languages,
        ),
    )


class SeedResearchAdapter:
    """Implements ``mde_compass.engine.ports.ResearchSeeder``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def seed(self, ctx: TurnContext, brief: Brief) -> SeedResult:
        inp = build_input(ctx, brief, self._catalog)
        res = await self._runner.run(SPEC, inp, session_id=ctx.session_id, tenant_id=ctx.tenant_id)
        clean = normalize(inp, res.output)
        if len(clean.requests) < MIN_REQUESTS:
            clean = normalize(inp, fallback(inp))
        return to_domain(clean)
