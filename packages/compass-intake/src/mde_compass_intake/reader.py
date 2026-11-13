"""Read a Compass export into a :class:`SpecInput`: a Brief in, the person's facts, graded by trust, out.

``formats.py`` is the only module that knows what an export file looks like (versions, views, the hash seal, the
structure block); this module is format-free: it turns the Brief inside into a ``SpecInput``. Trust rules (doc 15
section 3.4, the Compass author's corrections):

1. Compass's tiers are weak ("Approve all" is not review), so each value is graded here: a feature exists as ``locked``
   only when a quote of the person's own words supports it AND that quote is found in the record of what was said;
   facts, geography, locales and style values additionally need a confidence of at least 0.7;
2. priority is a default unless the person ranked it (a user-evidenced ``later``); duplicates and junk are cleaned
   with the merge recorded, never dropped silently;
3. the structure is a proposal (``default``), never evidence;
4. a Brief that lacks something Spec needs is read anyway: roles are derived from the audience and marked ``assumed``,
   the locale defaults to ``en``; every such step is in ``assumptions`` and makes ``thin_input`` true. Only a missing
   idea or a missing feature is fatal, and that is decided in ``readiness.py``, not here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mde_compass_intake.cleaning import Cleaning, load_cleaning
from mde_compass_intake.countries import (
    code_for_name,
    parse_countries,
)
from mde_compass_intake.countries import (
    languages as known_languages,
)
from mde_compass_intake.formats import ExportRefused, Normalised, fold, normalise
from mde_compass_intake.models import Failure, Tier
from mde_compass_intake.spec_input import (
    App,
    Assumption,
    CompassAssumption,
    Entity,
    EntityField,
    ExistingSystem,
    Fact,
    Feature,
    Flow,
    FlowStep,
    Geography,
    Integration,
    Locales,
    Merge,
    Module,
    NameClash,
    Role,
    Screen,
    ScreenUse,
    Setting,
    SpecInput,
    StructureIssue,
    StructureProposal,
    Trust,
    TrustProfile,
)
from mde_compass_intake.text import dedupe_names, stem_set, tokens
from mde_compass_intake.tolerant import is_slot, m, rows, s, slot, slot_value, strs, tier_of

__all__ = ["ExportRefused", "read_export"]

GEO_CONFIDENCE_FLOOR = 0.7
# the Brief sections Spec relies on: a missing one, or one that is present but empty, is a drift flag
EXPECTED_SECTIONS = (
    "idea",
    "audience",
    "context",
    "scope",
    "roles",
    "apps",
    "experience",
    "access",
    "domain_facts",
)
SCOPE_ALIASES = ("features",)  # a renamed scope section is still read, and flagged


def _norm(text: str) -> str:
    return " ".join(tokens(text))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _empty(value: Any) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    if is_slot(value):
        return _empty(value.get("value"))
    if isinstance(value, Mapping):
        return all(_empty(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_empty(v) for v in value)
    return False


# --- trust ----------------------------------------------------------------------------------------------


class _Grader:
    """Grades one value: Compass tier, confidence and a verified quote of the person's own words."""

    def __init__(self, norm: Normalised, cleaning: Cleaning) -> None:
        self.cleaning = cleaning
        self.interview = norm.interview_text

    def verified(self, quote: str) -> bool:
        q = fold(quote)
        if not q:
            return False
        # without an interview record the quote cannot be checked against what was said: it counts as stated
        return q in self.interview if self.interview else True

    def trust(
        self,
        raw: Mapping[str, Any],
        *,
        min_confidence: float = 0.0,
        extra_quote: tuple[str, str] | None = None,
    ) -> Trust:
        tier = tier_of(raw.get("tier"))
        conf = _number(raw.get("confidence"))
        provenance = raw.get("provenance")
        candidates: list[tuple[str, str]] = []
        for p in provenance if isinstance(provenance, list) else []:
            if isinstance(p, Mapping) and s(p.get("type")) in self.cleaning.user_provenance_types:
                candidates.append((s(p.get("ref")), s(p.get("quote"))))
        if extra_quote:
            candidates.append(extra_quote)
        ids = tuple(dict.fromkeys(ref for ref, q in candidates if ref and self.verified(q)))
        verified = any(self.verified(q) for _, q in candidates)
        enough = conf is None or conf >= min_confidence
        grade: Tier = "locked" if tier == "locked" and verified and enough else "default"
        return Trust(
            grade=grade,
            confidence=conf,
            assumed=s(raw.get("status")) == "assumed" or tier == "default",
            verified_quote=verified,
            quote_ids=ids,
        )

    def slot_trust(self, raw: Any, *, min_confidence: float | None = None) -> Trust:
        floor = self.cleaning.min_confidence if min_confidence is None else min_confidence
        return self.trust(raw, min_confidence=floor) if is_slot(raw) else Trust(assumed=True)


