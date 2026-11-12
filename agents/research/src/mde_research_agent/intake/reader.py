"""Read a Compass export into a :class:`ResearchInput`: a Brief in, the person's facts and trust flags out.

Research never imports Compass. ``formats.py`` is the only module that knows what an export file looks like
(versions, the optional research view, the hash seal); this module is format-free: it turns the Brief inside into a
``ResearchInput``. A Brief whose seal could not be verified is read anyway and carries ``input_binding`` (D-156).
What Compass cannot say yet (a ``quality`` block, tier tags in the view, a request ``origin``) is DERIVED here and
flagged, never assumed away. Trust rules (the Compass author's corrections, binding):

1. values are read by tier: ``locked`` is the person's verified words, ``default`` an AI guess; approval
   ("Approve all") is not review;
2. ``fallback_count`` undercounts: failures are recomputed from ``spend.by_call`` (0-token calls), and
   requests that show template patterns are template text;
3. the structure is a hint only: its counts never enter the load score or a query;
4. ``later`` priority does not mean defer (it only ranks);
5. roles and features may be duplicated: names are deduplicated before terms are built;
6. open questions and assumptions are not search seeds;
7. a weak Locked geography sets ``geo_uncertain``;
8. the user's own brand name alone is never a search target, and the brand, phones, e-mails and addresses never
   leave in any query (``core/privacy.py``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.countries import code_for_name, parse_countries
from mde_research_agent.core.load import compute_load
from mde_research_agent.core.privacy import build_private_terms
from mde_research_agent.core.text import dedupe_names, same_thing, stem_set, tokens
from mde_research_agent.domain.input import (
    App,
    Entity,
    Fact,
    Feature,
    Flow,
    Geography,
    InputQuality,
    Integration,
    Locales,
    Module,
    PlanSource,
    Reference,
    Request,
    RequestCounts,
    ResearchInput,
    Role,
    SearchHints,
)
from mde_research_agent.intake.formats import ExportRefused, Normalised, normalise
from mde_research_agent.intake.tolerant import m, rows, s, slot, strs, tier_of

__all__ = ["ExportRefused", "read_export"]

GEO_CONFIDENCE_FLOOR = 0.7


def _norm(text: str) -> str:
    return " ".join(tokens(text))


# --- sections --------------------------------------------------------------------------------------


def _facts(brief: Mapping[str, Any], cat: Catalog) -> tuple[Fact, ...]:
    prefixes = tuple(p.casefold() for p in cat.template_patterns.template_fact_prefixes)
    out: list[Fact] = []
    for raw in rows(brief.get("domain_facts"), "id"):
        statement = s(raw.get("statement"))
        out.append(
            Fact(
                id=str(raw["id"]),
                kind=s(raw.get("kind")) or None,
                type=s(raw.get("type")),
                statement=statement,
                topics=strs(raw.get("topics")),
                entities=strs(raw.get("entities")),
                refs=strs(raw.get("refs")),
                tier=tier_of(raw.get("tier")),
                has_quote=bool(s(raw.get("quote"))),
                is_template=statement.casefold().startswith(prefixes),
            )
        )
    return tuple(out)


def _place_norms(geography: Geography) -> frozenset[str]:
    """The places the Brief states, folded: a city or country is where the app is used, never the person's brand."""
    names = [geography.country, geography.region, *geography.cities]
    names += [c.name for c in geography.countries]
    return frozenset(n for n in (_norm(x or "") for x in names) if n)


