"""The main store and the appendix (AD-55): nothing is deleted to fit a size.

Every item gets a ``rank`` (1 is best), ``tokens_est`` (characters over 4 of its COMPACT view) and ``store``: ``main``
(what Spec reads, up to ``store_tokens``) or ``appendix`` (everything else, with a reason). The rules, in order:

* **Protected, always main:** an item linked to a Brief id (not ``NEW``) with ``impact: high`` or of kind ``rule``,
  ``compliance_note`` or ``service_option``; items flagged ``conflict`` or ``needs-confirm``; the best two of every kind.
  When the protected set alone is over the limit it all stays and the caller warns.
* **The rest in rank order, best first:** Brief-linked, high impact, evidence (official, corroborated, single_source,
  unverified), the norm level as a tie-break only, job priority, then id. The first one that would pass the limit and
  everything after it go to the appendix.

``compact_view`` is the deterministic projection Spec reads (and ``GET .../latest?view=compact`` serves): main-store items,
norms, gaps, confirm, suggestions and defaults, with sources in ONE table and ``use_note`` only when it differs from the
kind's default. It is not a second stored file. Pure functions, no I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from mde_research_agent.catalog.loader import JOB_NAMES

PROTECTED_KINDS = frozenset({"rule", "compliance_note", "service_option"})
EVIDENCE_RANK = {"official": 0, "corroborated": 1, "single_source": 2, "unverified": 3}
LEVEL_RANK = {"core": 0, "common": 1, "emerging": 2, "advanced": 3, "limited_evidence": 4}
APPENDIX_REASON = "over_store_limit"
_JOB_ORDER = {j: i for i, j in enumerate(JOB_NAMES)}


def tokens_of(obj: Any) -> int:
    """The token estimate used everywhere: characters over 4 of the compact JSON."""
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)) // 4


def brief_linked(item: dict[str, Any]) -> bool:
    return any(a.get("id") and a.get("kind") != "NEW" for a in item.get("applies_to", []))


def is_protected(item: dict[str, Any]) -> bool:
    flags = set(item.get("flags", []))
    return (
        brief_linked(item) and (item.get("impact") == "high" or item.get("kind") in PROTECTED_KINDS)
    ) or bool(flags & {"conflict", "needs-confirm"})


def rank_key(item: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
    level = (item.get("prevalence") or {}).get("level", "limited_evidence")
    return (
        0 if brief_linked(item) else 1,
        0 if item.get("impact") == "high" else 1,
        EVIDENCE_RANK.get(item.get("evidence", "unverified"), 3),
        LEVEL_RANK.get(
            level, 5
        ),  # a tie-break only: niche, Brief-linked rules are limited evidence by nature
        _JOB_ORDER.get(item.get("job", ""), 99),
        str(item.get("id", "")),
    )


def compact_item(item: dict[str, Any], default_notes: dict[str, str]) -> dict[str, Any]:
    """One item in the compact view: sources by id, empty fields dropped, ``use_note`` only when it is not the default."""
    out: dict[str, Any] = {
        "id": item["id"],
        "job": item.get("job"),
        "kind": item.get("kind"),
        "label": item.get("label"),
        "claim": item.get("claim"),
        "impact": item.get("impact"),
        "evidence": item.get("evidence"),
        "tier": item.get("tier"),
        "confidence": item.get("confidence"),
    }
    note = item.get("use_note") or ""
    if note and note != default_notes.get(item.get("kind", ""), ""):
        out["use_note"] = note
    if item.get("flags"):
        out["flags"] = item["flags"]
    if item.get("applies_to"):
        out["applies_to"] = [
            f"{a['kind']}:{a['id']}:{a['relation']}"
            if a.get("id")
            else f"{a['kind']}::{a['relation']}"
            for a in item["applies_to"]
        ]
    if item.get("sources"):
        out["sources"] = [s["id"] for s in item["sources"]]
    if item.get("independent_domains"):
        out["independent"] = item["independent_domains"]
    prev = item.get("prevalence")
    if prev:
        out["prevalence"] = f"{prev['seen_in']}/{prev['of']} {prev['level']}"
    rec = item.get("recency") or {}
    if rec.get("newest_source"):
        out["newest_source"] = rec["newest_source"]
    if item.get("about_region") not in (None, "unknown"):
        out["region"] = item["about_region"]
    data = {k: v for k, v in (item.get("data") or {}).items() if v not in (None, "", [], {})}
    if data:
        out["data"] = data
    return out


@dataclass(frozen=True)
class StoreDecision:
    rank: int
    tokens_est: int
    store: str  # main | appendix
    appendix_reason: str | None = None


@dataclass(frozen=True)
class StorePlan:
    decisions: dict[str, StoreDecision]
    main_tokens: int  # overhead plus the main items
    protected_over_limit: (
        bool  # the protected set alone exceeded the limit: nothing was moved, the store grows
    )

    @property
    def appendix_items(self) -> int:
        return sum(1 for d in self.decisions.values() if d.store == "appendix")


def assign_store(
    items: list[dict[str, Any]],
    *,
    overhead_tokens: int,
    store_tokens: int,
    default_notes: dict[str, str],
    note_allowance: int = 0,
) -> StorePlan:
    """Rank every item and decide main or appendix. ``items`` are dumped items (dicts); the result is deterministic."""
    ordered = sorted(items, key=rank_key)
    cost = {i["id"]: tokens_of(compact_item(i, default_notes)) + note_allowance for i in ordered}
    rank = {i["id"]: n for n, i in enumerate(ordered, start=1)}
    protected = {i["id"] for i in ordered if is_protected(i)}
    best: dict[str, int] = {}
    for i in ordered:  # the best two of every kind always stay
        k = str(i.get("kind"))
        if best.get(k, 0) < 2:
            best[k] = best.get(k, 0) + 1
            protected.add(i["id"])
    main: set[str] = set(protected)
    used = overhead_tokens + sum(cost[i] for i in main)
    over = used > store_tokens
    if not over:
        for i in ordered:
            if i["id"] in main:
                continue
            if used + cost[i["id"]] > store_tokens:
                break
            main.add(i["id"])
            used += cost[i["id"]]
    decisions = {
        i["id"]: StoreDecision(
            rank[i["id"]],
            cost[i["id"]] - note_allowance,
            "main" if i["id"] in main else "appendix",
            None if i["id"] in main else APPENDIX_REASON,
        )
        for i in ordered
    }
    return StorePlan(decisions, used, over)


def _present(value: Any) -> Any:
    """``value`` without null, empty text, empty list and empty object entries (a nested walk), for the compact view."""
    if isinstance(value, dict):
        out = {k: _present(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_present(v) for v in value]
    return value


def compact_dossier(d: dict[str, Any]) -> dict[str, Any]:
    """A dossier in the compact view: empty parts are dropped, but a field that is empty keeps its ``null_reason`` (an
    explicit unknown, R27) and a conflict keeps every value."""
    out = _present({k: v for k, v in d.items() if k != "fields"})
    out["fields"] = {
        key: _present(f)
        if f.get("value") is not None or f.get("values")
        else {"null_reason": f.get("null_reason")}
        for key, f in d.get("fields", {}).items()
    }
    return out


def compact_view(research: dict[str, Any], default_notes: dict[str, str]) -> dict[str, Any]:
    """The deterministic compact projection of a ``research.json`` (main-store items only, sources in one table)."""
    items = [i for i in research.get("items", []) if i.get("store", "main") == "main"]
    used = {s["id"]: s for i in items for s in i.get("sources", [])}
    sources = {
        sid: {
            "domain": s.get("domain"),
            "class": s.get("source_class"),
            "url": s.get("url"),
            "published": s.get("published_on"),
        }
        for sid, s in sorted(used.items())
    }
    profile = dict(research.get("profile", {}))
    return {
        "schema": research.get("schema"),
        "status": research.get("status"),
        "program_id": research.get("program_id"),
        "brief_hash": research.get("brief_hash"),
        "profile": profile,
        "lane_quality": research.get("lane_quality", {}),
        "bn_coverage": research.get("bn_coverage", {}),
        "sources": sources,
        "items": [compact_item(i, default_notes) for i in items],
        "norms": research.get("norms", []),
        "suggestions": research.get("suggestions", {}),
        "defaults_proposed": research.get("defaults_proposed", []),
        "confirm": research.get("confirm", []),
        "conflicts": research.get("conflicts", []),
        "gaps": research.get("gaps", []),
        "dossiers": [compact_dossier(d) for d in research.get("dossiers", [])],
        "dossiers_by_integration": research.get("dossiers_by_integration", {}),
        "platform_constraints": research.get("platform_constraints", []),
        "compliance_register": research.get("compliance_register", []),
        "lane_decisions": research.get("lane_decisions", []),
        "appendix_items": sum(1 for i in research.get("items", []) if i.get("store") == "appendix"),
    }