def _setting(raw: Any, grader: _Grader) -> Setting:
    value, _, _ = slot(raw)
    return Setting(values=strs(value), trust=grader.slot_trust(raw))


# --- sections ---------------------------------------------------------------------------------------------


def _facts(brief: Mapping[str, Any], grader: _Grader) -> tuple[Fact, ...]:
    out: list[Fact] = []
    for raw in rows(brief.get("domain_facts"), "id"):
        fid = str(raw["id"])
        quote = s(raw.get("quote"))
        out.append(
            Fact(
                id=fid,
                kind=s(raw.get("kind")) or None,
                type=s(raw.get("type")),
                statement=s(raw.get("statement")),
                topics=strs(raw.get("topics")),
                entities=strs(raw.get("entities")),
                refs=strs(raw.get("refs")),
                tier=tier_of(raw.get("tier")),
                has_quote=bool(quote),
                trust=grader.trust(
                    raw,
                    min_confidence=grader.cleaning.min_confidence,
                    extra_quote=(fid, quote) if quote else None,
                ),
            )
        )
    return tuple(out)


def _scope_rows(brief: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    flags: list[str] = []
    found = rows(brief.get("scope"), "id")
    if "scope" not in brief:
        for alias in SCOPE_ALIASES:
            if alias in brief:
                flags.append(f"key_alias:{alias}->scope")
                found = rows(brief.get(alias), "id")
                break
    return [r for r in found if s(r.get("name"))], flags


def _features(
    recs: list[Mapping[str, Any]], grader: _Grader
) -> tuple[tuple[Feature, ...], list[Merge]]:
    reps = dedupe_names([(str(r["id"]), s(r["name"])) for r in recs])
    cleaning = grader.cleaning
    built: dict[str, dict[str, Any]] = {}
    for r in recs:
        fid = str(r["id"])
        trust = grader.trust(r)
        priority = s(r.get("priority")).lower() or "nice"
        status = s(r.get("scope_status")).lower() or "in"
        evidenced = trust.verified_quote
        deferred = evidenced and "later" in (priority, status)
        built[fid] = {
            "id": fid,
            "name": s(r["name"]),
            "intent": s(r.get("intent")),
            "scope_status": status,
            "priority": priority,
            "tier": tier_of(r.get("tier")),
            "apps": strs(r.get("apps")),
            "trust": trust,
            "user_evidenced": evidenced,
            "quote_ids": trust.quote_ids,
            "priority_trust": Trust(
                grade="locked" if deferred else "default",
                confidence=trust.confidence,
                assumed=not deferred,
                verified_quote=deferred,
                quote_ids=trust.quote_ids if deferred else (),
            ),
            "deferred_by_user": deferred,
            "duplicate_of": None if reps[fid] == fid else reps[fid],
            "alias_ids": (),
        }
    merges: list[Merge] = []
    rank = {p: i for i, p in enumerate(reversed(cleaning.priorities))}  # must > nice > later
    for fid, row in built.items():
        rep = row["duplicate_of"]
        if rep is None:
            continue
        merges.append(
            Merge(
                kind="feature", from_id=fid, into_id=rep, reason="same feature under another name"
            )
        )
        head = built[rep]
        head["alias_ids"] = (*head["alias_ids"], fid)
        # the representative keeps the strongest evidence of the group, so a user-evidenced feature is never lost
        head["user_evidenced"] = head["user_evidenced"] or row["user_evidenced"]
        head["quote_ids"] = tuple(dict.fromkeys((*head["quote_ids"], *row["quote_ids"])))
        if rank.get(row["priority"], -1) > rank.get(head["priority"], -1) and row["user_evidenced"]:
            head["priority"] = row["priority"]
    return tuple(Feature(**row) for row in built.values()), merges


def _roles_and_apps(
    brief: Mapping[str, Any], grader: _Grader
) -> tuple[tuple[Role, ...], tuple[App, ...], tuple[NameClash, ...], list[Merge]]:
    cleaning = grader.cleaning
    app_rows = rows(brief.get("apps"), "id")
    role_rows = rows(brief.get("roles"), "id")
    app_names = {_norm(s(a.get("name"))): str(a["id"]) for a in app_rows if s(a.get("name"))}
    role_names = [
        (str(r["id"]), s(r.get("name_in_user_words")) or s(r.get("name"))) for r in role_rows
    ]
    role_reps = dedupe_names(role_names)
    junk_roles = {_norm(n) for n in cleaning.junk_role_names}
    app_words = set(cleaning.app_name_words)
    merges: list[Merge] = []
    roles: list[Role] = []
    clashes: list[NameClash] = []
    for r, (rid, name) in zip(role_rows, role_names, strict=True):
        is_app_name = bool(name) and (
            _norm(name) in app_names or bool(app_words & set(tokens(name)))
        )
        junk = not _norm(name) or _norm(name) in junk_roles
        dup = None if role_reps[rid] == rid else role_reps[rid]
        base = grader.trust(r)
        clean = dup is None and not is_app_name and not junk
        trust = base if clean else base.model_copy(update={"grade": "default"})
        if dup:
            merges.append(
                Merge(
                    kind="role",
                    from_id=rid,
                    into_id=dup,
                    reason="same role (singular or plural, or another wording)",
                )
            )
        if is_app_name:
            clashes.append(NameClash(name=name, role_id=rid, app_id=app_names.get(_norm(name))))
        roles.append(
            Role(
                id=rid,
                name=name,
                goal=s(r.get("goal")),
                tier=tier_of(r.get("tier")),
                trust=trust,
                duplicate_of=dup,
                is_app_name=is_app_name,
                junk=junk,
            )
        )
    aliases: dict[str, list[str]] = {}
    for role in roles:
        if role.duplicate_of:
            aliases.setdefault(role.duplicate_of, []).append(role.id)
    role_as_app = {_norm(r.name) for r in roles if r.is_app_name}
    app_ids = [(str(a["id"]), s(a.get("name"))) for a in app_rows]
    app_reps = dedupe_names([(i, n) for i, n in app_ids if n])
    junk_apps = {_norm(n) for n in cleaning.junk_app_names}
    apps: list[App] = []
    for a in app_rows:
        aid = str(a["id"])
        name = s(a.get("name"))
        junk = not _norm(name) or _norm(name) in junk_apps
        dup = None if app_reps.get(aid, aid) == aid else app_reps[aid]
        if dup:
            merges.append(
                Merge(kind="app", from_id=aid, into_id=dup, reason="same app under another name")
            )
        base = grader.trust(a)
        is_real = bool(name) and _norm(name) not in role_as_app
        good = dup is None and not junk and is_real
        apps.append(
            App(
                id=aid,
                name=name,
                purpose=s(a.get("purpose")),
                surface=s(a.get("surface")),
                roles=strs(a.get("roles")),
                tier=tier_of(a.get("tier")),
                trust=base if good else base.model_copy(update={"grade": "default"}),
                duplicate_of=dup,
                is_real=is_real,
                junk=junk,
            )
        )
    by_role = {r.id: tuple(a.id for a in apps if r.id in a.roles) for r in roles}
    roles = [
        r.model_copy(
            update={"apps": by_role.get(r.id, ()), "alias_ids": tuple(aliases.get(r.id, ()))}
        )
        for r in roles
    ]
    return tuple(roles), tuple(apps), tuple(clashes), merges


def _derive_roles(
    audience: tuple[str, ...], features: tuple[Feature, ...], cleaning: Cleaning
) -> tuple[list[Role], Assumption]:
    """Roles for a Brief that has none usable: one per audience segment, else one generic role; an administrator when a
    feature talks about managing or approving. Always ``assumed``, ids in their own namespace."""
    reps = dedupe_names([(f"a{i}", seg) for i, seg in enumerate(audience)])
    names = [seg for i, seg in enumerate(audience) if reps[f"a{i}"] == f"a{i}"]
    source = "the audience"
    if not names:
        names, source = [cleaning.default_role_name], "nothing stated (one generic user)"
    cues = {c for t in cleaning.admin_cues for c in stem_set(t)}
    if any(cues & stem_set(f"{f.name} {f.intent}") for f in features if f.duplicate_of is None):
        names.append(cleaning.admin_role_name)
        source += " and the features"
    derived = [
        Role(
            id=f"{cleaning.derived_role_prefix}{i:03d}",
            name=name,
            tier="default",
            trust=Trust(grade="default", assumed=True),
            assumed=True,
        )
        for i, name in enumerate(names, start=1)
    ]
    return derived, Assumption(
        field="roles",
        value=", ".join(names),
        reason=f"the Brief has no usable role; derived from {source}",
    )


def _geography(norm: Normalised, grader: _Grader, brief: Mapping[str, Any]) -> Geography:
    geo = norm.geo
    uncertain, why = False, None
    if not (geo.city or geo.region or geo.country):
        uncertain, why = True, "no geography stated"
    elif geo.slot_present and geo.confidence is not None and geo.confidence < GEO_CONFIDENCE_FLOOR:
        uncertain, why = True, f"weak geography (confidence {geo.confidence:.2f})"
    elif geo.tier != "locked":
        uncertain, why = True, "geography is an assumption, not the person's words"
    countries = parse_countries(geo.country)
    if len(countries) > 1:
        country, code = countries[0].name, countries[0].code
    else:
        country, code = geo.country, code_for_name(geo.country)
    raw = m(brief.get("context")).get("geo")
    trust = grader.slot_trust(raw)
    if not (geo.city or geo.region or geo.country):
        trust = Trust(assumed=True)
    return Geography(
        city=geo.city,
        region=geo.region,
        country=country,
        country_code=code,
        countries=countries,
        uncertain=uncertain,
        uncertain_reason=why,
        trust=trust,
    )


def _locales(
    norm: Normalised, grader: _Grader, brief: Mapping[str, Any]
) -> tuple[Locales, Assumption | None]:
    info = known_languages()
    codes, source, assumption = norm.locale_codes, norm.locale_source, None
    trust = grader.slot_trust(m(brief.get("experience")).get("locales"))
    if source not in ("view", "experience"):  # a meta field or a hint has no quote of its own
        trust = trust.model_copy(update={"grade": "default"})
    search = norm.search_languages
    if not codes:
        codes, search, source = ("en",), ("en",), "default"
        trust = Trust(assumed=True)
        assumption = Assumption(
            field="locales", value="en", reason="the Brief names no language; English is assumed"
        )

    def primary(code: str) -> str:
        return code.split("-")[0]

    rtl = tuple(c for c in codes if info.get(primary(c)) and info[primary(c)].direction == "rtl")
    non_latin = tuple(
        c for c in codes if info.get(primary(c)) and not info[primary(c)].latin and c != "hi-Latn"
    )
    return (
        Locales(
            codes=codes,
            search_languages=search,
            source=source,
            parsed_from_text=norm.locale_source != "none"
            and "locales_parsed_from_text" in norm.flags,
            non_latin=non_latin,
            rtl=rtl,
            trust=trust,
        ),
        assumption,
    )


def _structure(norm: Normalised, brief: Mapping[str, Any]) -> StructureProposal:
    block = norm.structure_block
    screens = tuple(
        Screen(
            id=str(r["id"]),
            name=s(r.get("name")),
            purpose=s(r.get("purpose")),
            module=s(r.get("module")),
            apps=strs(r.get("apps")),
            roles=strs(r.get("roles")),
            serves=strs(r.get("serves")),
            entities=tuple(
                ScreenUse(entity=s(u.get("entity")), use=s(u.get("use")))
                for u in rows(r.get("entities"), "entity")
            ),
            pattern=s(r.get("pattern")),
            phase=s(r.get("phase")),
        )
        for r in rows(brief.get("screens"), "id")
    )
    flows = tuple(
        Flow(
            id=str(r["id"]),
            name=s(r.get("name")),
            goal=s(r.get("goal")),
            role=s(r.get("role")),
            steps=tuple(
                FlowStep(screen=s(st.get("screen")), action=s(st.get("action")))
                for st in rows(r.get("steps"))
            ),
            features=strs(r.get("features")),
            alternates=strs(r.get("alternates")),
            acceptance=strs(r.get("acceptance")),
        )
        for r in rows(brief.get("flows"), "id")
    )
    entities = tuple(
        Entity(
            id=str(r["id"]),
            key=s(r.get("key")),
            name=s(r.get("name")),
            purpose=s(r.get("purpose")),
            module=s(r.get("module")),
            fields=tuple(
                EntityField(
                    key=s(f.get("key")),
                    name=s(f.get("name")),
                    type=s(f.get("type")),
                    required=bool(f.get("required")),
                    ref=s(f.get("ref")),
                    values=strs(f.get("values")),
                )
                for f in rows(r.get("fields"), "key")
            ),
            states=strs(r.get("states")),
            access={str(k): s(v) for k, v in m(r.get("access")).items() if s(v)},
        )
        for r in rows(brief.get("entities"), "id")
    )
    matrix = {
        str(role): {str(mod): strs(ids) for mod, ids in m(by_module).items()}
        for role, by_module in m(block.get("role_matrix")).items()
    }
    nav_raw = block.get("navigation")
    navigation = tuple(
        (s(e[0]), s(e[1]))
        for e in (nav_raw if isinstance(nav_raw, list) else [])
        if isinstance(e, (list, tuple)) and len(e) == 2 and s(e[0]) and s(e[1])
    )
    issues = tuple(
        StructureIssue(
            code=s(r.get("code")),
            severity=s(r.get("severity")),
            ids=strs(r.get("ids")),
            detail=s(r.get("detail")),
        )
        for r in rows(block.get("issues"), "code")
    )
    return StructureProposal(
        present=bool(screens or flows or entities or matrix),
        source=norm.structure_source,
        band=norm.structure_band,
        screens=screens,
        flows=flows,
        entities=entities,
        role_matrix=matrix,
        navigation=navigation,
        issues=issues,
    )


def _drift(
    brief: Mapping[str, Any], norm: Normalised, grader: _Grader, scope_rows: list[Any]
) -> list[str]:
    cleaning = grader.cleaning
    flags = list(norm.drift_flags)
    for key in EXPECTED_SECTIONS:
        names = (key, *SCOPE_ALIASES) if key == "scope" else (key,)
        found = next((n for n in names if n in brief), None)
        if found is None:
            flags.append(f"field_missing:brief.{key}")
        elif _empty(brief.get(found)):
            flags.append(f"field_empty:brief.{key}")
    if not norm.idea.summary:
        flags.append("field_empty:brief.idea.summary")
    enums = (
        ("priority", cleaning.priorities, scope_rows),
        ("scope_status", cleaning.scope_statuses, scope_rows),
    )
    for field_name, allowed, recs in enums:
        seen = {s(r.get(field_name)).lower() for r in recs} - {""} - set(allowed)
        flags += [f"enum_changed:scope.{field_name}={v}" for v in sorted(seen)]
    tiers = {
        str(r.get("tier")).strip().lower()
        for key in ("scope", "roles", "apps")
        for r in rows(brief.get(key))
        if r.get("tier") is not None
    } - set(cleaning.tiers)
    flags += [f"enum_changed:tier={v}" for v in sorted(tiers)]
    if not norm.interview_text:
        flags.append("quotes_unchecked")
    return flags


# --- the reader ---------------------------------------------------------------------------------------------


def read_export(data: Mapping[str, Any], *, program_id: str | None = None) -> SpecInput:
    """The export as a :class:`SpecInput`, graded and cleaned.

    Raises :class:`ExportRefused` only when there is no usable Brief. Everything else, including a Brief with no idea
    or no feature, is read; ``readiness.check_readiness`` decides whether Spec can go on."""
    norm = normalise(data)
    brief = norm.brief
    cleaning = load_cleaning()
    grader = _Grader(norm, cleaning)
    flags = list(norm.flags)
    assumptions: list[Assumption] = []

    scope_rows, alias_flags = _scope_rows(brief)
    features, merges = _features(scope_rows, grader)
    roles, apps, clashes, role_merges = _roles_and_apps(brief, grader)
    merges += role_merges
    facts = _facts(brief, grader)
    geography = _geography(norm, grader, brief)
    locales, locale_assumption = _locales(norm, grader, brief)
    if locale_assumption:
        assumptions.append(locale_assumption)
    if not (geography.city or geography.region or geography.country):
        assumptions.append(
            Assumption(field="geography", value="", reason="the Brief names no place")
        )

    if not SpecInput(features=features, roles=tuple(roles)).clean_roles():
        derived, assumption = _derive_roles(norm.audience, features, cleaning)
        roles = (*roles, *derived)
        assumptions.append(assumption)

    # cleaning flags (SPC-IN-06, 07, 08)
    cleaning_flags: list[str] = []
    counted = (
        ("duplicate_features", sum(1 for f in features if f.duplicate_of)),
        ("duplicate_roles", sum(1 for r in roles if r.duplicate_of)),
        ("junk_roles", sum(1 for r in roles if r.junk)),
        ("role_app_name_clash", len(clashes)),
        ("duplicate_apps", sum(1 for a in apps if a.duplicate_of)),
        ("junk_apps", sum(1 for a in apps if a.junk)),
    )
    cleaning_flags += [f"{name}:{n}" for name, n in counted if n]
    live = [f for f in features if f.duplicate_of is None and f.scope_status != "out"]
    nice = sum(1 for f in live if f.priority == "nice")
    if (
        len(live) >= cleaning.mostly_nice_min_features
        and nice / len(live) >= cleaning.mostly_nice_share
    ):
        cleaning_flags.append("priorities_mostly_nice")

    # the intake flags Research also raises
    if any(
        f.kind in {"rule", "compliance", "integration_detail", "nfr"}
        and f.tier == "locked"
        and not f.has_quote
        for f in facts
    ):
        flags.append("locked_fact_without_quote")
    junk = {j.casefold() for j in cleaning.junk_open_question_tokens}
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
    structure = _structure(norm, brief)
    if not structure.present:
        flags.append("structure_missing")

    drift = _drift(brief, norm, grader, scope_rows) + alias_flags
    if locale_assumption:
        drift.append("locales_defaulted")

    idea_raw = m(brief.get("idea")).get("summary")
    idea_trust = grader.slot_trust(idea_raw)
    if not norm.idea.summary:
        idea_trust = Trust(assumed=True)
    experience, access, data_block = (
        m(brief.get("experience")),
        m(brief.get("access")),
        m(brief.get("data")),
    )
    sensitive_raw = slot_value(data_block.get("sensitive", brief.get("sensitive_data")))
    systems_raw = slot_value(data_block.get("existing_systems", brief.get("existing_systems")))
    rule_kinds = set(cleaning.rule_fact_kinds)
    degraded = norm.degraded
    trust = TrustProfile(
        degraded=degraded,
        input_binding=norm.binding,
        probes=norm.failing_probes,
        fallback_count=norm.fallback_count,
        failures=tuple(Failure(call=f.call, reason=f.reason) for f in norm.failures),
        structure_source=norm.structure_source,
        readiness=dict(norm.readiness),
        weak_inputs=degraded or norm.binding != "verified",
    )
    return SpecInput(
        program_id=program_id or norm.program_id or norm.session_id,
        session_id=norm.session_id,
        brief_version=norm.brief_version,
        brief_hash=norm.brief_hash,
        input_binding=norm.binding,
        compass_format=norm.compass_format,
        tier=norm.tier,
        size_band=norm.size_band,
        size_band_source=norm.size_band_source,
        idea=norm.idea,
        idea_trust=idea_trust,
        audience=norm.audience,
        geography=geography,
        locales=locales,
        tone=_setting(experience.get("tone_words"), grader),
        density=_setting(experience.get("density_mode"), grader),
        devices=_setting(experience.get("devices"), grader),
        auth_methods=_setting(access.get("auth_methods"), grader),
        account_model=_setting(access.get("account_model"), grader),
        brand=_setting(experience.get("brand.name"), grader),
        features=features,
        roles=tuple(roles),
        apps=apps,
        name_clashes=clashes,
        modules=tuple(
            Module(
                id=str(r["id"]),
                name=s(r.get("name")),
                kind=s(r.get("kind")),
                purpose=s(r.get("purpose")),
                apps=strs(r.get("apps")),
                features=strs(r.get("features")),
                roles=strs(r.get("roles")),
                entry=s(r.get("entry")),
            )
            for r in rows(brief.get("modules"), "id")
        ),
        structure=structure,
        integrations=tuple(
            Integration(
                name=s(r.get("name")),
                preference=s(r.get("preference")),
                notes=s(r.get("notes")),
                tier=tier_of(r.get("tier")),
                trust=grader.trust(r, min_confidence=cleaning.min_confidence),
            )
            for r in rows(brief.get("integrations"), "name")
        ),
        domain_facts=facts,
        rules_and_numbers=tuple(f for f in facts if f.kind in rule_kinds),
        sensitive_data=strs(sensitive_raw),
        existing_systems=tuple(
            ExistingSystem(
                name=s(r.get("name")), kind=s(r.get("kind")), pointer=s(r.get("pointer"))
            )
            for r in rows(systems_raw, "name")
        ),
        compass_assumptions=tuple(
            CompassAssumption(
                id=str(r["id"]),
                claim=s(r.get("claim")),
                reason=s(r.get("reason")),
                impact=s(r.get("impact")),
                theme=s(r.get("theme")),
                decision=s(r.get("decision")),
            )
            for r in rows(brief.get("assumptions"), "id")
        ),
        glossary=norm.glossary,
        declined=norm.declined,
        references=tuple(s(r.get("label")) for r in rows(brief.get("references"), "label")),
        trust=trust,
        merges=tuple(merges),
        assumptions=tuple(assumptions),
        thin_input=bool(assumptions),
        flags=tuple(dict.fromkeys(flags)),
        drift_flags=tuple(dict.fromkeys(drift)),
        cleaning_flags=tuple(cleaning_flags),
    )
