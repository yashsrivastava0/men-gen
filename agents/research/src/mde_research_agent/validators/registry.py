"""Validators RV-01..RV-23 over a finished run. Each returns violations; none raises.

What each does when it fails is decided by the caller (``commit_gate``): item-level problems are repaired or the
item dropped BEFORE commit (the assembler already enforces most of them, so a violation here is a bug signal);
RV-04 (a Locked tier) and RV-13 (hash binding) refuse the commit outright.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import jsonschema

from mde_research_agent.assemble.items import LOW_CLASSES, evidence_of
from mde_research_agent.assemble.notes import openers
from mde_research_agent.assemble.quality import TargetInfo, bn_coverage, lane_quality
from mde_research_agent.assemble.quality import lane_items as lane_items_of
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.anatomy import check_query, hygiene_problems
from mde_research_agent.core.applies import BriefIndex
from mde_research_agent.core.applies import problem as applies_problem
from mde_research_agent.core.novelty import novel_tokens
from mde_research_agent.core.numbers import claim_figures, figures, missing_figures
from mde_research_agent.core.quotes import normalize_with_offsets, verify_quote
from mde_research_agent.core.slots import evaluate_target
from mde_research_agent.core.sources import can_support_high_impact, independent_count
from mde_research_agent.core.text import stem_set, tokens
from mde_research_agent.domain.atoms import validate_payload
from mde_research_agent.domain.events import DETAIL_ALLOWLIST, Event
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.plan import Plan
from mde_research_agent.domain.research import AppliesTo, Item
from mde_research_agent.engine.checking import null_paths, passage_window
from mde_research_agent.engine.state import PageRecord
from mde_research_agent.schemas import research_v5_schema

MAX_NOTE_WORDS = 15
Severity = str  # "error" | "warn"


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    item_id: str | None = None
    severity: Severity = "error"


@dataclass
class ValidationContext:
    output: dict[str, Any]  # the dumped research.json
    evidence: dict[str, Any]
    inp: ResearchInput
    catalog: Catalog
    plan: Plan | None = None
    events: list[Event] = field(default_factory=list)
    ceilings_hard: dict[str, int] = field(default_factory=dict)
    store_tokens: int = 0
    chunk_items: int = 0
    digest_tokens: int = 0
    _pages: dict[str, dict[str, Any]] | None = None

    @property
    def pages(self) -> dict[str, dict[str, Any]]:
        if self._pages is None:
            self._pages = {p["id"]: p for p in self.evidence.get("pages", [])}
        return self._pages


Validator = Callable[[ValidationContext], list[Violation]]


def rv01_schema(v: ValidationContext) -> list[Violation]:
    out: list[Violation] = []
    errors = list(jsonschema.Draft202012Validator(research_v5_schema()).iter_errors(v.output))
    out += [
        Violation("RV-01", f"schema: {e.message[:160]} at {'/'.join(map(str, e.absolute_path))}")
        for e in errors[:5]
    ]
    for item in v.output.get("items", []):
        for problem in validate_payload(item.get("kind", ""), item.get("data", {})):
            out.append(
                Violation("RV-01", f"data does not match kind: {problem[:120]}", item.get("id"))
            )
    return out


def rv02_quotes(v: ValidationContext) -> list[Violation]:
    out = []
    norm_cache: dict[str, Any] = {}
    for a in v.evidence.get("atoms", []):
        if a.get("status") != "verified":
            continue
        page = v.pages.get(a["page_id"])
        if page is None:
            out.append(Violation("RV-02", "quote has no stored page", a.get("item_id")))
            continue
        norm = norm_cache.setdefault(page["id"], normalize_with_offsets(page["text"]))
        if not verify_quote(norm, a["quote"]).ok:
            out.append(
                Violation(
                    "RV-02", f"quote not found on the stored page {page['url']}", a.get("item_id")
                )
            )
    return out


def rv03_sources(v: ValidationContext) -> list[Violation]:
    out = []
    urls = {p["final_url"] for p in v.pages.values()} | {p["url"] for p in v.pages.values()}
    by_url = {p["final_url"]: p for p in v.pages.values()} | {p["url"]: p for p in v.pages.values()}
    for item in v.output.get("items", []):
        for s in item.get("sources", []):
            if s["url"] not in urls:
                out.append(
                    Violation("RV-03", f"source {s['url']} is not in the fetch log", item["id"])
                )
                continue
            page = by_url[s["url"]]
            if s.get("retrieved_on") != page.get("retrieved_on"):
                out.append(
                    Violation("RV-03", "retrieved date differs from the fetch log", item["id"])
                )
            if s.get("published_on") != page.get("published"):
                out.append(
                    Violation("RV-03", "published date differs from the fetch log", item["id"])
                )
    return out


def rv04_tier(v: ValidationContext) -> list[Violation]:
    return [
        Violation("RV-04", f"tier {i.get('tier')!r} is not default or open", i.get("id"))
        for i in v.output.get("items", [])
        if i.get("tier") not in ("default", "open")
    ]


def _substantive(tok: str) -> bool:
    """A figure that could be a fact (an amount, a percentage, a decimal, a long number or a date), not a small count
    the digest's own code computed ("+4 more")."""
    digits = sum(ch.isdigit() for ch in tok)
    return digits >= 3 or (digits >= 1 and any(c in tok for c in "%.$₹€£"))


