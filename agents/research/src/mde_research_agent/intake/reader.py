"""Read an approved Compass export (format 2, plain JSON) into a :class:`ResearchInput`.

Research never imports Compass. It trusts the file only after the hashes agree (RV-13), then reads the
``research`` consumer view as the main contract and looks tiers and ids up in ``brief`` (the view is
values only). What Compass cannot say yet (a ``quality`` block, tier tags in the view, a request
``origin``) is DERIVED here and flagged, never assumed away. Trust rules (the Compass author's
corrections, binding):

1. values are read by tier: ``locked`` is the person's verified words, ``default`` an AI guess; approval
   ("Approve all") is not review;
2. ``fallback_count`` undercounts: failures are recomputed from ``spend.by_call`` (0-token calls), and
   requests that show template patterns are template text;
3. the structure is a hint only: its counts never enter the load score or a query;
4. ``later`` priority does not mean defer (it only ranks);
5. roles and features may be duplicated: names are deduplicated before terms are built;
6. open questions and assumptions are not search seeds;
7. a weak Locked geography sets ``geo_uncertain``;
8. the user's own brand name alone is never a search target.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from mde_core.errors import MdeError
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.countries import code_for_name
from mde_research_agent.core.load import compute_load
from mde_research_agent.core.text import dedupe_names, same_thing, stem_set, tokens
from mde_research_agent.domain.input import (
    App,
    Entity,
    Fact,
    Failure,
    Feature,
    Flow,
    FocusTopic,
    Geography,
    Idea,
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
    Tier,
)

ZERO_TOKEN_TIMEOUT_MS = 45_000
GEO_CONFIDENCE_FLOOR = 0.7


class ExportRefused(MdeError):
    """The export cannot be researched. ``code`` is machine-readable, ``message`` is plain words."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- small readers -------------------------------------------------------------------------------


def _tier(raw: Any) -> Tier:
    text = str(raw or "").strip().lower()
    return "locked" if text == "locked" else "open" if text == "open" else "default"


def _is_slot(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and "value" in value
        and ("tier" in value or "provenance" in value)
    )


def _slot(value: Any) -> tuple[Any, Tier, float | None]:
    """``(value, tier, confidence)`` of a slot-wrapped value; a plain value is ``default`` tier."""
    if _is_slot(value):
        conf = value.get("confidence")
        return (
            value.get("value"),
            _tier(value.get("tier")),
            float(conf) if isinstance(conf, (int, float)) else None,
        )
    return value, "default", None


