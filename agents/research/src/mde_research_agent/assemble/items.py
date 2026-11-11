"""Build ``research.v5`` items from verified atoms: group, merge, source, flag, score confidence.

Atoms about the same thing (a competitor named the same on several pages, the same rule value on two official
pages) become ONE item with several sources, so ``independent_domains`` and corroboration mean something. Different
rule values stay separate items (a conflict is information). Everything here is deterministic: groups are ordered by
(job, kind, key), atoms inside a group by a stable key, ids are assigned after sorting, so completion order never
shows in the result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from mde_research_agent.catalog.loader import JOB_NAMES
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.applies import BriefIndex, retag
from mde_research_agent.domain.atoms import ORDERED_FIELDS
from mde_research_agent.core.norm_keys import clean_key, match_brief, reconcile
from mde_research_agent.core.prevalence import is_fee_text, is_stale, recency_of
from mde_research_agent.core.sources import can_support_high_impact, independent_count, org_key
from mde_research_agent.engine.checking import passage_window
from mde_research_agent.core.text import stem_set
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.plan import Target
from mde_research_agent.domain.research import AppliesTo, Item, Recency, SourceRef
from mde_research_agent.engine.context import RunContext
from mde_research_agent.engine.state import AtomRecord, PageRecord

CLASS_RANK = {"official": 0, "help": 1, "press": 2, "store": 3, "blog": 4, "forum": 5, "seo": 6}
RELATION = {
    "rule": "constrains", "compliance_note": "constrains", "entity_pattern": "refines", "lifecycle": "refines",
    "screen_pattern": "refines", "workflow": "refines", "role_pattern": "refines", "navigation_pattern": "refines",
}  # fmt: skip


@dataclass
class BuiltItem:
    item: Item
    atoms: list[AtomRecord]
    group: tuple[str, ...]
    areas_key: tuple[str, str] | None = (
        None  # (area, canonical norm key) for kinds that have a norm
    )
    pages: list[PageRecord] = field(default_factory=list)
    target_ids: set[str] = field(default_factory=set)
    windows: list[tuple[str, str]] = field(default_factory=list)  # (page url, passage window): content-independence
    excluded: bool = False  # every source is about another region or entity: out of corroboration, norms, prevalence


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _canon(value: Any) -> str:
    return clean_key(str(value or ""))


def _group_key(a: AtomRecord, mapping: dict[tuple[str, str], str], area: str) -> tuple[str, ...]:
    d = a.data
    k = a.kind
    if k in ("competitor", "reference_check"):
        return (k, _canon(d.get("name")))
    if k == "service_option":
        return (k, _canon(d.get("need")), _canon(d.get("provider")))
    if k == "rule":
        return (
            k,
            mapping.get((area, a.norm_key), a.norm_key),
            _canon(d.get("value")),
            _canon(d.get("unit")),
        )
    if k == "compliance_note":
        return (k, _canon(d.get("topic")), _canon(d.get("authority")))
    if k == "design_convention":
        return (k, str(d.get("aspect")), mapping.get((area, a.norm_key), a.norm_key))
    if k == "local_format":
        return (k, str(d.get("kind")), _canon(d.get("pattern")))
    if k == "data_hint":
        return (k, str(d.get("kind")), mapping.get((area, a.norm_key), a.norm_key))
    if k == "trend":
        return (k, _canon(d.get("topic")))
    return (k, mapping.get((area, a.norm_key), a.norm_key))


def _richness(a: AtomRecord) -> int:
    return sum(len(v) for v in a.data.values() if isinstance(v, list)) + sum(
        1 for v in a.data.values() if v
    )


def _merge_lists(a: list[Any], b: list[Any]) -> list[Any]:
    seen = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in a}
    out = list(a)
    for x in b:
        key = json.dumps(x, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def _variant_of(value: Any) -> list[str]:
    """An ordered list as comparable strings (a transition becomes ``from>to: trigger``)."""
    out: list[str] = []
    for x in value or []:
        if isinstance(x, dict):
            out.append(f"{x.get('from_state', '')}>{x.get('to_state', '')}: {x.get('trigger', '')}".strip())
        else:
            out.append(str(x))
    return out


def merge_payloads(
    primary: dict[str, Any], others: list[dict[str, Any]], kind: str = ""
) -> dict[str, Any]:
    """The primary payload enriched by the others: lists are unioned, empty scalars filled.

    ORDERED lists (steps, sections, transitions) are never joined: two sources order a journey differently and the
    union would be a third, invented, order. The primary (richest) list is kept; a different list from another source
    goes to the payload's ``*_variants`` field (at most three).
    """
    ordered = dict([ORDERED_FIELDS[kind]]) if kind in ORDERED_FIELDS else {}
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in primary.items()}
    variants: list[list[str]] = []
    for o in others:
        for k, v in o.items():
            if k in ordered:
                alt = _variant_of(v)
                if alt and alt != _variant_of(out.get(k)) and alt not in variants:
                    variants.append(alt)
            elif k in {vf for vf in ordered.values()}:
                continue
            elif isinstance(v, list) and isinstance(out.get(k), list):
                out[k] = _merge_lists(out[k], v)
            elif out.get(k) in (None, "", []) and v not in (None, "", []):
                out[k] = v
    if variants:
        out[ordered[next(iter(ordered))]] = variants[:3]
    return out


def _source_ref(page: PageRecord, ids: dict[str, str]) -> SourceRef:
    return SourceRef(
        id=ids[page.url],
        domain=page.domain,
        source_class=page.source_class,  # type: ignore[arg-type]
        url=page.final_url or page.url,
        retrieved_on=page.retrieved_on,
        published_on=page.published,
    )


def _applies(
    target_links: Any,
    kind: str,
    norm_key: str,
    inp: ResearchInput,
    area: str | None,
    index: BriefIndex,
) -> list[AppliesTo]:
    rel = RELATION.get(kind, "expects")
    out: list[AppliesTo] = []
    for fid in target_links.features:
        out.append(AppliesTo(kind="FTR", id=fid, relation=rel))  # type: ignore[arg-type]
    for rid in target_links.roles:
        out.append(AppliesTo(kind="ROL", id=rid, relation=rel))  # type: ignore[arg-type]
    for aid in target_links.apps:
        out.append(AppliesTo(kind="APP", id=aid, relation=rel))  # type: ignore[arg-type]
    for mid in target_links.modules:
        out.append(AppliesTo(kind="MOD", id=mid, relation=rel))  # type: ignore[arg-type]
    for eid in target_links.entities:
        out.append(AppliesTo(kind="ENT", id=eid, relation=rel))  # type: ignore[arg-type]
    for fl in target_links.flows:
        out.append(AppliesTo(kind="FLW", id=fl, relation=rel))  # type: ignore[arg-type]
    for key in target_links.integrations:
        out.append(AppliesTo(kind="INT", id=clean_key(key), relation=rel))  # type: ignore[arg-type]
    generic = frozenset({"app", "user", "list", "screen", "page"})
    names: dict[str, str] = {}
    if area == "feature":
        names = {f.id: f.name for f in inp.features if f.duplicate_of is None}
        hit = match_brief(norm_key, names, generic=generic)
        if hit:
            out.append(AppliesTo(kind="FTR", id=hit, relation="expects"))
    elif area == "entity":
        names = {e.id: e.name for e in inp.entities}
        hit = match_brief(norm_key, names, generic=generic)
        if hit:
            out.append(AppliesTo(kind="ENT", id=hit, relation="refines"))
    elif area == "flow":
        names = {f.id: f.name for f in inp.flows}
        hit = match_brief(norm_key, names, generic=generic)
        if hit:
            out.append(AppliesTo(kind="FLW", id=hit, relation="refines"))
    if not out:
        out.append(AppliesTo(kind="NEW", id=None, relation="suggests"))
    fixed, _ = retag(out, index)
    return fixed


LOW_CLASSES = frozenset({"forum", "blog", "seo"})


def atom_fit_flags(a: AtomRecord, t: Target) -> set[str]:
    """RV-20: ``other_region`` when the item is about another specific country than the topic's place;
    ``other_entity`` when the topic is anchored on a named entity and the item names one that shares no word with it."""
    flags: set[str] = set()
    place = t.place.country_code if t.place else None
    if place and a.about_region not in ("global", "unknown", place.lower()):
        flags.add("other_region")
    if t.entity and a.about_entity and not (stem_set(t.entity) & stem_set(a.about_entity)):
        flags.add("other_entity")
    return flags


def evidence_of(
    *,
    verdicts: list[str],
    flags: set[str],
    classes: list[str],
    independent: int,
    independent_non_low: int,
) -> str:
    """The evidence ladder (doc 13 9.4): ``official`` > ``corroborated`` > ``single_source`` > ``unverified``."""
    if "supported" not in verdicts or flags & {"other_region", "other_entity", "unchecked"}:
        return "unverified"
    if "number_widened" in flags and independent < 2:
        return "unverified"
    if any(c in ("official", "help") for c in classes):
        return "official"
    if independent_non_low >= 2:
        return "corroborated"
    return "single_source"


def confidence_of(
    item_flags: set[str],
    independent: int,
    classes: list[str],
    impact: str,
    verdicts: list[str],
    cat: Catalog,
) -> str:
    best = min((CLASS_RANK.get(c, 9) for c in classes), default=9)
    authoritative = best <= 1
    if (
        "partial" in verdicts
        or "limited_evidence" in item_flags
        or (impact == "high" and independent < 2 and not authoritative)
    ):
        return "low"
    if (independent >= 2 and best <= 3) or (authoritative and "supported" in verdicts):
        return "high"
    return "medium"


def build_items(ctx: RunContext, index: BriefIndex) -> list[BuiltItem]:
    cat = ctx.catalog
    lanes = cat.lanes
    atoms = [a for ts in ctx.targets.values() for a in ts.atoms if a.status == "verified"]
    targets: dict[str, Target] = {tid: ts.target for tid, ts in ctx.targets.items()}
    pages_by_id = ctx.pages_by_id
    page_urls = sorted({pages_by_id[a.page_id].url for a in atoms})
    src_ids = {u: f"S-{i:03d}" for i, u in enumerate(page_urls, start=1)}
    keys = [(lanes.kinds[a.kind].area, a.norm_key) for a in atoms]
    mapping = reconcile(keys)
    groups: dict[tuple[str, ...], list[AtomRecord]] = {}
    for a in atoms:
        groups.setdefault((a.job, *_group_key(a, mapping, lanes.kinds[a.kind].area)), []).append(a)
    job_order = {j: i for i, j in enumerate(JOB_NAMES)}
    built: list[BuiltItem] = []
    for gkey in sorted(groups, key=lambda g: (job_order.get(g[0], 99), g[1], g[2:])):
        members = sorted(
            groups[gkey],
            key=lambda a: (
                -_richness(a),
                CLASS_RANK.get(pages_by_id[a.page_id].source_class, 9),
                _sha(a.quote),
                a.page_id,
            ),
        )
        t = targets[members[0].target_id]
        fit = {m.id: atom_fit_flags(m, t) for m in members}
        clean = [m for m in members if not fit[m.id]]
        excluded = not clean  # every source is about another region or entity
        src_members = clean or members  # flagged sources never corroborate; when ALL are flagged the item stays, unverified
        primary = src_members[0]
        kind = primary.kind
        area = lanes.kinds[kind].area
        pages = []
        windows: list[tuple[str, str]] = []
        seen_pages: set[str] = set()
        for a in src_members:
            p = pages_by_id[a.page_id]
            if p.url not in seen_pages:
                seen_pages.add(p.url)
                pages.append(p)
                windows.append((p.url, passage_window(p, a.quote)))
        order = {p.url: i for i, p in enumerate(sorted(pages, key=lambda p: (CLASS_RANK.get(p.source_class, 9), p.url)))}
        pages.sort(key=lambda p: order[p.url])
        windows.sort(key=lambda w: order[w[0]])
        data = merge_payloads(primary.data, [m.data for m in src_members[1:]], kind)
        # RV-19: independence by CONTENT. Two domains giving the same paragraph are one source; so are two pages of
        # one organisation. The passage window around the quote is what is compared.
        independent = independent_count([u for u, _ in windows], texts=[w for _, w in windows])
        orgs = len({org_key(p.url) or p.url for p in pages})
        non_low = [(u, w) for (u, w), p in zip(windows, pages, strict=True) if p.source_class not in LOW_CLASSES]
        independent_non_low = independent_count([u for u, _ in non_low], texts=[w for _, w in non_low])
        impact = "high" if any(a.impact == "high" for a in members) else "normal"
        dates = [p.published for p in pages] + [a.published_on for a in src_members]
        newest = max((d for d in dates if d), default=None)
        age = recency_of(dates, ctx.as_of).age_months
        flags: set[str] = set()
        flags |= {f for f in t.flags if f in ("assumption_derived", "geo_uncertain")}
        if excluded:
            flags |= set().union(*fit.values())
        if independent < orgs:
            flags.add("duplicate_source")
        if all("number_widened" in m.flags for m in src_members):
            flags.add("number_widened")
        if any(a.seed for a in members):
            flags.add("entity_from_model")
        if kind in lanes.legal_adjacent_kinds:
            flags.add("legal-adjacent")
        if impact == "high" and (
            independent < 2 or not any(can_support_high_impact(p.source_class, cat) for p in pages)
        ):  # type: ignore[arg-type]
            flags.add("single-source")
        fee = is_fee_text(f"{primary.claim} {data.get('fees') or ''}", cat.prevalence)
        if is_stale(age, job=primary.job, is_fee=fee, cfg=cat.prevalence):
            flags.add("stale")
        verdicts = [a.verdict for a in src_members if a.verdict]
        if not any(a.checked for a in src_members):
            flags.add("unchecked")
        elif impact == "high" and ctx.checker_vendor == "same":
            flags.add("checker_same_vendor")
        if verdicts and "supported" not in verdicts:
            flags.add("partial_support")
        classes = [p.source_class for p in pages]
        evidence = evidence_of(
            verdicts=verdicts,
            flags=flags,
            classes=classes,
            independent=independent,
            independent_non_low=independent_non_low,
        )
        conf = confidence_of(
            flags,
            independent,
            classes,
            impact,
            [v for v in verdicts if v],
            cat,
        )
        tier = (
            "open"
            if flags
            & {
                "single-source",
                "entity_from_model",
                "conflict",
                "unchecked",
                "other_region",
                "other_entity",
                "partial_support",
            }
            or evidence == "unverified"
            else "default"
        )
        item = Item(
            id="",
            job=primary.job,
            spec_need=cat.jobs[primary.job].spec_need,
            kind=kind,
            label=primary.label,
            claim=primary.claim,
            use_note="",
            slot=_slot_of(t, kind),
            impact=impact,
            confidence=conf,  # type: ignore[arg-type]
            tier=tier,  # type: ignore[arg-type]
            flags=sorted(flags),  # type: ignore[arg-type]
            applies_to=_applies(
                t.links,
                kind,
                primary.norm_key,
                ctx.input,
                area if area in ("feature", "entity", "flow") else None,
                index,
            ),
            sources=[_source_ref(p, src_ids) for p in pages],
            independent_domains=independent,
            evidence=evidence,  # type: ignore[arg-type]
            source_language=primary_page_language(pages, primary, pages_by_id),
            about_region=primary.about_region,
            recency=Recency(
                newest_source=newest,
                age_months=age,
                trend="rising"
                if (kind == "trend" and data.get("direction") == "rising")
                else "unknown",
            ),
            request_ids=sorted({r for m in members for r in targets[m.target_id].links.requests}),
            target_id=primary.target_id,
            data=data,
        )
        ba = (
            (area, mapping.get((area, primary.norm_key), primary.norm_key))
            if area in cat.lanes.norm_areas
            else None
        )
        built.append(
            BuiltItem(
                item=item,
                atoms=members,
                group=gkey,
                areas_key=ba,
                pages=pages,
                target_ids={m.target_id for m in members},
                windows=windows,
                excluded=excluded,
            )
        )
    for i, b in enumerate(built, start=1):
        b.item = b.item.model_copy(update={"id": f"RI-{i:03d}"})
    return built


def primary_page_language(
    pages: list[PageRecord], primary: AtomRecord, pages_by_id: dict[str, PageRecord]
) -> str | None:
    """The language of the page the item's main facts came from (a hint for Spec; quotes stay in this language)."""
    p = pages_by_id.get(primary.page_id)
    return (p.language if p is not None else None) or next((q.language for q in pages if q.language), None)


def _slot_of(t: Target, kind: str) -> str | None:
    for s in t.slots:
        if s.kind == kind:
            return s.key
    return t.slots[0].key if t.slots else None


def stem_overlap(a: str, b: str) -> bool:
    return bool(stem_set(a) & stem_set(b))


def newest_date(item: Item) -> date | None:
    return item.recency.newest_source if item.recency else None
