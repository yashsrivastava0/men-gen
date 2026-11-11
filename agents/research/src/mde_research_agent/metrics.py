"""Run metrics: the measures of doc 14 section 3 as code, computed from ``research.json`` and its evidence.

These are the numbers a person (or the front end's Quality tab) reads to judge a run. The old quote metric re-checked items
that had already passed, so it was always 100%: it is replaced by what can actually fail:

* **extraction quote pass rate, first try and final**: over EVERY item the model returned (malformed and empty ones
  included); the first-try rate is what the model got right unaided, the final rate counts the one "quote exactly" retry;
* **number-check rejections**: items rejected because a number was not in the quote;
* **fetch success by reason**: pages read over pages selected, with the reasons of the skipped ones;
* **searches contributing**: searches that returned a page behind a kept item, over all searches;
* **required slots filled**, per job and in total; **appendix share**; **lane quality**;
* **checker rates**: supported, partial, not supported, unchecked; a rate under 1% rejections is a rubber-stamp signal;
* **merge rate**; **stage timings and cost by step**; **fallbacks used**.

Every ratio is ``None`` when its denominator is zero. Targets (doc 14) are reported next to the values as ``acceptance``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

RECENCY_JOBS = ("design", "features", "services")
RECENT_MONTHS = 24
MIN_NORM_SOURCES = 3
TARGETS = {
    "extraction_quote_pass_first_try": 0.90,
    "number_rejection_rate_max": 0.03,
    "fetch_success": 0.70,
    "searches_contributing": 0.60,
    "slots_filled": 0.70,
    "checker_not_supported_max": 0.10,
    "norms_with_3_plus_sources": 0.70,
    "recent_24m_of_dated": 0.70,
}


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _fetch(evidence: dict[str, Any]) -> dict[str, Any]:
    events = evidence.get("events", [])
    read = sum(1 for e in events if e.get("type") == "page.read")
    skipped = Counter(
        (e.get("detail") or {}).get("reason", "other")
        for e in events
        if e.get("type") == "page.skipped"
    )
    if not events:  # a run without its event log: the pages kept are the pages read
        read = len(evidence.get("pages", []))
    selected = read + sum(skipped.values())
    return {
        "selected": selected,
        "read": read,
        "success": _ratio(read, selected),
        "skipped_by_reason": dict(sorted(skipped.items())),
    }


def _checker(atoms: list[dict[str, Any]]) -> dict[str, Any]:
    checked = [a for a in atoms if a.get("verdict")]
    unchecked = [a for a in atoms if not a.get("verdict") and a.get("status") == "verified"]
    total = len(checked) + len(unchecked)
    verdicts = Counter(a["verdict"] for a in checked)
    return {
        "items_checked": total,
        "supported": _ratio(verdicts["supported"], total),
        "partial": _ratio(verdicts["partial"], total),
        "not_supported": _ratio(verdicts["not_supported"], total),
        "unchecked": _ratio(len(unchecked), total),
        "rubber_stamp_signal": bool(total >= 20 and verdicts["not_supported"] / total < 0.01),
    }


def run_metrics(research: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    items = research.get("items", [])
    pages = {p["id"]: p for p in evidence.get("pages", [])}
    atoms = evidence.get("atoms", [])
    verified = [a for a in atoms if a.get("status") == "verified" and a.get("item_id")]
    ext = evidence.get("extraction", {})
    extracted = ext.get("extracted", 0)
    first_ok = ext.get("first_try_ok", 0)
    final_ok = first_ok + ext.get("recovered_by_retry", 0)
    dropped = research.get("dropped", {})
    high = [i for i in items if i.get("impact") == "high"]
    high_ok = [
        i
        for i in high
        if i.get("evidence") in ("official", "corroborated")
        or i.get("independent_domains", 0) >= 2
        or "single-source" in i.get("flags", [])
    ]
    cov = research.get("coverage", {})
    used_urls = {pages[a["page_id"]]["url"] for a in verified if a["page_id"] in pages}
    used_urls |= {pages[a["page_id"]].get("final_url") for a in verified if a["page_id"] in pages}
    queries = evidence.get("queries", [])
    contributing = [q for q in queries if q.get("results") and used_urls & set(q.get("urls", []))]
    norms = research.get("norms", [])
    norms3 = [n for n in norms if n["prevalence"]["seen_in"] >= MIN_NORM_SOURCES]
    recency_items = [i for i in items if i["job"] in RECENCY_JOBS]
    dated = [i for i in recency_items if (i.get("recency") or {}).get("age_months") is not None]
    recent = [i for i in dated if i["recency"]["age_months"] <= RECENT_MONTHS]
    by_job: dict[str, list[int]] = {}
    for t in evidence.get("targets", []):
        row = by_job.setdefault(t["job"], [0, 0])
        row[0] += t.get("slots_required", 0)
        row[1] += t.get("slots_filled", 0)
    appendix = [i for i in items if i.get("store") == "appendix"]
    run = research.get("run", {})
    out: dict[str, Any] = {
        "items": len(items),
        "norms": len(norms),
        "extraction": {
            "extracted": extracted,
            "first_try_ok": first_ok,
            "final_ok": final_ok,
            "quote_pass_first_try": _ratio(first_ok, extracted),
            "quote_pass_final": _ratio(final_ok, extracted),
            "quote_failed_first": ext.get("quote_failed_first", 0),
            "recovered_by_retry": ext.get("recovered_by_retry", 0),
            "number_not_in_quote": dropped.get("number_not_in_quote", 0),
            "number_rejection_rate": _ratio(dropped.get("number_not_in_quote", 0), extracted),
            "invalid": dropped.get("invalid", 0),
            "claim_too_long": dropped.get("claim_too_long", 0),
            "forbidden_pattern": dropped.get("forbidden_pattern", 0),
        },
        "fetch": _fetch(evidence),
        "checker": _checker(atoms),
        "merge": {
            "atoms_per_item": _ratio(len(verified), len(items)) if items else None,
        },
        "appendix_items": len(appendix),
        "appendix_share": _ratio(len(appendix), len(items)),
        "lane_quality": {k: v.get("quality") for k, v in research.get("lane_quality", {}).items()},
        "slots_by_job": {
            j: {"required": r, "filled": f, "ratio": _ratio(f, r)}
            for j, (r, f) in sorted(by_job.items())
        },
        "stages": run.get("stages", {}),
        "cost_by_step": run.get("cost_by_step", {}),
        "fallbacks": run.get("fallbacks", {}),
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
        "evidence": dict(Counter(i.get("evidence") for i in items)),
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
    # kept for the batch scripts and their reports: the final quote pass rate over every extraction
    out["quote_check_pass"] = out["extraction"]["quote_pass_final"]
    out["acceptance"] = acceptance(out)
    return out


def acceptance(m: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each measure next to its target (doc 14): ``ok`` is None when the run has nothing to measure."""

    def row(value: float | None, target: float, *, maximum: bool = False) -> dict[str, Any]:
        ok = None if value is None else (value <= target if maximum else value >= target)
        return {"value": value, "target": target, "kind": "max" if maximum else "min", "ok": ok}

    return {
        "extraction_quote_pass_first_try": row(
            m["extraction"]["quote_pass_first_try"], TARGETS["extraction_quote_pass_first_try"]
        ),
        "number_rejection_rate": row(
            m["extraction"]["number_rejection_rate"],
            TARGETS["number_rejection_rate_max"],
            maximum=True,
        ),
        "fetch_success": row(m["fetch"]["success"], TARGETS["fetch_success"]),
        "searches_contributing": row(
            m["searches_contributing_ratio"], TARGETS["searches_contributing"]
        ),
        "slots_filled": row(m["slots_filled_ratio"], TARGETS["slots_filled"]),
        "checker_not_supported": row(
            m["checker"]["not_supported"], TARGETS["checker_not_supported_max"], maximum=True
        ),
        "norms_with_3_plus_sources": row(
            m["norms_with_3_plus_sources_ratio"], TARGETS["norms_with_3_plus_sources"]
        ),
        "recent_24m_of_dated": row(m["recent_24m_ratio_of_dated"], TARGETS["recent_24m_of_dated"]),
    }
