"""Run metrics: the acceptance targets of the design as code, computed from ``research.json`` and its evidence.

Used by the live scripts (the report tables), by the tests, and by anyone judging a run: quote check rate, high-impact
corroboration, required-slot fill, how many searches contributed a used item, norms backed by three or more sources, how
recent the design, feature and service items are, and the counts by kind, job and flag.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from mde_research_agent.core.quotes import normalize_with_offsets, verify_quote

RECENCY_JOBS = ("design", "features", "services")
RECENT_MONTHS = 24
MIN_NORM_SOURCES = 3


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def run_metrics(research: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    items = research.get("items", [])
    pages = {p["id"]: p for p in evidence.get("pages", [])}
    norm_cache: dict[str, Any] = {}
    verified = [
        a for a in evidence.get("atoms", []) if a.get("status") == "verified" and a.get("item_id")
    ]
    ok = 0
    for a in verified:
        page = pages.get(a["page_id"])
        if page is None:
            continue
        norm = norm_cache.setdefault(page["id"], normalize_with_offsets(page["text"]))
        ok += 1 if verify_quote(norm, a["quote"]).ok else 0
    high = [i for i in items if i.get("impact") == "high"]
    high_ok = [
        i
        for i in high
        if i.get("independent_domains", 0) >= 2 or "single-source" in i.get("flags", [])
    ]
    cov = research.get("coverage", {})
    used_urls = {pages[a["page_id"]]["url"] for a in verified if a["page_id"] in pages}
    queries = evidence.get("queries", [])
    contributing = [q for q in queries if q.get("results") and used_urls & set(q.get("urls", []))]
    norms = research.get("norms", [])
    norms3 = [n for n in norms if n["prevalence"]["seen_in"] >= MIN_NORM_SOURCES]
    recency_items = [i for i in items if i["job"] in RECENCY_JOBS]
    dated = [i for i in recency_items if (i.get("recency") or {}).get("age_months") is not None]
    recent = [i for i in dated if i["recency"]["age_months"] <= RECENT_MONTHS]
    return {
        "items": len(items),
        "norms": len(norms),
        "quote_check_pass": _ratio(ok, len(verified)),
        "high_impact_items": len(high),
        "high_impact_corroborated_or_flagged": _ratio(len(high_ok), len(high)),
        "slots_required": cov.get("slots_required", 0),
        "slots_filled": cov.get("slots_filled", 0),
        "slots_filled_ratio": _ratio(cov.get("slots_filled", 0), cov.get("slots_required", 0)),
        "targets": cov.get("targets", 0),
        "targets_covered": cov.get("covered", 0),
        "targets_not_found": cov.get("not_found", 0),
        "searches": len(queries),
        "searches_contributing": len(contributing),
        "searches_contributing_ratio": _ratio(len(contributing), len(queries)),
        "norms_with_3_plus_sources_ratio": _ratio(len(norms3), len(norms)),
        "recency_items": len(recency_items),
        "recency_items_dated": len(dated),
        "recent_24m_ratio_of_dated": _ratio(len(recent), len(dated)),
        "recent_24m_ratio_of_all": _ratio(len(recent), len(recency_items)),
        "by_kind": dict(Counter(i["kind"] for i in items)),
        "by_job": dict(Counter(i["job"] for i in items)),
        "flags": dict(Counter(f for i in items for f in i.get("flags", []))),
        "confidence": dict(Counter(i.get("confidence") for i in items)),
        "source_classes": dict(
            Counter(s["source_class"] for i in items for s in i.get("sources", []))
        ),
        "failed_slots": [
            {
                "target": t["id"],
                "label": t["label"],
                "status": t["status"],
                "filled": t["slots_filled"],
                "required": t["slots_required"],
            }
            for t in evidence.get("targets", [])
            if t.get("slots_filled", 0) < t.get("slots_required", 0)
        ],
        "budget": research.get("budget", {}),
        "status": research.get("status"),
    }
