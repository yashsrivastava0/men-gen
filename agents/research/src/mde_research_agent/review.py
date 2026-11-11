"""Reading a committed run: the paged and filtered item list, the sources table and the overview (no item list).

Pure functions over the committed ``research.json`` and its evidence file. The owner's rejections are an overlay and are
passed in (a set of item ids): the committed file never changes. Each item in the review carries the BEST QUOTE from the
evidence (the quote of its best source), the lane it feeds and its sources, so a person can judge it without opening any
file.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

CLASS_RANK = {"official": 0, "help": 1, "press": 2, "store": 3, "blog": 4, "forum": 5, "seo": 6}
MAX_LIMIT = 200


@dataclass
class ReviewIndex:
    """Everything a review query needs, built once per committed run (a committed run never changes)."""

    items: list[dict[str, Any]]  # review items (item + lane, job, best_quote, sources, search text)
    pages: list[dict[str, Any]]  # evidence pages without their text
    used: Counter[str]  # source id -> items that use it


def _lane_of(kind: str, lanes: dict[str, Any]) -> str:
    k = lanes.get(kind)
    return (k.lanes[0] if k and k.lanes else "blueprint") if hasattr(k, "lanes") else "blueprint"


def build_index(
    research: dict[str, Any], evidence: dict[str, Any], lanes: dict[str, Any]
) -> ReviewIndex:
    pages_by_final = {p.get("final_url") or p["url"]: p for p in evidence.get("pages", [])}
    pages_by_url = {p["url"]: p for p in evidence.get("pages", [])}
    by_item: dict[str, list[dict[str, Any]]] = {}
    for a in evidence.get("atoms", []):
        if a.get("item_id") and a.get("status") == "verified":
            by_item.setdefault(a["item_id"], []).append(a)
    page_by_id = {p["id"]: p for p in evidence.get("pages", [])}
    used: Counter[str] = Counter()
    out: list[dict[str, Any]] = []
    for it in research.get("items", []):
        sources = [
            {
                "id": s["id"],
                "domain": s["domain"],
                "url": s["url"],
                "title": (pages_by_final.get(s["url"]) or pages_by_url.get(s["url"]) or {}).get(
                    "title", ""
                ),
                "published": s.get("published_on"),
                "class": s["source_class"],
            }
            for s in it.get("sources", [])
        ]
        for s in sources:
            used[s["id"]] += 1
        best: dict[str, Any] | None = None
        atoms = sorted(
            by_item.get(it["id"], []),
            key=lambda a: (
                CLASS_RANK.get((page_by_id.get(a["page_id"]) or {}).get("source_class", ""), 9),
                a["id"],
            ),
        )
        if atoms:
            a = atoms[0]
            page = page_by_id.get(a["page_id"]) or {}
            src = next(
                (s for s in sources if s["url"] in (page.get("final_url"), page.get("url"))), None
            )
            best = {
                "text": a["quote"],
                "source_id": src["id"] if src else None,
                "domain": page.get("domain"),
                "url": (src or {}).get("url") or page.get("final_url"),
                "published": page.get("published"),
            }
        lane = _lane_of(it["kind"], lanes)
        text = " ".join(
            [
                it.get("label", ""),
                it.get("claim", ""),
                (best or {}).get("text", ""),
                it.get("kind", ""),
            ]
        ).casefold()
        out.append({**it, "lane": lane, "best_quote": best, "sources": sources, "_text": text})
    pages = [{k: v for k, v in p.items() if k != "text"} for p in evidence.get("pages", [])]
    return ReviewIndex(out, pages, used)


def _matches(it: dict[str, Any], f: dict[str, str]) -> bool:
    if f.get("job") and it["job"] != f["job"]:
        return False
    if f.get("lane") and it["lane"] != f["lane"]:
        return False
    if f.get("evidence") and it.get("evidence") != f["evidence"]:
        return False
    if f.get("flag") and f["flag"] not in it.get("flags", []):
        return False
    if f.get("impact") and it.get("impact") != f["impact"]:
        return False
    if f.get("ids") and it["id"] not in set(f["ids"].split(",")):
        return False
    if f.get("kind") and it["kind"] != f["kind"]:
        return False
    if f.get("store") and it.get("store", "main") != f["store"]:
        return False
    return not (f.get("q") and f["q"].casefold() not in it["_text"])


def review_page(
    index: ReviewIndex,
    rejected: set[str],
    *,
    limit: int = 50,
    offset: int = 0,
    **filters: str,
) -> dict[str, Any]:
    """One page of items (best rank first), with facet counts over the whole filtered set."""
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    hits = [i for i in index.items if _matches(i, filters)]
    hits.sort(key=lambda i: (i.get("rank") or 10**9, i["id"]))
    facets: dict[str, dict[str, int]] = {
        k: {} for k in ("job", "lane", "evidence", "flag", "impact", "kind", "store")
    }

    def bump(name: str, value: str | None) -> None:
        if value:
            facets[name][value] = facets[name].get(value, 0) + 1

    for i in hits:
        bump("job", i["job"])
        bump("lane", i["lane"])
        bump("evidence", i.get("evidence"))
        bump("impact", i.get("impact"))
        bump("kind", i["kind"])
        bump("store", i.get("store", "main"))
        for f in i.get("flags", []):
            bump("flag", f)
    page = [
        {**{k: v for k, v in i.items() if k != "_text"}, "rejected": i["id"] in rejected}
        for i in hits[offset : offset + limit]
    ]
    return {"total": len(hits), "offset": offset, "limit": limit, "items": page, "facets": facets}


def sources_page(
    index: ReviewIndex, *, limit: int = 100, offset: int = 0, q: str = ""
) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_LIMIT))
    rows = []
    for p in index.pages:
        sid = next(
            (
                s["id"]
                for i in index.items
                for s in i["sources"]
                if s["url"] in (p.get("final_url"), p["url"])
            ),
            None,
        )
        rows.append(
            {
                "id": sid or p["id"],
                "url": p.get("final_url") or p["url"],
                "domain": p["domain"],
                "title": p.get("title", ""),
                "class": p["source_class"],
                "published": p.get("published"),
                "used_items": index.used.get(sid or "", 0),
            }
        )
    if q:
        needle = q.casefold()
        rows = [r for r in rows if needle in f"{r['domain']} {r['title']} {r['url']}".casefold()]
    rows.sort(key=lambda r: (-r["used_items"], r["domain"], r["url"]))
    return {
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "items": rows[offset : offset + limit],
    }


def overview(
    research: dict[str, Any], evidence: dict[str, Any] | None, rejected: set[str]
) -> dict[str, Any]:
    """Everything except the item list (and the page texts): what the results header and the other tabs show."""
    body = {k: v for k, v in research.items() if k != "items"}
    return {
        "run_id": (research.get("run") or {}).get("run_id"),
        "schema": research.get("schema"),
        **{
            k: body.get(k)
            for k in (
                "status",
                "program_id",
                "profile",
                "coverage",
                "budget",
                "input_quality",
                "lane_quality",
                "bn_coverage",
                "validation",
                "norms",
                "suggestions",
                "confirm",
                "gaps",
                "conflicts",
                "defaults_proposed",
                "slices",
                "dropped",
                "flagged",
            )
        },
        "run": body.get("run"),
        "item_count": len(research.get("items", [])),
        "rejected_ids": sorted(rejected),
        "fallbacks": (research.get("run") or {}).get("fallbacks", {}),
        "evidence_validation": (evidence or {}).get("validation", []),
    }