def _own_brand_terms(
    brief: Mapping[str, Any],
    facts: tuple[Fact, ...],
    cat: Catalog,
    places: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    terms: dict[str, str] = {}
    brand, _, _ = slot(m(brief.get("experience")).get("brand.name"))
    if s(brand):
        terms[_norm(s(brand))] = s(brand)
    cues = tuple(c.casefold() for c in cat.template_patterns.own_brand_cues)
    for fact in facts:
        if fact.type == "context" and any(c in fact.statement.casefold() for c in cues):
            for ent in fact.entities:
                if ent and _norm(ent) and _norm(ent) not in places:
                    terms.setdefault(_norm(ent), ent)
    return tuple(terms.values())


def _is_own_brand(text: str, own: tuple[str, ...]) -> bool:
    n = _norm(text)
    return bool(n) and any(_norm(o) and (_norm(o) in n or same_thing(text, o)) for o in own)


def _features(brief: Mapping[str, Any]) -> tuple[Feature, ...]:
    recs = [r for r in rows(brief.get("scope"), "id") if s(r.get("name"))]
    reps = dedupe_names([(str(r["id"]), s(r["name"])) for r in recs])
    out = []
    for r in recs:
        fid = str(r["id"])
        out.append(
            Feature(
                id=fid,
                name=s(r["name"]),
                intent=s(r.get("intent")),
                priority=s(r.get("priority")) or "nice",
                scope_status=s(r.get("scope_status")) or "in",
                tier=tier_of(r.get("tier")),
                duplicate_of=None if reps[fid] == fid else reps[fid],
            )
        )
    return tuple(out)


def _roles_and_apps(brief: Mapping[str, Any]) -> tuple[tuple[Role, ...], tuple[App, ...]]:
    app_rows = rows(brief.get("apps"), "id")
    role_rows = rows(brief.get("roles"), "id")
    app_names = {_norm(s(a.get("name"))) for a in app_rows}
    role_names = [
        (str(r["id"]), s(r.get("name_in_user_words")) or s(r.get("name"))) for r in role_rows
    ]
    reps = dedupe_names(role_names)
    roles: list[Role] = []
    for r, (rid, name) in zip(role_rows, role_names, strict=True):
        # a role that is really an app name: the same words as an app, or "... screens" (not "Buyer" vs "Buyer app")
        is_app_name = bool(name) and (_norm(name) in app_names or "screens" in tokens(name))
        roles.append(
            Role(
                id=rid,
                name=name,
                goal=s(r.get("goal")),
                tier=tier_of(r.get("tier")),
                duplicate_of=None if reps[rid] == rid else reps[rid],
                is_app_name=is_app_name,
            )
        )
    role_as_app = {_norm(r.name) for r in roles if r.is_app_name}
    apps = []
    for a in app_rows:
        aid = str(a["id"])
        name = s(a.get("name"))
        apps.append(
            App(
                id=aid,
                name=name,
                purpose=s(a.get("purpose")),
                tier=tier_of(a.get("tier")),
                is_real=bool(name) and _norm(name) not in role_as_app,
            )
        )
    return tuple(roles), tuple(apps)


def _requests(
    brief: Mapping[str, Any], norm: Normalised, cat: Catalog, hints: SearchHints
) -> tuple[Request, ...]:
    raw_requests = rows(brief.get("research_requests"), "id")
    pat = cat.template_patterns
    repeated = re.compile(pat.repeated_word_regex, re.I)
    segments = ", ".join(norm.audience)
    questions = [s(r.get("question")).casefold() for r in raw_requests]
    audience_in_most = (
        bool(segments)
        and len(raw_requests) >= 3
        and (
            sum(1 for q in questions if segments.casefold() in q) / len(raw_requests)
            >= pat.repeated_audience_share
        )
    )
    category_stems = stem_set(norm.idea.category)
    hints_empty = not (hints.synonyms or hints.local_competitors or hints.disambiguated_names)
    out: list[Request] = []
    for r in raw_requests:
        reasons: list[str] = []
        topic, entity = s(r.get("topic")), s(r.get("entity"))
        terms = strs(r.get("terms"))
        if repeated.search(f"{topic} {entity}"):
            reasons.append("repeated_word")
        if sum(1 for t in terms if len(t) >= pat.fragment_term_chars or t.endswith(",")) >= 2:
            reasons.append("fragment_terms")
        if audience_in_most and segments.casefold() in s(r.get("question")).casefold():
            reasons.append("repeated_audience")
        template = bool(reasons)
        entity_stems = stem_set(entity)
        no_entity = not entity or (bool(category_stems) and entity_stems <= category_stems)
        empty = not topic and not s(r.get("question"))
        thin = (not template) and (
            empty or (no_entity and hints_empty and not strs(r.get("references")))
        )
        if empty:
            reasons.append("empty")
        elif thin:
            reasons.append("no_named_entity")
        declared = s(r.get("origin")).lower()
        origin = (
            declared if declared in ("model", "template") else ("template" if template else "model")
        )
        out.append(
            Request(
                id=str(r["id"]),
                topic=topic,
                question=s(r.get("question")),
                decision_unlocked=s(r.get("decision_unlocked")),
                entity=entity,
                geography=s(r.get("geography")),
                source_languages=strs(r.get("source_languages")),
                terms=terms,
                references=strs(r.get("references")),
                prefer_sources=strs(r.get("prefer_sources")),
                avoid=strs(r.get("avoid")),
                priority=s(r.get("priority")) or "medium",
                shown_to_user_as=s(r.get("shown_to_user_as")),
                origin=origin,  # type: ignore[arg-type]
                thin=thin,
                reasons=tuple(reasons),
            )
        )
    return tuple(out)


def _geography(norm: Normalised) -> Geography:
    geo = norm.geo
    uncertain, why = False, None
    if not (geo.city or geo.region or geo.country):
        uncertain, why = True, "no geography stated"
    elif geo.slot_present and geo.confidence is not None and geo.confidence < GEO_CONFIDENCE_FLOOR:
        uncertain, why = True, f"weak geography (confidence {geo.confidence:.2f})"
    elif geo.tier != "locked":
        uncertain, why = True, "geography is an assumption, not the person's words"
    countries = parse_countries(geo.country)
    if (
        len(countries) > 1
    ):  # "Mexico, Guatemala and Costa Rica": the first named is the primary country
        country, code = countries[0].name, countries[0].code
    else:
        country, code = geo.country or None, code_for_name(geo.country)
    return Geography(
        country=country,
        country_code=code,
        countries=countries,
        cities=(geo.city,) if geo.city else (),
        region=geo.region or None,
        uncertain=uncertain,
        uncertain_reason=why,
        tier=geo.tier,
    )


def _locales(norm: Normalised, cat: Catalog) -> Locales:
    codes = norm.locale_codes
    info = cat.languages
    non_latin = tuple(
        c
        for c in codes
        if (info.get(c.split("-")[0]) and not info[c.split("-")[0]].latin and c != "hi-Latn")
    )
    notes: list[str] = []
    if any(c.split("-")[0] == "hi" for c in codes):
        notes.append(
            "hi may stand for Hinglish: searched in English with transliterated terms, never guessed"
        )
    return Locales(
        codes=codes, search_languages=norm.search_languages, non_latin=non_latin, notes=tuple(notes)
    )


# --- the reader --------------------------------------------------------------------------------------


def read_export(
    data: Mapping[str, Any], catalog: Catalog, *, program_id: str | None = None
) -> ResearchInput:
    """The export as a :class:`ResearchInput`, with its trust flags and research load.

    Raises :class:`ExportRefused` only when there is no usable Brief. An unverified seal is a flag
    (``quality.input_binding``), not a refusal."""
    norm = normalise(data, catalog.languages)
    brief = norm.brief
    flags = list(norm.flags)

    geography = _geography(norm)
    facts = _facts(brief, catalog)
    own = _own_brand_terms(brief, facts, catalog, _place_norms(geography))
    features = _features(brief)
    roles, apps = _roles_and_apps(brief)

    raw_hints = m(brief.get("search_hints"))
    hints = SearchHints(
        disambiguated_names=tuple(
            n for n in strs(raw_hints.get("disambiguated_names")) if not _is_own_brand(n, own)
        ),
        synonyms=strs(raw_hints.get("synonyms")),
        local_competitors=tuple(
            n for n in strs(raw_hints.get("local_competitors")) if not _is_own_brand(n, own)
        ),
        languages=strs(raw_hints.get("languages")),
    )
    references = tuple(
        Reference(
            label=s(r.get("label")),
            tier=tier_of(r.get("tier")),
            own_brand=_is_own_brand(s(r.get("label")), own),
            ambiguous=_is_own_brand(s(r.get("label")), own),
        )
        for r in rows(brief.get("references"), "label")
        if s(r.get("label"))
    )
    requests = _requests(brief, norm, catalog, hints)
    locales = _locales(norm, catalog)

    sens_value, _, _ = slot(m(brief.get("data")).get("sensitive"))
    sensitive = strs(sens_value)

    if any(
        f.kind in {"rule", "compliance", "integration_detail", "nfr"}
        and f.tier == "locked"
        and not f.has_quote
        for f in facts
    ):
        flags.append("locked_fact_without_quote")
    junk = {j.casefold() for j in catalog.template_patterns.junk_tokens}
    open_questions = brief.get("open_questions")
    if any(
        t.casefold() in junk
        for t in strs(
            [
                q.get("question", q) if isinstance(q, Mapping) else q
                for q in (open_questions if isinstance(open_questions, list) else [])
            ]
        )
    ):
        flags.append("junk_open_questions_ignored")
    if geography.uncertain:
        flags.append("geo_uncertain")
    if len(geography.countries) > 1:
        flags.append("multi_country")
    if norm.binding != "verified":
        flags.append(norm.binding)

    counts = RequestCounts(
        trusted=sum(1 for r in requests if r.origin != "template" and not r.thin),
        template=sum(1 for r in requests if r.origin == "template"),
        thin=sum(1 for r in requests if r.thin),
    )
    plan_source: PlanSource = (
        "rebuilt"
        if requests and (counts.template + counts.thin) >= 0.6 * len(requests)
        else "mixed"
        if counts.template + counts.thin
        else "from_request"
    )
    if not requests:
        plan_source = "rebuilt"
    quality = InputQuality(
        requests=counts,
        plan_source=plan_source,
        degraded=norm.degraded,
        failing_probes=norm.failing_probes,
        structure_source=norm.structure_source,  # type: ignore[arg-type]
        geo_uncertain=geography.uncertain,
        failures=norm.failures,
        flags=tuple(flags),
        input_binding=norm.binding,
        compass_format=norm.compass_format,
    )

    kinds = set(catalog.load.fact_kinds)
    seen: set[tuple[str, str]] = set()
    fact_count = 0
    for f in facts:
        key = (f.kind or "", _norm(f.statement))
        if f.kind in kinds and not f.is_template and key not in seen:
            seen.add(key)
            fact_count += 1
    load = compute_load(
        features=sum(1 for f in features if f.duplicate_of is None and f.scope_status != "out"),
        facts_of_kind=fact_count,
        apps=sum(1 for a in apps if a.is_real),
        sensitive_flags=len(sensitive),
        named_references=sum(1 for r in references if not r.own_brand),
        config=catalog.load,
    )
    private = build_private_terms(
        brief,
        own_brand=own,
        facts=facts,
        generic=[*catalog.intent_words.generic_words, *_place_norms(geography)],
    )
    return ResearchInput(
        program_id=program_id or norm.program_id or norm.session_id,
        session_id=norm.session_id,
        brief_version=norm.brief_version,
        brief_hash=norm.brief_hash,
        idea=norm.idea,
        geography=geography,
        locales=locales,
        audience=norm.audience,
        features=features,
        roles=roles,
        apps=apps,
        modules=tuple(
            Module(
                id=str(r["id"]),
                name=s(r.get("name")),
                apps=strs(r.get("apps")),
                roles=strs(r.get("roles")),
            )
            for r in rows(brief.get("modules"), "id")
        ),
        entities=tuple(
            Entity(
                id=str(r["id"]),
                key=s(r.get("key")),
                name=s(r.get("name")),
                module=s(r.get("module")) or None,
            )
            for r in rows(brief.get("entities"), "id")
        ),
        flows=tuple(
            Flow(
                id=str(r["id"]),
                name=s(r.get("name")),
                role=s(r.get("role")) or None,
                features=strs(r.get("features")),
            )
            for r in rows(brief.get("flows"), "id")
        ),
        integrations=tuple(
            Integration(
                name=s(r.get("name")),
                preference=s(r.get("preference")),
                tier=tier_of(r.get("tier")),
            )
            for r in rows(brief.get("integrations"), "name")
        ),
        references=references,
        glossary=norm.glossary,
        facts=facts,
        requests=requests,
        declined=norm.declined,
        hints=hints,
        focus=norm.focus,
        sensitive_flags=sensitive,
        own_brand_terms=own,
        private=private,
        quality=quality,
        load=load,
    )