def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strs(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(_s(v) for v in value if _s(v))
    return ()


def _norm(text: str) -> str:
    return " ".join(tokens(text))


# --- binding -----------------------------------------------------------------------------------------


def check_binding(data: Mapping[str, Any]) -> tuple[str, int]:
    """RV-13: the three hashes agree. Returns ``(brief_hash, version)`` or raises :class:`ExportRefused`."""
    export = data.get("export")
    brief = data.get("brief")
    if not isinstance(export, Mapping) or not isinstance(brief, Mapping):
        raise ExportRefused("not_an_export", "This is not a Compass export.")
    if export.get("format_version") != 2:
        raise ExportRefused("not_format_2", "Only Compass export format 2 can be researched.")
    baseline = export.get("approved_baseline")
    if not isinstance(baseline, Mapping) or not baseline.get("hash"):
        raise ExportRefused(
            "not_approved", "The Brief has not been approved (no approved baseline)."
        )
    approved = str(baseline["hash"])
    brief_hash = str(brief.get("_hash") or "")
    export_hash = str(export.get("brief_hash") or approved)
    if not (approved == brief_hash == export_hash):
        raise ExportRefused(
            "hash_mismatch", "The Brief changed after it was approved (the hashes do not match)."
        )
    version = baseline.get("version") or export.get("brief_version") or brief.get("version") or 0
    return approved, int(version)


# --- sections --------------------------------------------------------------------------------------


def _facts(brief: Mapping[str, Any], cat: Catalog) -> tuple[Fact, ...]:
    prefixes = tuple(p.casefold() for p in cat.template_patterns.template_fact_prefixes)
    out: list[Fact] = []
    for raw in brief.get("domain_facts") or []:
        if not isinstance(raw, Mapping) or not raw.get("id"):
            continue
        statement = _s(raw.get("statement"))
        out.append(
            Fact(
                id=str(raw["id"]),
                kind=_s(raw.get("kind")) or None,
                type=_s(raw.get("type")),
                statement=statement,
                topics=_strs(raw.get("topics")),
                entities=_strs(raw.get("entities")),
                refs=_strs(raw.get("refs")),
                tier=_tier(raw.get("tier")),
                has_quote=bool(_s(raw.get("quote"))),
                is_template=statement.casefold().startswith(prefixes),
            )
        )
    return tuple(out)


def _own_brand_terms(
    brief: Mapping[str, Any], facts: tuple[Fact, ...], cat: Catalog
) -> tuple[str, ...]:
    terms: dict[str, str] = {}
    exp = brief.get("experience") or {}
    brand, _, _ = _slot(exp.get("brand.name") if isinstance(exp, Mapping) else None)
    if _s(brand):
        terms[_norm(_s(brand))] = _s(brand)
    cues = tuple(c.casefold() for c in cat.template_patterns.own_brand_cues)
    for fact in facts:
        if fact.type == "context" and any(c in fact.statement.casefold() for c in cues):
            for ent in fact.entities:
                if ent and _norm(ent):
                    terms.setdefault(_norm(ent), ent)
    return tuple(terms.values())


def _is_own_brand(text: str, own: tuple[str, ...]) -> bool:
    n = _norm(text)
    return bool(n) and any(_norm(o) and (_norm(o) in n or same_thing(text, o)) for o in own)


def _features(brief: Mapping[str, Any]) -> tuple[Feature, ...]:
    rows = [
        r
        for r in brief.get("scope") or []
        if isinstance(r, Mapping) and r.get("id") and _s(r.get("name"))
    ]
    reps = dedupe_names([(str(r["id"]), _s(r["name"])) for r in rows])
    out = []
    for r in rows:
        fid = str(r["id"])
        out.append(
            Feature(
                id=fid,
                name=_s(r["name"]),
                intent=_s(r.get("intent")),
                priority=_s(r.get("priority")) or "nice",
                scope_status=_s(r.get("scope_status")) or "in",
                tier=_tier(r.get("tier")),
                duplicate_of=None if reps[fid] == fid else reps[fid],
            )
        )
    return tuple(out)


def _roles_and_apps(brief: Mapping[str, Any]) -> tuple[tuple[Role, ...], tuple[App, ...]]:
    app_rows = [r for r in brief.get("apps") or [] if isinstance(r, Mapping) and r.get("id")]
    role_rows = [r for r in brief.get("roles") or [] if isinstance(r, Mapping) and r.get("id")]
    app_names = {_norm(_s(a.get("name"))) for a in app_rows}
    role_names = [
        (str(r["id"]), _s(r.get("name_in_user_words")) or _s(r.get("name"))) for r in role_rows
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
                goal=_s(r.get("goal")),
                tier=_tier(r.get("tier")),
                duplicate_of=None if reps[rid] == rid else reps[rid],
                is_app_name=is_app_name,
            )
        )
    role_as_app = {_norm(r.name) for r in roles if r.is_app_name}
    apps = []
    for a in app_rows:
        aid = str(a["id"])
        name = _s(a.get("name"))
        apps.append(
            App(
                id=aid,
                name=name,
                purpose=_s(a.get("purpose")),
                tier=_tier(a.get("tier")),
                is_real=bool(name) and _norm(name) not in role_as_app,
            )
        )
    return tuple(roles), tuple(apps)


