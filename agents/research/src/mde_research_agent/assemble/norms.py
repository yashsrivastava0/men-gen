"""Norms (how common is each choice), conflicts and suggestions, computed by CODE from the built items.

A norm says "seen in n of N sources" with a level (core, common, advanced, emerging, limited_evidence) and the newest
source date. For features the sources are the COMPARABLE APPS (each competitor lists features); for screens, flows,
entities, integrations and design conventions they are independent organisations (domains). Norm keys are
reconciled so two wordings count as one. A norm that matches nothing in the Brief becomes a SUGGESTION: always
``undecided``, never scope. Different values for the same rule are CONFLICTS (flagged, put on the confirm list).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from datetime import date

from mde_research_agent.assemble.items import BuiltItem, canonical_amount, typed_slot
from mde_research_agent.catalog.content import dossier_config
from mde_research_agent.catalog.models import PrevalenceConfig
from mde_research_agent.core.applies import problem as applies_problem  # noqa: F401  (re-exported for callers)
from mde_research_agent.core.norm_keys import clean_key, match_brief, reconcile
from mde_research_agent.core.prevalence import age_months, make_prevalence
from mde_research_agent.core.sources import independent_count, own_site
from mde_research_agent.core.text import stem_set
from mde_research_agent.core.urls import host_of
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.research import (
    Conflict,
    Norm,
    Prevalence,
    Recency,
    Suggestion,
    Suggestions,
)

GENERIC = frozenset({"app", "user", "list", "screen", "page", "manage", "view"})


def _sources_of(
    members: list[BuiltItem], *, skip: Callable[[str], bool] | None = None
) -> list[tuple[str, str]]:
    """``(url, passage window)`` of every source of the items, one per page, optionally without some domains."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for b in members:
        for url, window in b.windows:
            if url in seen or (skip is not None and skip(url)):
                continue
            seen.add(url)
            out.append((url, window))
    return out


def _independent(members: list[BuiltItem], *, skip: Callable[[str], bool] | None = None) -> int:
    """RV-19: how many INDEPENDENT sources the items have. One organisation is one source, and two organisations
    giving the same paragraph are one source (the passage window around the quote is compared)."""
    src = _sources_of(members, skip=skip)
    return independent_count([u for u, _ in src], texts=[w for _, w in src]) if src else 0


def _is_own(url: str, pool: list[BuiltItem]) -> bool:
    """Is ``url`` the home of any provider in ``pool``?"""
    host = host_of(url)
    return any(own_site(str(b.item.data.get("provider", "")), host) for b in pool)


def _need_of(b: BuiltItem) -> str:
    """The integration need of a provider item: the need key code mapped it to (the INT entry of ``applies_to``), else the
    words the page used. One provider's facts and its summary therefore meet in ONE norm."""
    for a in b.item.applies_to:
        if a.kind == "INT" and a.id and b.item.kind == "provider_fact":
            return clean_key(a.id)
    return clean_key(str(b.item.data.get("need", "")))


def _count_windows(pairs: list[tuple[str, str]]) -> int:
    return independent_count([u for u, _ in pairs], texts=[w for _, w in pairs]) if pairs else 0


def _newest(items: list[BuiltItem]) -> date | None:
    dates: list[date] = []
    for b in items:
        recency = b.item.recency
        if recency is not None and recency.newest_source is not None:
            dates.append(recency.newest_source)
    return max(dates) if dates else None


def _rising(norm_key: str, built: list[BuiltItem]) -> bool:
    keys = stem_set(norm_key)
    return any(
        b.item.kind == "trend"
        and b.item.data.get("direction") == "rising"
        and keys & stem_set(str(b.item.data.get("topic", "")))
        for b in built
    )


