"""Everything that is DERIVED from the structure of the app, in code (ADR 0017): never stored twice, never asked of a
model. Pure functions of a ``Brief``: no I/O, no model calls, no turn.

* counts, the role x module and role x entity matrices, the navigation edges (from flow steps), the phase of a screen;
* the size band of a session (tier, document claims, the person's override) and the idea-time suggestion;
* the research guidance the research view carries (ADR 0017 point 7);
* the module slice and the outline: what a downstream call reads instead of the whole structure (values only);
* the routing of every detail fact to the module that needs it (so a long interview or a document is never cut by a
  call's size cap) and the list of facts that no module received (the ``fact_orphan`` check).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, JsonValue

from mde_compass.catalog.fact_kinds import kind_for, overlap_stopwords
from mde_compass.catalog.fact_kinds import words_of as fact_words
from mde_compass.catalog.size_bands import (
    BandId,
    SizeBand,
    band_for_tier,
    band_from_claims,
    get_band,
    load_size_bands,
    wider,
)
from mde_compass.domain.brief import Brief, DomainFact
from mde_compass.domain.common import BriefItem

DETAIL_KINDS: frozenset[str] = frozenset(
    {"screen", "flow", "entity", "rule", "number", "acceptance", "integration_detail"}
)
"""The kinds of detail fact that decide what a module contains. Cross-cutting kinds (compliance, nfr, constraint,
context, preference, example, other) go to the outline, not to one module, so they are never "orphans"."""
_META = frozenset({"tier", "provenance", "confidence", "status", "history"})
_EXCLUDED_SCOPE = frozenset({"out", "unsupported", "needs-engineering"})
Phase = Literal["core", "extended", "later"]
BandSource = Literal["tier", "document", "user"]
MIN_WORD = 4  # a word of fewer letters is too common to show that a fact belongs to a module


@dataclass(frozen=True)
class Counts:
    modules: int
    screens: int
    flows: int
    entities: int


def counts(brief: Brief) -> Counts:
    return Counts(len(brief.modules), len(brief.screens), len(brief.flows), len(brief.entities))


def has_structure(brief: Brief) -> bool:
    return bool(brief.modules or brief.screens or brief.flows or brief.entities)


def values(record: BaseModel) -> dict[str, JsonValue]:
    """A record as values only: no tier, provenance, confidence, status or history (what a consumer may read)."""
    dumped = record.model_dump(mode="json", exclude=set(_META))
    return {k: v for k, v in dumped.items() if v not in (None, "", [], {})}


# ----------------------------------------------------------------------------------------- derived views


def role_matrix(brief: Brief) -> dict[str, dict[str, list[str]]]:
    """role id -> module id -> the screens of that module the role can open (roles with no screen are absent)."""
    out: dict[str, dict[str, list[str]]] = {}
    for screen in brief.screens:
        for role in screen.roles:
            out.setdefault(role, {}).setdefault(screen.module, []).append(screen.id)
    return out


def entity_matrix(brief: Brief) -> dict[str, dict[str, str]]:
    """role id -> entity id -> what the role may do with it (from each entity's ``access``)."""
    out: dict[str, dict[str, str]] = {}
    for entity in brief.entities:
        for role, level in entity.access.items():
            out.setdefault(role, {})[entity.id] = level
    return out


def nav_edges(brief: Brief) -> list[tuple[str, str]]:
    """Screen -> screen edges taken from consecutive flow steps, de-duplicated, in flow order. The navigation tree
    itself (tabs, stacks, routes) belongs to the later stage; this is only what the journeys already say."""
    seen: dict[tuple[str, str], None] = {}
    for flow in brief.flows:
        for a, b in zip(flow.steps, flow.steps[1:], strict=False):
            if a.screen != b.screen:
                seen.setdefault((a.screen, b.screen), None)
    return list(seen)


def phase_of(brief: Brief) -> dict[str, Phase]:
    """The phase of each screen, derived from the priority the person already gave the features (the closing
    question): ``core`` serves a ``must`` feature (directly or through a flow of one), ``later`` serves only features
    set for later, everything else is ``extended``. A model never assigns a phase."""
    feature = {f.id: f for f in brief.scope}
    core_features = {f.id for f in brief.scope if f.priority == "must" and f.scope_status == "in"}
    flow_features: dict[str, set[str]] = {}
    for flow in brief.flows:
        for step in flow.steps:
            flow_features.setdefault(step.screen, set()).update(flow.features)
    out: dict[str, Phase] = {}
    for screen in brief.screens:
        served = set(screen.serves) | flow_features.get(screen.id, set())
        known = [feature[f] for f in served if f in feature]
        if served & core_features:
            out[screen.id] = "core"
        elif known and all(f.scope_status == "later" or f.priority == "later" for f in known):
            out[screen.id] = "later"
        else:
            out[screen.id] = "extended"
    return out


def excluded_features(brief: Brief) -> set[str]:
    """Features the person or the stack ruled out: no screen belongs to them."""
    return {f.id for f in brief.scope if f.scope_status in _EXCLUDED_SCOPE}


# ------------------------------------------------------------------------------------------ size and band


def effective_band(
    tier: str, *, claims: int = 0, override: str | None = None
) -> tuple[BandId, BandSource]:
    """The band of a session: the tier's, raised by a document's verified claims, or the person's own choice.
    A document or a person can only RAISE what the tier gives; an override is the person's word and wins."""
    if override in load_size_bands().bands:
        return get_band(str(override)).id, "user"
    base = band_for_tier(tier).id
    doc = band_from_claims(claims)
    if doc is not None and wider(base, doc) != base:
        return doc, "document"
    return base, "tier"


def _distinct_roles(brief: Brief) -> int:
    if brief.roles:
        return len(brief.roles)
    hypothesis = brief.meta.hypothesis
    names = {
        r.strip().casefold() for r in (hypothesis.likely_roles if hypothesis else []) if r.strip()
    }
    return len(names)


def suggest_band(brief: Brief, tier_band: BandId) -> BandId | None:
    """A larger band than the tier's when the idea clearly asks for one (idea time; the caller shows ONE notice with
    a one-tap tier chip, it never switches the tier). None when the tier already fits or the signals are weak."""
    data = load_size_bands()
    profile = brief.meta.situation_profile
    plan = brief.meta.plan
    roles = _distinct_roles(brief)
    apps = len(brief.apps)
    features = len(brief.scope) or len(
        brief.meta.hypothesis.likely_features if brief.meta.hypothesis else []
    )
    found: BandId | None = None
    for rule in data.suggest:
        hit = (
            (profile is not None and profile.size_stakes in rule.size_stakes)
            or (plan is not None and plan.depth in rule.plan_depth)
            or (rule.roles_min and roles >= rule.roles_min)
            or (rule.apps_min and apps >= rule.apps_min)
            or (rule.features_min and features >= rule.features_min)
        )
        if hit:
            found = rule.band if found is None else wider(found, rule.band)
    if found is None or wider(found, tier_band) == tier_band:
        return None
    return found


def size_verdict(brief: Brief, band: SizeBand) -> Literal["fits", "over", "under"]:
    """How the drafted size compares with the band: ``over`` recommends the next band up, ``under`` is only a warning
    (a small app in a large tier stays small)."""
    data = load_size_bands()
    n = len(brief.screens)
    if band.over_limit(n, data.over_band_factor):
        return "over"
    if band.under_floor(n, data.thin_factor):
        return "under"
    return "fits"


# ------------------------------------------------------------------------------------- research guidance


def _item_text(item: BriefItem | None) -> str:
    value = item.value if item is not None and item.has_value() else ""
    return value if isinstance(value, str) else ""


def research_guidance(brief: Brief, band_id: str) -> dict[str, JsonValue]:
    """What kind of app this is and how deep research should go (values only; ADR 0017 point 7). The depth follows the
    band; the focus lists what is worth looking up, taken from the structure and the slots."""
    band = get_band(band_id)
    hypothesis = brief.meta.hypothesis
    guidance: dict[str, JsonValue] = {
        "size_band": band.id,
        "depth": band.research.depth,
        "search_budget": band.research.searches,
        "gap_fill": band.research.gap_fill,
        "max_requests": band.research.max_requests,
    }
    for key, text in (
        ("category", _item_text(brief.idea.get("category"))),
        ("subcategory", _item_text(brief.idea.get("subcategory"))),
        ("business_model", _item_text(brief.idea.get("business_model"))),
    ):
        if text:
            guidance[key] = text
    if hypothesis is not None and hypothesis.likely_families:
        guidance["families"] = list(dict.fromkeys(hypothesis.likely_families))
    sensitive = brief.data.get("sensitive")
    compliance = [
        f.statement
        for f in brief.domain_facts
        if (f.kind or kind_for(f.type, f.topics)) == "compliance"
    ]
    focus: list[JsonValue] = []

    def add(topic: str, about: Sequence[str]) -> None:
        names = list(dict.fromkeys(a for a in about if a))
        if names:
            focus.append({"topic": topic, "about": names})  # type: ignore[arg-type]

    add(
        "landscape",
        [m.name for m in brief.modules] or [f.name for f in brief.scope if f.scope_status == "in"],
    )
    add("domain", [e.name for e in brief.entities])
    add("integrations", [i.name for i in brief.integrations])
    sensitive_values = (
        sensitive.value if sensitive is not None and isinstance(sensitive.value, list) else []
    )
    add("compliance", [str(v) for v in sensitive_values if v != "none"] + compliance[:6])
    add("references", [r.label for r in brief.references])
    locales = brief.experience.get("locales")
    if locales is not None and isinstance(locales.value, list) and locales.value:
        add("locales", [str(v) for v in locales.value])
    if focus:
        guidance["focus"] = focus
    return guidance


# ------------------------------------------------------------------------- fact routing and module slices


@dataclass
class FactRouting:
    by_module: dict[str, list[DomainFact]] = field(default_factory=dict)
    general: list[DomainFact] = field(default_factory=list)  # cross-cutting: they go to the outline
    orphans: list[DomainFact] = field(default_factory=list)  # a detail fact no module received


def _significant(text: str) -> set[str]:
    stop = overlap_stopwords()
    return {w for w in fact_words(text) if len(w) >= MIN_WORD and w not in stop}


def module_ids_of(brief: Brief, module_id: str) -> set[str]:
    """Every id that belongs to a module: itself, its screens, the flows that touch them, the entities they use or it
    owns, and the features and roles it names."""
    module = next((m for m in brief.modules if m.id == module_id), None)
    if module is None:
        return set()
    screens = [s for s in brief.screens if s.module == module_id]
    screen_ids = {s.id for s in screens}
    ids: set[str] = {module_id, *module.features, *module.roles, *screen_ids}
    for screen in screens:
        ids.update(screen.serves)
        ids.update(screen.roles)
        ids.update(use.entity for use in screen.entities)
    for flow in brief.flows:
        if any(step.screen in screen_ids for step in flow.steps):
            ids.add(flow.id)
    ids.update(e.id for e in brief.entities if e.module == module_id)
    return ids


def _module_words(brief: Brief, module_id: str) -> set[str]:
    module = next(m for m in brief.modules if m.id == module_id)
    texts = [module.name, module.purpose]
    for screen in brief.screens:
        if screen.module == module_id:
            texts += [screen.name, screen.purpose]
    texts += [e.name for e in brief.entities if e.module == module_id]
    feature = {f.id: f.name for f in brief.scope}
    texts += [feature[f] for f in module.features if f in feature]
    return _significant(" ".join(texts))


def route_facts(brief: Brief, facts: Iterable[DomainFact] | None = None) -> FactRouting:
    """Send every active detail fact to the module that needs it: by the ids its ``refs`` name, otherwise by the real
    words it shares with a module (the best match only). A detail fact no module received is an orphan; a cross-cutting
    fact (compliance, nfr, constraint, context...) goes to the outline. Nothing is dropped for being long."""
    routing = FactRouting()
    module_ids = {m.id: module_ids_of(brief, m.id) for m in brief.modules}
    module_words = {m.id: _module_words(brief, m.id) for m in brief.modules}
    for fact in brief.domain_facts if facts is None else facts:
        kind = fact.kind or kind_for(fact.type, fact.topics)
        if kind not in DETAIL_KINDS:
            routing.general.append(fact)
            continue
        refs = set(fact.refs)
        hit = [mid for mid, ids in module_ids.items() if refs & ids]
        if not hit:
            wanted = _significant(fact.statement)
            scores = {mid: len(wanted & words) for mid, words in module_words.items()}
            best = max(scores.values(), default=0)
            hit = [mid for mid, score in scores.items() if best and score == best]
        if not hit:
            routing.orphans.append(fact)
            continue
        for mid in hit:
            routing.by_module.setdefault(mid, []).append(fact)
    return routing


def _fact_row(fact: DomainFact) -> dict[str, JsonValue]:
    row: dict[str, JsonValue] = {"id": fact.id, "statement": fact.statement}
    kind = fact.kind or kind_for(fact.type, fact.topics)
    if kind:
        row["kind"] = kind
    return row


def outline(brief: Brief) -> dict[str, JsonValue]:
    """Ids and names only: the first thing a downstream call reads, about 1-2K tokens even for a 60-screen app."""
    by_module: dict[str, list[JsonValue]] = {}
    for screen in brief.screens:
        by_module.setdefault(screen.module, []).append({"id": screen.id, "name": screen.name})
    modules: list[JsonValue] = []
    for module in brief.modules:
        row: dict[str, JsonValue] = {"id": module.id, "name": module.name}
        if module.purpose:
            row["purpose"] = module.purpose
        row["screens"] = by_module.get(module.id, [])
        modules.append(row)
    c = counts(brief)
    return {
        "counts": {
            "modules": c.modules,
            "screens": c.screens,
            "flows": c.flows,
            "entities": c.entities,
        },
        "modules": modules,
        "entities": [{"id": e.id, "name": e.name} for e in brief.entities],  # type: ignore[misc]
        "flows": [{"id": f.id, "name": f.name} for f in brief.flows],  # type: ignore[misc]
    }


def module_slice(
    brief: Brief, module_id: str, *, routing: FactRouting | None = None
) -> dict[str, JsonValue]:
    """One module with everything a downstream call needs to build it, values only: its screens; the flows that
    touch them (steps outside the module appear as id and name stubs); the entities they use or the module owns; the
    roles involved and what each may do; the features it serves; and the detail facts routed to it. It fits one call
    whatever the size of the app."""
    module = next((m for m in brief.modules if m.id == module_id), None)
    if module is None:
        raise KeyError(module_id)
    routing = routing or route_facts(brief)
    screens = [s for s in brief.screens if s.module == module_id]
    screen_ids = {s.id for s in screens}
    name_of = {s.id: s.name for s in brief.screens}
    flows: list[JsonValue] = []
    for flow in brief.flows:
        if not any(step.screen in screen_ids for step in flow.steps):
            continue
        row = values(flow)
        row["steps"] = [
            {
                "screen": step.screen,
                "action": step.action,
                **({} if step.screen in screen_ids else {"name": name_of.get(step.screen, "")}),
            }
            for step in flow.steps
        ]
        flows.append(row)
    used = {use.entity for s in screens for use in s.entities}
    entities = [e for e in brief.entities if e.id in used or e.module == module_id]
    role_ids = {r for s in screens for r in s.roles} | set(module.roles)
    roles: list[JsonValue] = [
        {"id": r.id, "name": r.name_in_user_words, **({"goal": r.goal} if r.goal else {})}
        for r in brief.roles
        if r.id in role_ids
    ]
    access: list[JsonValue] = [
        {"entity": e.id, "role": role, "level": level}
        for e in entities
        for role, level in e.access.items()
        if role in role_ids
    ]
    phases = phase_of(brief)
    served = {f for s in screens for f in s.serves} | set(module.features)
    features: list[JsonValue] = [values(f) for f in brief.scope if f.id in served]
    out: dict[str, JsonValue] = {
        "module": values(module),
        "screens": [{**values(s), "phase": phases.get(s.id, "extended")} for s in screens],  # type: ignore[misc]
        "flows": flows,
        "entities": [values(e) for e in entities],  # type: ignore[misc]
        "roles": roles,
        "access": access,
        "features": features,
    }
    facts = routing.by_module.get(module_id, [])
    if facts:
        out["details"] = [_fact_row(f) for f in facts]  # type: ignore[assignment]
    return {k: v for k, v in out.items() if v not in (None, [], {})}


def slice_tokens(slice_: Mapping[str, Any]) -> int:
    """A rough token estimate of a slice (characters / 4, like the rest of Compass): used to keep every slice inside
    the budget a downstream call can read."""
    import json

    return len(json.dumps(slice_, ensure_ascii=False)) // 4