def rv05_no_new_facts(v: ValidationContext) -> list[Violation]:
    out = []
    items = {i["id"]: i for i in v.output.get("items", [])}
    for i in items.values():
        note = i.get("use_note", "")
        if not note:
            continue
        if len(note.split()) > MAX_NOTE_WORDS:
            out.append(Violation("RV-05", "use_note is longer than 15 words", i["id"]))
        new = novel_tokens(
            note,
            [
                i.get("label", ""),
                i.get("claim", ""),
                json.dumps(i.get("data", {}), ensure_ascii=False),
            ],
            openers(),
        )
        if new:
            out.append(Violation("RV-05", f"use_note adds {new[:3]}", i["id"]))
    for lane, sl in v.output.get("slices", {}).items():
        corpus = [
            json.dumps(items[i], ensure_ascii=False) for i in sl.get("item_ids", []) if i in items
        ]
        if lane == "verify":
            corpus = [json.dumps(i, ensure_ascii=False) for i in items.values()] + [
                json.dumps(v.output.get(k, []), ensure_ascii=False)
                for k in ("gaps", "confirm", "defaults_proposed", "norms")
            ]
        for tok in novel_tokens(
            sl.get("digest", ""),
            [*corpus, json.dumps(v.output.get("norms", []), ensure_ascii=False)],
        ):
            if _substantive(tok) or tok.startswith(("http", "www")):
                out.append(Violation("RV-05", f"{lane} digest adds {tok!r}", None, "warn"))
                break
    return out


def rv06_query_hygiene(v: ValidationContext) -> list[Violation]:
    out = []
    brand = [set(tokens(b)) for b in v.inp.own_brand_terms]
    for q in v.evidence.get("queries", []):
        for p in hygiene_problems(q["text"]):
            out.append(Violation("RV-06", f"query {q['id']}: {p}"))
        toks = set(tokens(q["text"]))
        if toks and any(b and toks <= b for b in brand):
            out.append(Violation("RV-06", f"query {q['id']} is only the own brand"))
    return out


def rv07_corroboration(v: ValidationContext) -> list[Violation]:
    out = []
    for i in v.output.get("items", []):
        flags = set(i.get("flags", []))
        if (
            i.get("impact") == "high"
            and i.get("independent_domains", 0) < 2
            and "single-source" not in flags
        ):
            out.append(
                Violation(
                    "RV-07",
                    "high-impact item with one independent domain is not flagged single-source",
                    i["id"],
                )
            )
        classes = [s["source_class"] for s in i.get("sources", [])]
        if (
            i.get("impact") == "high"
            and classes
            and not any(can_support_high_impact(c, v.catalog) for c in classes)
            and "single-source" not in flags
        ):  # type: ignore[arg-type]
            out.append(
                Violation(
                    "RV-07", "high-impact item rests on forum, blog or SEO pages only", i["id"]
                )
            )
    return out


def rv08_support(v: ValidationContext) -> list[Violation]:
    out = []
    verdict = {a["item_id"]: a for a in v.evidence.get("atoms", []) if a.get("item_id")}
    for a in v.evidence.get("atoms", []):
        if a.get("item_id") and a.get("verdict") == "not_supported":
            out.append(Violation("RV-08", "a not_supported item was committed", a["item_id"]))
    for i in v.output.get("items", []):
        a = verdict.get(i["id"])
        if a and a.get("verdict") == "partial" and i.get("confidence") == "high":
            out.append(Violation("RV-08", "partial support but confidence high", i["id"], "warn"))
    return out