def compute_norms(
    built: list[BuiltItem], inp: ResearchInput, cfg: PrevalenceConfig, as_of: date
) -> list[Norm]:
    norms: list[Norm] = []
    built = [b for b in built if not b.excluded]

    def add(
        area: str,
        key: str,
        label: str,
        seen: int,
        of: int,
        members: list[BuiltItem],
        matches: str | None,
    ) -> None:
        newest = _newest(members)
        rising = _rising(key, built)
        age = age_months(newest, as_of)
        prev = make_prevalence(seen, of, newest_age_months=age, rising=rising, cfg=cfg)
        norms.append(
            Norm(
                area=area,  # type: ignore[arg-type]
                key=key,
                label=label,
                prevalence=prev,  # type: ignore[arg-type]
                recency=Recency(
                    newest_source=newest, age_months=age, trend="rising" if rising else "unknown"
                ),
                item_ids=sorted({m.item.id for m in members}),
                matches_brief=matches,
            )
        )

    # --- features: the comparable apps are the sources --------------------------------------------------------
    apps = [b for b in built if b.item.kind in ("competitor", "reference_check")]
    if apps:
        raw = [
            ("feature", clean_key(str(f.get("feature_key", ""))))
            for b in apps
            for f in b.item.data.get("features", [])
        ]
        mapping = reconcile(raw)
        by_key: dict[str, dict[str, BuiltItem]] = defaultdict(dict)
        for b in apps:
            who = clean_key(str(b.item.data.get("name", ""))) or b.item.id
            for f in b.item.data.get("features", []):
                key = mapping.get(("feature", clean_key(str(f.get("feature_key", "")))), "")
                if key:
                    by_key[key][who] = b
        n_apps = len({clean_key(str(b.item.data.get("name", ""))) or b.item.id for b in apps})
        names = {f.id: f.name for f in inp.features if f.duplicate_of is None}
        for key in sorted(by_key):
            members = list(by_key[key].values())
            add(
                "feature",
                key,
                key,
                len(by_key[key]),
                n_apps,
                members,
                match_brief(key, names, generic=GENERIC),
            )

    # --- screens, flows, entities, integrations, design: independent organisations are the sources -------------------
    def by_areas(kind: str, area: str) -> tuple[list[BuiltItem], dict[str, list[BuiltItem]]]:
        pool = [b for b in built if b.item.kind == kind and b.areas_key]
        groups: dict[str, list[BuiltItem]] = defaultdict(list)
        for b in pool:
            groups[b.areas_key[1]].append(b)  # type: ignore[index]
        return pool, groups

    flow_names = {f.id: f.name for f in inp.flows} | {
        f.id: f.name for f in inp.features if f.duplicate_of is None
    }
    ent_names = {e.id: e.name for e in inp.entities}
    for kind, area, brief_names in (
        ("screen_pattern", "screen", {}),
        ("workflow", "flow", flow_names),
        ("entity_pattern", "entity", ent_names),
        ("design_convention", "design", {}),
    ):
        pool, groups = by_areas(kind, area)
        if not pool:
            continue
        total = _independent(pool)
        for key in sorted(groups):
            members = groups[key]
            seen = _independent(members)
            add(
                area,
                key,
                key,
                seen,
                total,
                members,
                match_brief(key, brief_names, generic=GENERIC) if brief_names else None,
            )
    # --- integrations: ONE norm per (need, provider), merged across items. ``seen_in`` counts only INDEPENDENT
    # third-party mentions: a provider's own documentation is evidence of what it offers (``official``), not a
    # vote that others use it. ``of`` is the third-party sources for the need.
    svc = [b for b in built if b.item.kind in ("service_option", "provider_fact")]
    if svc:
        by_need: dict[str, dict[str, list[BuiltItem]]] = defaultdict(lambda: defaultdict(list))
        for b in svc:
            need = _need_of(b)
            prov = clean_key(str(b.item.data.get("provider", "")))
            by_need[need][prov].append(b)
        for need in sorted(by_need):
            pool = [m for ms in by_need[need].values() for m in ms]
            for prov in sorted(by_need[need]):
                members = sorted(by_need[need][prov], key=lambda x: x.item.id)
                name = str(members[0].item.data.get("provider", prov))
                total = _independent(pool, skip=lambda u, pool=pool: _is_own(u, pool))
                seen = _independent(members, skip=lambda u, n=name: own_site(n, host_of(u)))
                add(
                    "integration",
                    f"{need}: {prov}",
                    name,
                    seen,
                    max(total, seen),
                    members,
                    need if need else None,
                )
    return sorted(norms, key=lambda n: (n.area, n.key))


