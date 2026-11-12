"""The readable report: ``render_report(research, evidence, rejected) -> str``, deterministic Markdown.

Written for a person who judges the research: what ran and what it cost, how trustworthy each lane is, what to confirm,
what conflicts, what is missing and why, then EVERY item (label, claim, evidence badge, flags, sources with domain, date
and link, the best quote from the evidence, the data in readable form, what it applies to, and a mark for items the owner
rejected). The appendix follows, then the validation summary, a query log summary and the fallbacks used. Rendered on
demand and never stored. The same inputs always give the same text (no clock, no randomness).
"""

from __future__ import annotations

import json
from typing import Any

from mde_research_agent.catalog.loader import JOB_NAMES
from mde_research_agent.review import build_index

JOB_TITLE = {
    "features": "Features and comparable apps",
    "structure": "Structure: screens, navigation and roles",
    "flows": "Journeys",
    "data": "Data, lifecycles and rules",
    "services": "Providers and integrations",
    "design": "Design conventions",
    "screens": "Screen detail",
    "local": "Local formats and sample data",
    "trust": "Trust: privacy, security and store rules",
}
BADGE = {
    "official": "OFFICIAL",
    "corroborated": "CORROBORATED",
    "single_source": "SINGLE SOURCE",
    "unverified": "UNVERIFIED",
}
LEVEL = {
    "core": "core",
    "common": "common",
    "advanced": "advanced",
    "emerging": "emerging",
    "limited_evidence": "limited evidence",
}


def _usd(micros: int) -> str:
    return f"USD {micros / 1_000_000:.2f}"


def _credit_line(evidence: dict[str, Any]) -> list[str]:
    """One header line with the provider credits the run was billed (kept in the run's evidence file, not in
    ``research.json``). No line when the run recorded none, so older runs read exactly as before."""
    credits = evidence.get("credits")
    if not isinstance(credits, dict) or not credits:
        return []
    parts = [
        f"{name.title()} {n}"
        for name, n in sorted(credits.items())
        if isinstance(n, int) and not isinstance(n, bool)
    ]
    return [f"- Search credits billed: {', '.join(parts)}"] if parts else []


def _data_lines(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k, v in data.items():
        if v in (None, "", [], {}):
            continue
        if isinstance(v, list):
            shown = "; ".join(
                json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x) for x in v[:12]
            )
            more = f" (+{len(v) - 12} more)" if len(v) > 12 else ""
            out.append(f"{k}: {shown}{more}")
        elif isinstance(v, dict):
            out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            out.append(f"{k}: {v}")
    return out


def _item_block(it: dict[str, Any], rejected: set[str]) -> list[str]:
    badge = BADGE.get(it.get("evidence", ""), "UNVERIFIED")
    flags = ", ".join(it.get("flags", []))
    withdrawn = it["id"] in rejected
    title = f"~~{it['label']}~~ (rejected by you)" if withdrawn else it["label"]
    lines = [
        f"#### {title} `{it['id']}`",
        f"**{badge}** · {it['kind']} · impact {it['impact']} · confidence {it['confidence']}"
        + (f" · flags: {flags}" if flags else "")
        + (f" · appendix ({it.get('appendix_reason')})" if it.get("store") == "appendix" else ""),
        "",
        it["claim"],
    ]
    q = it.get("best_quote")
    if q:
        lines += [
            "",
            f"> {q['text']}",
            f"> {q.get('domain') or ''}" + (f", {q['published']}" if q.get("published") else ""),
        ]
    if it.get("sources"):
        lines += [""]
        for s in it["sources"][:6]:
            when = f", {s['published']}" if s.get("published") else ""
            lines.append(f"- Source `{s['id']}`: {s['domain']} ({s['class']}{when}) {s['url']}")
        if len(it["sources"]) > 6:
            lines.append(f"- (+{len(it['sources']) - 6} more sources)")
    prev = it.get("prevalence")
    if prev:
        lines.append(
            f"- Seen in {prev['seen_in']} of {prev['of']} ({LEVEL.get(prev['level'], prev['level'])})"
        )
    for d in _data_lines(it.get("data", {})):
        lines.append(f"- {d}")
    if it.get("applies_to"):
        lines.append(
            "- Applies to: "
            + ", ".join(
                f"{a['kind']}{(':' + a['id']) if a.get('id') else ''} ({a['relation']})"
                for a in it["applies_to"]
            )
        )
    if it.get("use_note"):
        lines.append(f"- Use: {it['use_note']}")
    lines.append("")
    return lines