def _requests(
    brief: Mapping[str, Any], view: Mapping[str, Any], cat: Catalog, hints: SearchHints
) -> tuple[Request, ...]:
    raw_requests = [
        r for r in brief.get("research_requests") or [] if isinstance(r, Mapping) and r.get("id")
    ]
    pat = cat.template_patterns
    repeated = re.compile(pat.repeated_word_regex, re.I)
    audience = view.get("audience") if isinstance(view.get("audience"), Mapping) else {}
    segments = ", ".join(_strs((audience or {}).get("segments")))
    questions = [_s(r.get("question")).casefold() for r in raw_requests]
    audience_in_most = (
        bool(segments)
        and len(raw_requests) >= 3
        and (
            sum(1 for q in questions if segments.casefold() in q) / len(raw_requests)
            >= pat.repeated_audience_share
        )
    )
    idea = view.get("idea") if isinstance(view.get("idea"), Mapping) else {}
    category_stems = stem_set(_s((idea or {}).get("category")))
    hints_empty = not (hints.synonyms or hints.local_competitors or hints.disambiguated_names)
    out: list[Request] = []
    for r in raw_requests:
        reasons: list[str] = []
        topic, entity = _s(r.get("topic")), _s(r.get("entity"))
        terms = _strs(r.get("terms"))
        if repeated.search(f"{topic} {entity}"):
            reasons.append("repeated_word")
        if sum(1 for t in terms if len(t) >= pat.fragment_term_chars or t.endswith(",")) >= 2:
            reasons.append("fragment_terms")
        if audience_in_most and segments.casefold() in _s(r.get("question")).casefold():
            reasons.append("repeated_audience")
        template = bool(reasons)
        entity_stems = stem_set(entity)
        no_entity = not entity or (bool(category_stems) and entity_stems <= category_stems)
        empty = not topic and not _s(r.get("question"))
        thin = (not template) and (
            empty or (no_entity and hints_empty and not _strs(r.get("references")))
        )
        if empty:
            reasons.append("empty")
        elif thin:
            reasons.append("no_named_entity")
        declared = _s(r.get("origin")).lower()
        origin = (
            declared if declared in ("model", "template") else ("template" if template else "model")
        )
        out.append(
            Request(
                id=str(r["id"]),
                topic=topic,
                question=_s(r.get("question")),
                decision_unlocked=_s(r.get("decision_unlocked")),
                entity=entity,
                geography=_s(r.get("geography")),
                source_languages=_strs(r.get("source_languages")),
                terms=terms,
                references=_strs(r.get("references")),
                prefer_sources=_strs(r.get("prefer_sources")),
                avoid=_strs(r.get("avoid")),
                priority=_s(r.get("priority")) or "medium",
                shown_to_user_as=_s(r.get("shown_to_user_as")),
                origin=origin,  # type: ignore[arg-type]
                thin=thin,
                reasons=tuple(reasons),
            )
        )
    return tuple(out)


def _geography(brief: Mapping[str, Any], view: Mapping[str, Any]) -> Geography:
    raw_view = view.get("geography") if isinstance(view.get("geography"), Mapping) else {}
    context = brief.get("context") if isinstance(brief.get("context"), Mapping) else {}
    geo_slot = (context or {}).get("geo")
    geo_value, tier, conf = _slot(geo_slot)
    value = geo_value if isinstance(geo_value, Mapping) else (raw_view or {})
    city, region, country = _s(value.get("city")), _s(value.get("region")), _s(value.get("country"))
    uncertain, why = False, None
    if not (city or region or country):
        uncertain, why = True, "no geography stated"
    elif geo_slot is not None and conf is not None and conf < GEO_CONFIDENCE_FLOOR:
        uncertain, why = True, f"weak geography (confidence {conf:.2f})"
    elif tier != "locked":
        uncertain, why = True, "geography is an assumption, not the person's words"
    return Geography(
        country=country or None,
        country_code=code_for_name(country),
        cities=(city,) if city else (),
        region=region or None,
        uncertain=uncertain,
        uncertain_reason=why,
        tier=tier,
    )


def _locales(brief: Mapping[str, Any], view: Mapping[str, Any], cat: Catalog) -> Locales:
    lang = view.get("languages") if isinstance(view.get("languages"), Mapping) else {}
    codes = _strs((lang or {}).get("locales")) or _strs((brief.get("meta") or {}).get("locales"))
    search = _strs((lang or {}).get("search_languages")) or codes
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
    return Locales(codes=codes, search_languages=search, non_latin=non_latin, notes=tuple(notes))