def apply_norms(built: list[BuiltItem], norms: list[Norm]) -> list[BuiltItem]:
    """Attach each norm's prevalence to the items it describes (and flag limited evidence)."""
    lookup: dict[tuple[str, str], Norm] = {}
    for n in norms:
        if n.area in ("screen", "flow", "entity", "design"):
            lookup[(n.area, n.key)] = n
    by_item: dict[str, Prevalence] = {}
    for n in norms:
        if n.area == "integration":
            for iid in n.item_ids:
                by_item[iid] = n.prevalence
    for b in built:
        prev: Prevalence | None = by_item.get(b.item.id)
        if prev is None and b.areas_key and b.areas_key in lookup:
            prev = lookup[b.areas_key].prevalence
        if prev is not None:
            flags = set(b.item.flags)
            if prev.level == "limited_evidence":
                flags.add("limited_evidence")
            b.item = b.item.model_copy(update={"prevalence": prev, "flags": sorted(flags)})
    return built


def detect_conflicts(built: list[BuiltItem], inp: ResearchInput | None = None) -> list[Conflict]:
    """Rules about the same subject and unit with different values: keep both, flag, ask for confirmation."""
    groups: dict[tuple[str, str], list[BuiltItem]] = defaultdict(list)
    for b in built:
        if b.item.kind == "rule" and b.item.data.get("value"):
            groups[
                (
                    clean_key(str(b.item.data.get("subject", ""))),
                    clean_key(str(b.item.data.get("unit", ""))),
                )
            ].append(b)
    out: list[Conflict] = []
    for (subject, _unit), members in sorted(groups.items()):
        values = {clean_key(str(m.item.data["value"])) for m in members}
        if len(members) < 2 or len(values) < 2:
            continue
        for m in members:
            flags = set(m.item.flags) | {"conflict", "needs-confirm"}
            m.item = m.item.model_copy(update={"flags": sorted(flags), "tier": "open"})
        listing = "; ".join(
            f"{m.item.data['value']} {m.item.data.get('unit') or ''} ({m.item.sources[0].domain if m.item.sources else '?'})".strip()
            for m in members
        )
        out.append(
            Conflict(
                topic=subject or "rule",
                item_ids=sorted(m.item.id for m in members),
                note=listing[:240],
            )
        )
    out += _provider_conflicts(built)
    out += _against_locked(built, inp)
    return out


def _provider_conflicts(built: list[BuiltItem]) -> list[Conflict]:
    """Provider facts that state DIFFERENT values for the same thing: two amounts for one provider, field, unit, period,
    condition, currency and country, or two statuses. Both items stay (they are separate items by construction), both are
    flagged ``conflict`` and go on the confirm list; the dossier shows the field as a conflict with both values.
    Fields of kind ``list`` never conflict: several values side by side are the normal case."""
    cfg = dossier_config()
    groups: dict[tuple[str, ...], list[tuple[str, BuiltItem]]] = defaultdict(list)
    for b in built:
        if b.item.kind != "provider_fact" or b.excluded:
            continue
        d = b.item.data
        spec = cfg.fields.get(str(d.get("field", "")))
        if spec is None or spec.kind == "list":
            continue
        where = (
            b.item.about_region,
            *sorted(clean_key(str(c)) for c in d.get("countries", [])),
        )
        base = (clean_key(str(d.get("provider", ""))), str(d.get("field")), *where)
        if spec.kind == "typed":
            amount = canonical_amount(d.get("typed"))
            if amount:
                currency = amount.split(" ", 1)[1] if " " in amount else ""
                groups[(*base, currency, *typed_slot(d.get("typed")))].append((amount, b))
        elif d.get("value"):
            groups[base].append((clean_key(str(d["value"])), b))
    out: list[Conflict] = []
    for key, members in sorted(groups.items()):
        if len({v for v, _ in members}) < 2:
            continue
        for _, m in members:
            flags = set(m.item.flags) | {"conflict", "needs-confirm"}
            m.item = m.item.model_copy(update={"flags": sorted(flags), "tier": "open"})
        listing = "; ".join(
            f"{m.item.data.get('value')} ({m.item.sources[0].domain if m.item.sources else '?'})"
            for _, m in members
        )
        out.append(
            Conflict(
                topic=f"{members[0][1].item.data.get('provider')}: {key[1]}"[:120],
                item_ids=sorted(m.item.id for _, m in members),
                note=listing[:240],
            )
        )
    return out


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _against_locked(built: list[BuiltItem], inp: ResearchInput | None) -> list[Conflict]:
    """A researched value that contradicts a number the person stated (Locked) is shown as a conflict; Locked wins."""
    if inp is None:
        return []
    locked = [
        (f, set(_NUM.findall(f.statement)))
        for f in inp.facts
        if f.tier == "locked"
        and f.has_quote
        and f.kind in ("rule", "number")
        and _NUM.search(f.statement)
    ]
    out: list[Conflict] = []
    for b in built:
        d = b.item.data
        if b.item.kind != "rule" or not d.get("value"):
            continue
        mine = set(_NUM.findall(str(d["value"])))
        if not mine:
            continue
        words = stem_set(f"{d.get('subject', '')} {b.item.label}")
        for fact, nums in locked:
            if len(words & stem_set(fact.statement)) >= 2 and nums and not (nums & mine):
                flags = set(b.item.flags) | {"conflict", "needs-confirm"}
                b.item = b.item.model_copy(update={"flags": sorted(flags), "tier": "open"})
                out.append(
                    Conflict(
                        topic=f"{d.get('subject', 'rule')} (your Brief)",
                        item_ids=[b.item.id],
                        note=f"Your Brief says {', '.join(sorted(nums))} (Locked); the web says {d['value']}. Locked wins."[
                            :240
                        ],
                    )
                )
                break
    return out