def rv09_caps(v: ValidationContext) -> list[Violation]:
    """The three caps (AD-55). Enforced: the digests stay inside ``digest_tokens`` and no lane chunk is longer than
    ``chunk_items``. (The store limit itself is RV-22.)"""
    out = []
    for lane, sl in v.output.get("slices", {}).items():
        if v.digest_tokens and len(sl.get("digest", "")) > v.digest_tokens * 4 * 1.2:
            out.append(Violation("RV-09", f"{lane} digest is over its cap"))
        if v.chunk_items:
            for n, chunk in enumerate(sl.get("chunks", [])):
                if len(chunk) > v.chunk_items:
                    out.append(
                        Violation(
                            "RV-09",
                            f"{lane} chunk {n} has {len(chunk)} items, over {v.chunk_items}",
                        )
                    )
        ids = sl.get("item_ids", [])
        if ids != [i for chunk in sl.get("chunks", []) for i in chunk]:
            out.append(Violation("RV-09", f"{lane} chunks do not add up to its item list"))
    return out


def rv10_coverage(v: ValidationContext) -> list[Violation]:
    out = []
    in_items = {i.get("target_id") for i in v.output.get("items", [])}
    in_gaps = {g.get("target_id") for g in v.output.get("gaps", [])}
    for t in v.evidence.get("targets", []):
        if t["status"] != "covered" and t["id"] not in in_items and t["id"] not in in_gaps:
            out.append(Violation("RV-10", f"target {t['id']} appears in neither items nor gaps"))
    seen_requests = {r for i in v.output.get("items", []) for r in i.get("request_ids", [])}
    gap_labels = " ".join(g.get("target_id", "") for g in v.output.get("gaps", []))
    for r in v.inp.requests:
        if r.origin == "template" or r.thin:
            continue
        if (
            r.id not in seen_requests
            and r.id not in gap_labels
            and not any(r.id in (t.get("label") or "") for t in v.evidence.get("targets", []))
        ):
            linked = v.plan is not None and any(
                r.id in t.links.requests for t in (*v.plan.targets, *v.plan.unrun)
            )
            if not linked:
                out.append(
                    Violation(
                        "RV-10", f"request {r.id} appears in neither items nor gaps", None, "warn"
                    )
                )
    return out


def rv11_reserved(v: ValidationContext) -> list[Violation]:
    return []  # personal-data filtering was removed by owner decision (AD-54); nothing runs here


def rv12_budget(v: ValidationContext) -> list[Violation]:
    out = []
    b = v.output.get("budget", {})
    for key, limit in (
        ("searches_used", "searches"),
        ("pages_read", "pages"),
        ("seconds", "seconds"),
    ):
        cap = v.ceilings_hard.get(limit)
        if cap is not None and b.get(key, 0) > cap + (30 if key == "seconds" else 0):
            out.append(Violation("RV-12", f"{key} {b.get(key)} is over the hard ceiling {cap}"))
    return out


def rv13_binding(v: ValidationContext) -> list[Violation]:
    if v.output.get("brief_hash") != v.inp.brief_hash:
        return [Violation("RV-13", "brief_hash does not equal the approved Brief hash")]
    return []


def rv14_anatomy(v: ValidationContext) -> list[Violation]:
    if v.plan is None:
        return []
    out = []
    by_id = {t.id: t for t in (*v.plan.targets, *v.plan.unrun)}
    intent = [w for lang in v.catalog.intent_words.languages.values() for w in lang.words]
    for q in v.evidence.get("queries", []):
        t = by_id.get(q["target_id"])
        if t is None:
            out.append(Violation("RV-14", f"query {q['id']} has no target"))
            continue
        if not t.decision_unlocked:
            out.append(Violation("RV-14", f"target {t.id} has no decision_unlocked"))
        names = [e["name"] for e in v.evidence.get("entities", [])]
        local = (
            [s for s in v.inp.hints.synonyms if not s.isascii()]
            if q.get("variant") == "local"
            else []
        )
        seeds = list(v.plan.discovery_names) if q.get("variant") == "literal" else []
        res = check_query(
            q["text"],
            anchors=[
                *t.anchors,
                *local,
                *seeds,
                *names,
                *([t.entity] if t.entity else []),
            ],
            places=list(t.place.phrases) if t.place else [],
            intent_words=[*intent, *t.intent],
            generic_words=v.catalog.intent_words.generic_words,
            own_brand=v.inp.own_brand_terms,
            require_place=bool(t.place and not t.place.unknown) and not q.get("domain"),
            named=[*names, *seeds, *([t.entity] if t.entity else [])],
        )
        if not res.ok:
            out.append(
                Violation(
                    "RV-14", f"query {q['id']} fails the anatomy gate: {','.join(res.problems)}"
                )
            )
    return out