def _failures(data: Mapping[str, Any]) -> tuple[tuple[Failure, ...], int]:
    """Failures recomputed from ``spend.by_call`` plus Compass's own fallback count."""
    spend = data.get("spend") if isinstance(data.get("spend"), Mapping) else {}
    found: list[Failure] = []
    for call, st in sorted(((spend or {}).get("by_call") or {}).items()):
        if not isinstance(st, Mapping) or not st.get("calls"):
            continue
        if not st.get("tokens_in") and not st.get("tokens_out"):
            slow = (
                float(st.get("latency_ms") or 0) / max(1, int(st["calls"])) >= ZERO_TOKEN_TIMEOUT_MS
            )
            found.append(Failure(call=str(call), reason="timeout" if slow else "zero_token"))
    meta = (data.get("brief") or {}).get("meta") or {}
    fallback = int(((meta.get("interview") or {}).get("fallback_count")) or 0)
    return tuple(found), fallback


def _structure_source(data: Mapping[str, Any], fallback_count: int) -> str:
    brief = data.get("brief") or {}
    has_structure = bool((data.get("structure") or {}).get("counts")) or bool(brief.get("modules"))
    if not has_structure:
        return "unknown"
    drafted = ((data.get("spend") or {}).get("by_call") or {}).get("cmp.draft_module")
    if not drafted or not drafted.get("calls"):
        return "fallback"
    return "mixed" if fallback_count >= 5 else "model"


# --- the reader --------------------------------------------------------------------------------------


