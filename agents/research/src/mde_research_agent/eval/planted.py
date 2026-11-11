"""The planted-error test (doc 14 section 5): do our checks catch errors we plant in verified items?

From a committed run, take 40 clean verified items (stratified like the spot-check, by a seed), plant one error of each of
five kinds in each (up to 200 corrupted variants), and run everything through the checks. The report keeps apart what CODE
catches (a number that is not in the quote, an item about another country or company) and what the CHECKER model catches
among what code let through. Pass: at least 90% caught overall, at least 80% per kind, and at most 5% of the clean items
wrongly rejected. If the catch rate is under 90% a second checker vendor is needed (OI-23).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from mde_research_agent.calls import check as check_call
from mde_research_agent.core.numbers import claim_figures, figures, missing_figures
from mde_research_agent.core.text import stem_set
from mde_research_agent.engine.checking import CHECK_BATCH, null_paths, passage_window
from mde_research_agent.engine.state import PageRecord
from mde_research_agent.eval.corrupt import KINDS, corrupt
from mde_research_agent.spotcheck import draw

PASS = {"overall": 0.90, "per_kind": 0.80, "clean_rejected_max": 0.05}


def _page(p: dict[str, Any]) -> PageRecord:
    return PageRecord(
        id=p["id"],
        url=p["url"],
        final_url=p.get("final_url") or p["url"],
        domain=p["domain"],
        source_class=p["source_class"],
        title=p.get("title", ""),
        text=p["text"],
        content_hash=p.get("content_hash", ""),
        text_source=p.get("text_source", ""),
        published=None,
        retrieved_on=date.today(),
    )


def clean_atoms(
    research: dict[str, Any], evidence: dict[str, Any], n: int, seed: int
) -> list[dict[str, Any]]:
    """The sample: verified atoms (claim, quote, data, region, entity, target) drawn by strata."""
    items = {i["id"]: i for i in research.get("items", [])}
    pool = []
    for a in evidence.get("atoms", []):
        if a.get("status") == "verified" and a.get("item_id") in items and a.get("quote"):
            it = items[a["item_id"]]
            pool.append(
                {
                    **a,
                    "job": a["job"],
                    "kind": a["kind"],
                    "impact": a.get("impact", "normal"),
                    "best_quote": {"text": a["quote"]},
                    "label": a["label"],
                    "id": a["id"],
                    "item": it["id"],
                }
            )
    by_id = {p["id"]: p for p in pool}
    cards = draw(pool, "planted", seed, n)
    return [by_id[c["item_id"]] for c in cards]


def code_catches(a: dict[str, Any], target: dict[str, Any] | None) -> list[str]:
    """What the code-level rules say about an item: RV-18 (numbers), RV-20 (region and entity fit)."""
    why: list[str] = []
    if missing_figures(
        claim_figures(a["claim"], a.get("label", ""), a.get("data") or {}), figures(a["quote"])
    ):
        why.append("number_not_in_quote")
    if target:
        place = (target.get("place") or "").lower()
        if place and (a.get("about_region") or "unknown") not in ("global", "unknown", place):
            why.append("other_region")
        if (
            target.get("entity")
            and a.get("about_entity")
            and not (stem_set(target["entity"]) & stem_set(a["about_entity"]))
        ):
            why.append("other_entity")
    return why


async def run_planted(
    runner: Any, research: dict[str, Any], evidence: dict[str, Any], *, n: int = 40, seed: int = 1
) -> dict[str, Any]:
    """Run the whole test through ``runner`` (a ``CallRunner``); returns the report as a dict."""
    pages = {p["id"]: _page(p) for p in evidence.get("pages", [])}
    targets = {t["id"]: t for t in evidence.get("targets", [])}
    clean = clean_atoms(research, evidence, n, seed)
    variants: list[dict[str, Any]] = []
    for a in clean:
        for kind in KINDS:
            c = corrupt(a, kind, seed)
            if c is not None:
                variants.append(c)
    cases = [("clean", a) for a in clean] + [(v["planted"], v) for v in variants]
    caught_by_code: dict[str, set[str]] = {}
    pending: list[tuple[str, dict[str, Any]]] = []
    for kind, a in cases:
        if kind != "clean" and code_catches(a, targets.get(a.get("target_id", ""))):
            caught_by_code.setdefault(kind, set()).add(a["id"])
        else:
            pending.append((kind, a))
    verdicts: dict[str, check_call.CheckVerdict] = {}
    cost = 0
    for i in range(0, len(pending), CHECK_BATCH):
        batch = pending[i : i + CHECK_BATCH]
        items = [
            check_call.CheckItem(
                id=f"E{i + n_}",
                claim=a["claim"],
                quote=a["quote"],
                data=json.dumps(a.get("data") or {}, ensure_ascii=False, separators=(",", ":"))[
                    :2500
                ],
                passage=passage_window(pages[a["page_id"]], a["quote"])
                if a.get("page_id") in pages
                else a["quote"],
                page_title=pages[a["page_id"]].title if a.get("page_id") in pages else "",
                page_domain=pages[a["page_id"]].domain if a.get("page_id") in pages else "",
            )
            for n_, (_k, a) in enumerate(batch)
        ]
        res = await runner.run(check_call.SPEC, check_call.CheckIn(items=items))
        cost += res.cost_micros
        for it, (_k, a) in zip(items, batch, strict=True):
            for v in res.output.verdicts:
                if v.id == it.id:
                    verdicts[a["id"]] = v

    def flagged(v: check_call.CheckVerdict | None, a: dict[str, Any]) -> bool:
        if v is None:
            return False
        applied = (
            null_paths(a.get("data") or {}, v.unsupported_fields)[1] if v.unsupported_fields else []
        )
        return v.verdict != "supported" or bool(applied)

    per: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        mine = [v for v in variants if v["planted"] == kind]
        by_code = len(caught_by_code.get(kind, set()))
        by_checker = sum(
            1
            for v in mine
            if v["id"] not in caught_by_code.get(kind, set()) and flagged(verdicts.get(v["id"]), v)
        )
        per[kind] = {
            "planted": len(mine),
            "caught_by_code": by_code,
            "caught_by_checker": by_checker,
            "missed": len(mine) - by_code - by_checker,
            "caught_rate": round((by_code + by_checker) / len(mine), 4) if mine else None,
        }
    planted = sum(p["planted"] for p in per.values())
    caught = sum(p["caught_by_code"] + p["caught_by_checker"] for p in per.values())
    rejected_clean = sum(
        1 for a in clean if verdicts.get(a["id"]) and verdicts[a["id"]].verdict == "not_supported"
    )
    overall = round(caught / planted, 4) if planted else None
    rejected_rate = round(rejected_clean / len(clean), 4) if clean else None
    ok = bool(
        overall is not None
        and overall >= PASS["overall"]
        and all(
            p["caught_rate"] is None or p["caught_rate"] >= PASS["per_kind"] for p in per.values()
        )
        and (rejected_rate or 0) <= PASS["clean_rejected_max"]
    )
    return {
        "clean_items": len(clean),
        "planted": planted,
        "caught_overall": overall,
        "per_kind": per,
        "clean_wrongly_rejected": rejected_clean,
        "clean_rejected_rate": rejected_rate,
        "passed": ok,
        "needs_second_vendor": bool(overall is not None and overall < PASS["overall"]),
        "cost_micros": cost,
        "thresholds": PASS,
    }


def estimate(n: int = 40) -> dict[str, Any]:
    """The expected size of the test before it runs: about 5 variants per clean item through the checker."""
    items = n * 6
    return {
        "clean_items": n,
        "variants_max": n * len(KINDS),
        "checker_batches_max": -(-items // CHECK_BATCH),
        "usd_estimate": "0.02 to 0.10",
    }