def rv15_slots(v: ValidationContext) -> list[Violation]:
    if v.plan is None:
        return []
    out = []
    by_target: dict[str, list[Any]] = {}
    from types import SimpleNamespace

    for a in v.evidence.get("atoms", []):
        if a.get("status") == "verified":
            by_target.setdefault(a["target_id"], []).append(
                SimpleNamespace(kind=a["kind"], norm_key=a["norm_key"], data=a.get("data", {}))
            )
    filled = required = 0
    for t in v.plan.targets:
        cov = evaluate_target(t.slots, by_target.get(t.id, []))
        status = next((x["status"] for x in v.evidence.get("targets", []) if x["id"] == t.id), None)
        if status == "covered" and not cov.covered:
            out.append(
                Violation(
                    "RV-15",
                    f"target {t.id} is marked covered but a required slot is not filled",
                    None,
                    "warn",
                )
            )
        filled += cov.required_filled
        required += cov.required_total
    c = v.output.get("coverage", {})
    if c.get("slots_required", required) != required:
        out.append(
            Violation("RV-15", "coverage.slots_required does not match the plan", None, "warn")
        )
    return out


def rv16_applies(v: ValidationContext) -> list[Violation]:
    index = BriefIndex.from_input(v.inp, list(v.catalog.needs.needs))
    out = []
    for i in v.output.get("items", []):
        for a in i.get("applies_to", []):
            msg = applies_problem(AppliesTo.model_validate(a), index)
            if msg:
                out.append(Violation("RV-16", msg, i["id"]))
    return out


def rv17_events(v: ValidationContext) -> list[Violation]:
    out = []
    quotes = [a["quote"] for a in v.evidence.get("atoms", []) if a.get("quote")]
    for e in v.events:
        allowed = DETAIL_ALLOWLIST.get(e.type, frozenset())
        extra = set(e.detail) - allowed
        if extra:
            out.append(
                Violation(
                    "RV-17",
                    f"event {e.seq} {e.type} has fields outside the allowlist: {sorted(extra)}",
                )
            )
        blob = e.label + json.dumps(e.detail, ensure_ascii=False)
        if any(len(q) > 30 and q in blob for q in quotes):
            out.append(Violation("RV-17", f"event {e.seq} carries page text"))
        if not e.label.strip():
            out.append(Violation("RV-17", f"event {e.seq} has no label"))
    return out


# --- v5: grounding, independence, region and entity fit, checks, the store, recomputed trust --------------------------


def _items_by_id(v: ValidationContext) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in v.output.get("items", [])}


def _verified_atoms(v: ValidationContext) -> list[dict[str, Any]]:
    return [
        a for a in v.evidence.get("atoms", []) if a.get("status") == "verified" and a.get("item_id")
    ]


def _target_of(v: ValidationContext) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in v.evidence.get("targets", [])}


def rv18_numbers(v: ValidationContext) -> list[Violation]:
    """Every number, amount, percentage and date in an item's claim, label and numeric data is in its quote."""
    out = []
    for a in _verified_atoms(v):
        needed = claim_figures(a.get("claim", ""), a.get("label", ""), a.get("data") or {})
        missing = missing_figures(needed, figures(a.get("quote", "")))
        if missing:
            out.append(
                Violation("RV-18", f"{missing[0].raw!r} is not in the quote", a.get("item_id"))
            )
    return out


def _fit(a: dict[str, Any], t: dict[str, Any] | None) -> set[str]:
    flags: set[str] = set()
    if t is None:
        return flags
    place = (t.get("place") or "").lower()
    region = a.get("about_region") or "unknown"
    if place and region not in ("global", "unknown", place):
        flags.add("other_region")
    if (
        t.get("entity")
        and a.get("about_entity")
        and not (stem_set(t["entity"]) & stem_set(a["about_entity"]))
    ):
        flags.add("other_entity")
    return flags