def render_report(
    research: dict[str, Any],
    evidence: dict[str, Any],
    rejected: set[str] | None = None,
    lanes: dict[str, Any] | None = None,
) -> str:
    rejected = rejected or set()
    prof = research.get("profile", {})
    bud = research.get("budget", {})
    cov = research.get("coverage", {})
    run = research.get("run", {})
    idx = build_index(research, evidence, lanes or {})
    out: list[str] = []
    out += [f"# Research report ({research.get('schema')})", ""]
    out += [
        f"- Status: **{research.get('status')}**",
        f"- Size: {prof.get('load_class')} (load {prof.get('load_score')})"
        + (" · light mode" if prof.get("light") else ""),
        f"- Signals: {', '.join(prof.get('signals', [])) or 'none'}",
        f"- Work: {bud.get('searches_used', 0)} searches, {bud.get('pages_read', 0)} pages, {bud.get('seconds', 0)} s, {_usd(bud.get('usd_micros', 0))}",
        *_credit_line(evidence),
        f"- Topics: {cov.get('covered', 0)} covered, {cov.get('not_found', 0)} not found, of {cov.get('targets', 0)}; "
        f"required slots {cov.get('slots_filled', 0)} of {cov.get('slots_required', 0)}",
        f"- Items: {len(research.get('items', []))} (main store about {prof.get('store_tokens', 0)} tokens, appendix {prof.get('appendix_items', 0)})",
        f"- Provider: {run.get('search_provider') or 'n/a'}; models: {run.get('writer_model') or 'n/a'} / checker {run.get('checker_model') or 'n/a'} ({run.get('checker_vendor')})",
        "",
    ]
    out += [
        "## Lane quality",
        "",
        "| Lane | Quality | Required slots filled | Items | Strong share |",
        "| --- | --- | --- | --- | --- |",
    ]
    for lane, q in sorted(research.get("lane_quality", {}).items()):
        out.append(
            f"| {lane} | {q['quality']} | {q['slots_filled']} of {q['slots_required']} | {q['items']} | {round(q['strong_share'] * 100)}% |"
        )
    out += ["", "## Builder-need coverage", "", "| Need | Coverage |", "| --- | --- |"]
    for need, v in sorted(research.get("bn_coverage", {}).items()):
        out.append(f"| {need} | {v} |")
    out += ["", "## To confirm", ""]
    by_id = {i["id"]: i for i in idx.items}
    for c in research.get("confirm", []):
        it = by_id.get(c["item_id"])
        out.append(f"- `{c['item_id']}` {it['label'] if it else ''}: {c['why']}")
    if not research.get("confirm"):
        out.append("- Nothing needs confirming.")
    out += ["", "## Conflicts", ""]
    for c in research.get("conflicts", []):
        out.append(f"- {c['topic']}: {c['note']} (items {', '.join(c['item_ids'])})")
    if not research.get("conflicts"):
        out.append("- No conflicts.")
    out += ["", "## Gaps", ""]
    for g in research.get("gaps", []):
        out.append(
            f"- {g.get('label') or g['target_id']}: {g['reason']}"
            + (f" (searched: {'; '.join(g['queries'][:3])})" if g.get("queries") else "")
        )
    if not research.get("gaps"):
        out.append("- No gaps.")
    main = [i for i in idx.items if i.get("store", "main") == "main"]
    for job in (*JOB_NAMES, *sorted({i["job"] for i in main} - set(JOB_NAMES))):
        group = [i for i in main if i["job"] == job]
        if not group:
            continue
        out += ["", f"## {JOB_TITLE.get(job, job)}", ""]
        for it in sorted(group, key=lambda i: (i.get("rank") or 10**9, i["id"])):
            out += _item_block(it, rejected)
    appendix = [i for i in idx.items if i.get("store") == "appendix"]
    if appendix:
        out += [
            "",
            "## Appendix",
            "",
            "These findings did not fit the main store. Nothing was deleted.",
            "",
        ]
        for it in sorted(appendix, key=lambda i: (i.get("rank") or 10**9, i["id"])):
            out += _item_block(it, rejected)
    val = research.get("validation", {})
    out += [
        "",
        "## Validation",
        "",
        f"- Errors: {val.get('errors', 0)}; warnings: {val.get('warnings', 0)}",
    ]
    for rule, n in sorted(val.get("by_rule", {}).items()):
        out.append(f"  - {rule}: {n}")
    out += ["", "## Dropped and flagged", ""]
    for k, v in sorted(research.get("dropped", {}).items()):
        out.append(f"- dropped {k}: {v}")
    for k, v in sorted(research.get("flagged", {}).items()):
        out.append(f"- flagged {k}: {v}")
    out += ["", "## Queries", ""]
    per: dict[str, list[str]] = {}
    for q in evidence.get("queries", []):
        per.setdefault(q["target_id"], []).append(q["text"])
    labels = {t["id"]: t["label"] for t in evidence.get("targets", [])}
    for tid in sorted(per):
        out.append(f"- {labels.get(tid, tid)}: {len(per[tid])} queries, e.g. {per[tid][0]}")
    out += ["", "## Fallbacks used", ""]
    fb = research.get("run", {}).get("fallbacks", {})
    if fb:
        for cid, n in sorted(fb.items()):
            out.append(f"- {cid}: {n} (a deterministic path; nothing was invented)")
    else:
        out.append("- None.")
    out.append("")
    return "\n".join(out)