def read_export(
    data: Mapping[str, Any], catalog: Catalog, *, program_id: str | None = None
) -> ResearchInput:
    """The export as a :class:`ResearchInput`, with its trust flags and research load."""
    brief_hash, version = check_binding(data)
    brief: Mapping[str, Any] = data["brief"]
    views = data.get("consumer_views")
    view = views.get("research") if isinstance(views, Mapping) else None
    if not isinstance(view, Mapping):
        raise ExportRefused("missing_research_view", "The export has no research view.")
    flags: list[str] = []

    idea_raw = view.get("idea") if isinstance(view.get("idea"), Mapping) else {}
    idea = Idea(
        summary=_s(idea_raw.get("summary")),
        category=_s(idea_raw.get("category")),
        subcategory=_s(idea_raw.get("subcategory")),
        business_model=_s(idea_raw.get("business_model")),
    )
    facts = _facts(brief, catalog)
    own = _own_brand_terms(brief, facts, catalog)
    features = _features(brief)
    roles, apps = _roles_and_apps(brief)

    raw_hints = brief.get("search_hints") if isinstance(brief.get("search_hints"), Mapping) else {}
    hints = SearchHints(
        disambiguated_names=tuple(
            n for n in _strs(raw_hints.get("disambiguated_names")) if not _is_own_brand(n, own)
        ),
        synonyms=_strs(raw_hints.get("synonyms")),
        local_competitors=tuple(
            n for n in _strs(raw_hints.get("local_competitors")) if not _is_own_brand(n, own)
        ),
        languages=_strs(raw_hints.get("languages")),
    )
    references = tuple(
        Reference(
            label=_s(r.get("label")),
            tier=_tier(r.get("tier")),
            own_brand=_is_own_brand(_s(r.get("label")), own),
            ambiguous=_is_own_brand(_s(r.get("label")), own),
        )
        for r in brief.get("references") or []
        if isinstance(r, Mapping) and _s(r.get("label"))
    )
    requests = _requests(brief, view, catalog, hints)
    geography = _geography(brief, view)
    locales = _locales(brief, view, catalog)

    data_section = brief.get("data") if isinstance(brief.get("data"), Mapping) else {}
    sens_value, _, _ = _slot((data_section or {}).get("sensitive"))
    sensitive = _strs(sens_value)

    failures, fallback_count = _failures(data)
    meta = brief.get("meta") if isinstance(brief.get("meta"), Mapping) else {}
    probes = (meta or {}).get("probes") if isinstance((meta or {}).get("probes"), Mapping) else {}
    failing = tuple(sorted(k for k, ok in (probes or {}).items() if ok is False))
    if "quality" not in view:
        flags.append("no_quality_block")
    if not any(
        isinstance(r, Mapping) and "origin" in r for r in brief.get("research_requests") or []
    ):
        flags.append("request_origin_missing")
    flags.append("tiers_read_from_brief")
    if any(
        f.kind in {"rule", "compliance", "integration_detail", "nfr"}
        and f.tier == "locked"
        and not f.has_quote
        for f in facts
    ):
        flags.append("locked_fact_without_quote")
    if any(
        t.casefold() in {j.casefold() for j in catalog.template_patterns.junk_tokens}
        for t in _strs(
            [
                q.get("question", q) if isinstance(q, Mapping) else q
                for q in brief.get("open_questions") or []
            ]
        )
    ):
        flags.append("junk_open_questions_ignored")
    if geography.uncertain:
        flags.append("geo_uncertain")

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
        degraded=bool((meta or {}).get("degraded")),
        failing_probes=failing,
        structure_source=_structure_source(data, fallback_count),  # type: ignore[arg-type]
        geo_uncertain=geography.uncertain,
        failures=failures,
        flags=tuple(flags),
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

    guidance = view.get("guidance") if isinstance(view.get("guidance"), Mapping) else {}
    focus = tuple(
        FocusTopic(topic=_s(f.get("topic")), about=_strs(f.get("about")))
        for f in (guidance or {}).get("focus") or []
        if isinstance(f, Mapping) and _s(f.get("topic"))
    )
    audience_raw = view.get("audience") if isinstance(view.get("audience"), Mapping) else {}
    session_id = _s((data.get("export") or {}).get("session_id")) or _s(meta.get("session_id"))
    return ResearchInput(
        program_id=program_id or _s(meta.get("program_id")) or session_id,
        session_id=session_id,
        brief_version=version,
        brief_hash=brief_hash,
        idea=idea,
        geography=geography,
        locales=locales,
        audience=_strs((audience_raw or {}).get("segments")),
        features=features,
        roles=roles,
        apps=apps,
        modules=tuple(
            Module(
                id=str(m["id"]),
                name=_s(m.get("name")),
                apps=_strs(m.get("apps")),
                roles=_strs(m.get("roles")),
            )
            for m in brief.get("modules") or []
            if isinstance(m, Mapping) and m.get("id")
        ),
        entities=tuple(
            Entity(
                id=str(e["id"]),
                key=_s(e.get("key")),
                name=_s(e.get("name")),
                module=_s(e.get("module")) or None,
            )
            for e in brief.get("entities") or []
            if isinstance(e, Mapping) and e.get("id")
        ),
        flows=tuple(
            Flow(
                id=str(f["id"]),
                name=_s(f.get("name")),
                role=_s(f.get("role")) or None,
                features=_strs(f.get("features")),
            )
            for f in brief.get("flows") or []
            if isinstance(f, Mapping) and f.get("id")
        ),
        integrations=tuple(
            Integration(
                name=_s(i.get("name")),
                preference=_s(i.get("preference")),
                tier=_tier(i.get("tier")),
            )
            for i in brief.get("integrations") or []
            if isinstance(i, Mapping) and _s(i.get("name"))
        ),
        references=references,
        glossary=tuple(
            _s(g.get("term"))
            for g in view.get("glossary") or []
            if isinstance(g, Mapping) and _s(g.get("term"))
        ),
        facts=facts,
        requests=requests,
        declined=tuple(
            _s(d.get("what"))
            for d in view.get("declined") or []
            if isinstance(d, Mapping) and _s(d.get("what"))
        ),
        hints=hints,
        focus=focus,
        sensitive_flags=sensitive,
        own_brand_terms=own,
        quality=quality,
        load=load,
    )