def _windows(v: ValidationContext, atoms: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """``(url, source class, passage window)`` per distinct page of ``atoms``, from the stored pages."""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for a in atoms:
        p = v.pages.get(a.get("page_id", ""))
        if p is None or p["url"] in seen:
            continue
        seen.add(p["url"])
        rec = PageRecord(
            id=p["id"],
            url=p["url"],
            final_url=p["final_url"],
            domain=p["domain"],
            source_class=p["source_class"],
            title=p.get("title", ""),
            text=p["text"],
            content_hash=p.get("content_hash", ""),
            text_source=p.get("text_source", ""),
            published=None,
            retrieved_on=date(1970, 1, 1),
        )
        out.append((p["url"], p["source_class"], passage_window(rec, a["quote"])))
    return out


def _item_atoms(v: ValidationContext) -> dict[str, list[dict[str, Any]]]:
    by_item: dict[str, list[dict[str, Any]]] = {}
    for a in _verified_atoms(v):
        by_item.setdefault(a["item_id"], []).append(a)
    return by_item


def _sources_of(v: ValidationContext, atoms: list[dict[str, Any]], tmap: dict[str, dict[str, Any]]):
    clean = [a for a in atoms if not _fit(a, tmap.get(a.get("target_id", "")))]
    return clean or atoms, not clean


def rv19_independence(v: ValidationContext) -> list[Violation]:
    """Independence by content: two domains giving the same paragraph are one source."""
    out = []
    tmap = _target_of(v)
    by_item = _item_atoms(v)
    for i in v.output.get("items", []):
        atoms = by_item.get(i["id"], [])
        if not atoms:
            continue
        src, _ = _sources_of(v, atoms, tmap)
        w = _windows(v, src)
        got = independent_count([u for u, _, _ in w], texts=[x for _, _, x in w]) if w else 0
        if got != i.get("independent_domains"):
            out.append(
                Violation(
                    "RV-19",
                    f"independent_domains is {i.get('independent_domains')}, recomputed {got}",
                    i["id"],
                )
            )
    return out


def rv20_region_entity(v: ValidationContext) -> list[Violation]:
    """An item whose every source is about another country or company is flagged, unverified and not in a norm."""
    out = []
    tmap = _target_of(v)
    in_norm = {iid for n in v.output.get("norms", []) for iid in n.get("item_ids", [])}
    for iid, atoms in _item_atoms(v).items():
        item = _items_by_id(v).get(iid)
        if item is None:
            continue
        flags = [_fit(a, tmap.get(a.get("target_id", ""))) for a in atoms]
        if all(flags):
            need = set().union(*flags)
            if not need <= set(item.get("flags", [])):
                out.append(Violation("RV-20", f"missing flag {sorted(need)}", iid))
            if item.get("evidence") != "unverified":
                out.append(Violation("RV-20", "a flagged item must be unverified", iid))
            if iid in in_norm:
                out.append(Violation("RV-20", "a flagged item must not count in a norm", iid))
    return out


_ELEMENT_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\[\d+\]$")


def rv21_checked(v: ValidationContext) -> list[Violation]:
    """Every verified item has a verdict or the unchecked flag; no unsupported field remains in its data."""
    out = []
    items = _items_by_id(v)
    for a in _verified_atoms(v):
        item = items.get(a["item_id"])
        if item is None:
            continue
        if not a.get("checked") and "unchecked" not in item.get("flags", []):
            out.append(
                Violation(
                    "RV-21", "an item has neither a verdict nor the unchecked flag", item["id"]
                )
            )
        # A removed list element ("steps[0]") shifts the list, so applying the path again would remove the NEXT element and
        # look like a leftover: only paths that are idempotent (a scalar, a list emptied, "field[i].sub") can be checked
        # after the fact. The first live run with many checked items had 10 false RV-21 errors and was marked partial.
        paths = [
            p for p in (a.get("unsupported_fields") or []) if not _ELEMENT_PATH.match(p.strip())
        ]
        if paths:
            rest, _applied = null_paths(a.get("data") or {}, paths)
            if rest != (a.get("data") or {}):
                out.append(
                    Violation(
                        "RV-21", f"unsupported field {paths[0]!r} is still in the data", item["id"]
                    )
                )
    return out


def rv22_store(v: ValidationContext) -> list[Violation]:
    """Main-store tokens fit ``store_tokens`` (unless the protected set alone is over it), every appendix item has a
    reason, and the counts are conserved: verified atoms = items + merged."""
    out = []
    items = v.output.get("items", [])
    for i in items:
        if i.get("store") == "appendix" and not i.get("appendix_reason"):
            out.append(Violation("RV-22", "an appendix item has no reason", i["id"]))
    profile = v.output.get("profile", {})
    store_tokens = profile.get("store_tokens", 0)
    if v.store_tokens and store_tokens > v.store_tokens + 1 and not profile.get("store_over_limit"):
        out.append(
            Violation("RV-22", f"main store is about {store_tokens} tokens, over {v.store_tokens}")
        )
    atoms = _verified_atoms(v)
    if len(atoms) < len(items):
        out.append(Violation("RV-22", f"{len(items)} items from {len(atoms)} verified atoms"))
    return out


def rv23_recomputed(v: ValidationContext) -> list[Violation]:
    """``evidence``, ``lane_quality`` and ``bn_coverage`` recomputed from the items equal the stored values."""
    out = []
    tmap = _target_of(v)
    by_item = _item_atoms(v)
    items = v.output.get("items", [])
    for i in items:
        atoms = by_item.get(i["id"], [])
        if not atoms:
            continue
        src, _ = _sources_of(v, atoms, tmap)
        w = _windows(v, src)
        non_low = [(u, x) for u, c, x in w if c not in LOW_CLASSES]
        got = evidence_of(
            verdicts=[a["verdict"] for a in src if a.get("verdict")],
            flags=set(i.get("flags", [])),
            classes=[c for _, c, _ in w],
            independent=i.get("independent_domains", 0),
            independent_non_low=independent_count(
                [u for u, _ in non_low], texts=[x for _, x in non_low]
            )
            if non_low
            else 0,
        )
        if got != i.get("evidence"):
            out.append(
                Violation(
                    "RV-23", f"evidence is {i.get('evidence')!r}, recomputed {got!r}", i["id"]
                )
            )
    infos = [
        TargetInfo(
            t["job"],
            t.get("lane", ""),
            t["status"],
            t.get("slots_required", 0),
            t.get("slots_filled", 0),
        )
        for t in v.evidence.get("targets", [])
    ]
    models = [Item.model_validate(i) for i in items]
    lane_of = {
        t["id"]: [t.get("lane", ""), *t.get("also_lanes", [])]
        for t in v.evidence.get("targets", [])
    }
    per_lane = lane_items_of(models, v.catalog.lanes, lane_of)
    expect_lq = {
        k: q.model_dump()
        for k, q in lane_quality(per_lane, infos, list(v.catalog.lanes.lanes)).items()
    }
    if expect_lq != v.output.get("lane_quality"):
        out.append(Violation("RV-23", "lane_quality differs from the recomputed value"))
    if bn_coverage(models, infos, v.catalog.lanes) != v.output.get("bn_coverage"):
        out.append(Violation("RV-23", "bn_coverage differs from the recomputed value"))
    return out


REGISTRY: dict[str, Validator] = {
    "RV-01": rv01_schema, "RV-02": rv02_quotes, "RV-03": rv03_sources, "RV-04": rv04_tier, "RV-05": rv05_no_new_facts,
    "RV-06": rv06_query_hygiene, "RV-07": rv07_corroboration, "RV-08": rv08_support, "RV-09": rv09_caps, "RV-10": rv10_coverage,
    "RV-11": rv11_reserved, "RV-12": rv12_budget, "RV-13": rv13_binding, "RV-14": rv14_anatomy, "RV-15": rv15_slots,
    "RV-16": rv16_applies, "RV-17": rv17_events, "RV-18": rv18_numbers, "RV-19": rv19_independence,
    "RV-20": rv20_region_entity, "RV-21": rv21_checked, "RV-22": rv22_store, "RV-23": rv23_recomputed,
}  # fmt: skip

REFUSE_COMMIT = frozenset({"RV-04", "RV-13"})
# A violation of one of these is a bug in the assembler: the commit goes ahead but the run is marked ``partial``.
ENFORCED = frozenset({"RV-05", "RV-09", "RV-18", "RV-19", "RV-20", "RV-21", "RV-22", "RV-23"})


def run_all(v: ValidationContext, only: list[str] | None = None) -> list[Violation]:
    out: list[Violation] = []
    for rid, fn in REGISTRY.items():
        if only and rid not in only:
            continue
        try:
            out += fn(v)
        except (
            Exception
        ) as exc:  # a validator bug must not crash a run; it is reported as a violation
            out.append(
                Violation(
                    rid, f"validator crashed: {type(exc).__name__}: {str(exc)[:100]}", None, "warn"
                )
            )
    return out


def must_refuse(violations: list[Violation]) -> list[Violation]:
    return [x for x in violations if x.rule in REFUSE_COMMIT and x.severity == "error"]