def build_suggestions(built: list[BuiltItem], norms: list[Norm]) -> Suggestions:
    """Beyond the Brief: norms and details that match nothing the person asked for. Always undecided."""
    by_id = {b.item.id: b for b in built}
    sug = Suggestions()
    for n in norms:
        if n.matches_brief or n.prevalence.level in ("advanced", "limited_evidence"):
            continue
        s = Suggestion(label=n.label, item_ids=n.item_ids[:6], prevalence=n.prevalence)
        if n.area == "feature":
            sug.features.append(s)
        elif n.area == "screen":
            sug.screens.append(s)
    fields: dict[str, list[tuple[str, str]]] = defaultdict(list)
    field_items: dict[str, set[str]] = defaultdict(set)
    states: dict[str, list[tuple[str, str]]] = defaultdict(list)
    state_items: dict[str, set[str]] = defaultdict(set)
    valids: dict[str, list[tuple[str, str]]] = defaultdict(list)
    valid_items: dict[str, set[str]] = defaultdict(set)
    for b in built:
        if b.excluded:
            continue
        orgs = _sources_of([b])
        d = b.item.data
        if b.item.kind == "entity_pattern":
            for f in d.get("fields", []):
                label = f"{clean_key(str(d.get('entity', '')))}: {clean_key(str(f.get('name', '')))} ({f.get('type', '')})"
                fields[label] += orgs
                field_items[label].add(b.item.id)
        if b.item.kind == "lifecycle":
            for st in d.get("states", []):
                label = f"{clean_key(str(d.get('entity', '')))}: {clean_key(str(st))}"
                states[label] += orgs
                state_items[label].add(b.item.id)
        if b.item.kind == "screen_pattern":
            for v in d.get("validations", []):
                valids[clean_key(str(v))] += orgs
                valid_items[clean_key(str(v))].add(b.item.id)
            for stt in d.get("states", []):
                label = f"{clean_key(str(d.get('screen_key', '')))}: {stt.get('state', '')}"
                states[label] += orgs
                state_items[label].add(b.item.id)
    del by_id
    for store, items_map, out in (
        (fields, field_items, sug.fields),
        (states, state_items, sug.states),
        (valids, valid_items, sug.validations),
    ):
        for label in sorted(store):
            if _count_windows(list(dict.fromkeys(store[label]))) >= 2 and label.strip(": "):
                out.append(Suggestion(label=label, item_ids=sorted(items_map[label])[:6]))
    return sug
