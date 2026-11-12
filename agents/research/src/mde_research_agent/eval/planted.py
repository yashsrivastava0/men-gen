"""The planted-error test (doc 14 section 5): do our checks catch errors we plant in verified items?

From a committed run, take 40 clean items (verified and judged ``supported`` by the run's own checker, stratified like the
spot-check, by a seed), plant one error of each of eight kinds in each (up to 320 corrupted variants), and run everything
through the checks. The report keeps apart what CODE catches (a number that is not in the quote, an item about another
country or company) and what the CHECKER model catches among what code let through, and how it caught it (``not_supported``
or ``partial``). Pass: at least 90% caught overall, at least 80% per kind, at most 5% of the clean items wrongly rejected,
and a checker that discriminates.

Why the last condition: a checker that answers "partial" to everything catches 100% of the planted errors and rejects
nothing. So the report also gives ``clean_flagged_rate`` (clean items the checker did not call supported),
``discrimination`` (caught rate minus that rate) and ``degenerate_checker``; a degenerate checker fails the test. The
clean sample is drawn from items the run's own checker called supported, so the clean flag rate is the checker disagreeing
with itself on a re-check, a lower bound on its false-partial rate. ``partial_recheck`` re-asks the checker about items it
called partial in the run and says how many it would now call supported (consistency, and the room a tightening step has).
If the catch rate is under 90% a second checker vendor is wanted (OI-23); the gateway has only one vendor today, so the
report then says what to do instead, not that a second vendor exists.
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

PASS = {"overall": 0.90, "per_kind": 0.80, "clean_rejected_max": 0.05, "discrimination_min": 0.30}
RECHECK_N = 20  # items the run called partial that the checker is asked about again
MIN_PER_KIND = (
    8  # a kind with fewer planted variants is reported but not gated: 3 samples cannot show 80%
)
TOPUP_KINDS = (
    "currency_swapped",
    "period_changed",
    "qualifier_dropped",
)  # need an item with a price, a period, a qualifier
TOPUP_N = 12  # variants wanted for each of those kinds


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
        retrieved_on=date(1970, 1, 1),
    )


def _pool(research: dict[str, Any], evidence: dict[str, Any], verdict: str) -> list[dict[str, Any]]:
    """Verified atoms of one earlier verdict; runs recorded without verdicts count as ``supported``."""
    items = {i["id"]: i for i in research.get("items", [])}
    pool = []
    for a in evidence.get("atoms", []):
        if (
            a.get("status") == "verified"
            and (a.get("verdict") or "supported") == verdict
            and a.get("item_id") in items
            and a.get("quote")
        ):
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
    return pool


def clean_atoms(
    research: dict[str, Any], evidence: dict[str, Any], n: int, seed: int
) -> list[dict[str, Any]]:
    """The clean sample: atoms the run's checker called supported, drawn by strata."""
    pool = _pool(research, evidence, "supported")
    by_id = {p["id"]: p for p in pool}
    cards = draw(pool, "planted", seed, n)
    return [by_id[c["item_id"]] for c in cards]


def top_up(
    research: dict[str, Any],
    evidence: dict[str, Any],
    clean: list[dict[str, Any]],
    seed: int,
) -> list[dict[str, Any]]:
    """More clean items for the kinds that need something in the claim (a price, a period, a qualifier).

    The stratified sample holds few of them (one price in 40 items on a yoga run), and a kind with 3 samples cannot
    show a catch rate. Items are taken from the same ``supported`` pool, in the seed's order, until each kind has
    ``TOPUP_N`` candidates or the run has no more.
    """
    have = {a["id"] for a in clean}
    pool = sorted(_pool(research, evidence, "supported"), key=lambda a: _hash_key(seed, a["id"]))
    extra: list[dict[str, Any]] = []
    for kind in TOPUP_KINDS:
        n = sum(1 for a in [*clean, *extra] if corrupt(a, kind, seed) is not None)
        for a in pool:
            if n >= TOPUP_N:
                break
            if a["id"] not in have and corrupt(a, kind, seed) is not None:
                extra.append(a)
                have.add(a["id"])
                n += 1
    return extra


def _hash_key(seed: int, atom_id: str) -> str:
    import hashlib

    return hashlib.sha256(f"{seed}:{atom_id}".encode()).hexdigest()


def partial_atoms(
    research: dict[str, Any], evidence: dict[str, Any], n: int, seed: int
) -> list[dict[str, Any]]:
    """Atoms the run's checker called partial (the same checker is asked again)."""
    pool = _pool(research, evidence, "partial")
    by_id = {p["id"]: p for p in pool}
    cards = draw(pool, "partial", seed, n)
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


def _check_item(
    n_: int, a: dict[str, Any], pages: dict[str, PageRecord], *, legacy: bool = False
) -> check_call.CheckItem:
    page = pages.get(a.get("page_id", ""))
    region = a.get("about_region") or ""
    return check_call.CheckItem(
        about_region="" if legacy or region == "unknown" else region,
        about_entity="" if legacy else (a.get("about_entity") or ""),
        id=f"E{n_}",
        claim=a["claim"],
        quote=a["quote"],
        data=json.dumps(a.get("data") or {}, ensure_ascii=False, separators=(",", ":"))[:2500],
        passage=passage_window(page, a["quote"]) if page else a["quote"],
        page_title=page.title if page else "",
        page_domain=page.domain if page else "",
    )


async def _ask_checker(
    runner: Any,
    pending: list[dict[str, Any]],
    pages: dict[str, PageRecord],
    *,
    legacy: bool = False,
) -> tuple[dict[str, check_call.CheckVerdict], int]:
    """The checker's verdict for each atom (by atom id), in batches of ``CHECK_BATCH``, and what it cost."""
    verdicts: dict[str, check_call.CheckVerdict] = {}
    cost = 0
    for i in range(0, len(pending), CHECK_BATCH):
        batch = pending[i : i + CHECK_BATCH]
        items = [_check_item(i + n_, a, pages, legacy=legacy) for n_, a in enumerate(batch)]
        res = await runner.run(
            check_call.SPEC_V2 if legacy else check_call.SPEC,
            check_call.CheckIn(items=items, legacy=legacy),
        )
        cost += res.cost_micros
        for it, a in zip(items, batch, strict=True):
            for v in res.output.verdicts:
                if v.id == it.id:
                    verdicts[a["id"]] = v
    return verdicts, cost


def flagged(v: check_call.CheckVerdict | None, a: dict[str, Any]) -> bool:
    """The checker did not simply accept the item: not supported, partial, or it emptied a field."""
    if v is None:
        return False
    applied = (
        null_paths(a.get("data") or {}, v.unsupported_fields)[1] if v.unsupported_fields else []
    )
    return v.verdict != "supported" or bool(applied)


def score(
    clean: list[dict[str, Any]],
    variants: list[dict[str, Any]],
    caught_by_code: dict[str, set[str]],
    verdicts: dict[str, check_call.CheckVerdict],
    recheck: list[dict[str, Any]],
) -> dict[str, Any]:
    """Every number of the report, from the cases and the checker's verdicts (pure, so it is testable on its own)."""
    per: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        mine = [v for v in variants if v["planted"] == kind]
        coded = caught_by_code.get(kind, set())
        by_checker = [v for v in mine if v["id"] not in coded and flagged(verdicts.get(v["id"]), v)]
        as_rejected = sum(1 for v in by_checker if verdicts[v["id"]].verdict == "not_supported")
        per[kind] = {
            "planted": len(mine),
            "caught_by_code": len(coded),
            "caught_by_checker": len(by_checker),
            "checker_not_supported": as_rejected,
            "checker_partial": len(by_checker) - as_rejected,
            "missed": len(mine) - len(coded) - len(by_checker),
            "gated": len(mine) >= MIN_PER_KIND,
            "caught_rate": round((len(coded) + len(by_checker)) / len(mine), 4) if mine else None,
        }
    planted = sum(p["planted"] for p in per.values())
    caught = sum(p["caught_by_code"] + p["caught_by_checker"] for p in per.values())
    overall = round(caught / planted, 4) if planted else None
    rejected_clean = sum(
        1 for a in clean if verdicts.get(a["id"]) and verdicts[a["id"]].verdict == "not_supported"
    )
    flagged_clean = sum(1 for a in clean if flagged(verdicts.get(a["id"]), a))
    rejected_rate = round(rejected_clean / len(clean), 4) if clean else None
    flagged_rate = round(flagged_clean / len(clean), 4) if clean else None
    discrimination = (
        round(overall - flagged_rate, 4)
        if overall is not None and flagged_rate is not None
        else None
    )
    degenerate = bool(
        discrimination is not None
        and ((flagged_rate or 0) >= 0.9 or discrimination < PASS["discrimination_min"])
    )
    again = [verdicts.get(a["id"]) for a in recheck]
    ok = bool(
        overall is not None
        and overall >= PASS["overall"]
        and all(
            p["caught_rate"] is None
            or p["planted"] < MIN_PER_KIND
            or p["caught_rate"] >= PASS["per_kind"]
            for p in per.values()
        )
        and (rejected_rate or 0) <= PASS["clean_rejected_max"]
        and not degenerate
    )
    return {
        "clean_items": len(clean),
        "planted": planted,
        "caught_overall": overall,
        "per_kind": per,
        "clean_wrongly_rejected": rejected_clean,
        "clean_rejected_rate": rejected_rate,
        "clean_flagged": flagged_clean,
        "clean_flagged_rate": flagged_rate,
        "discrimination": discrimination,
        "degenerate_checker": degenerate,
        "partial_recheck": {
            "items": len(recheck),
            "still_partial": sum(1 for v in again if v is not None and v.verdict == "partial"),
            "now_supported": sum(1 for v in again if v is not None and v.verdict == "supported"),
            "now_not_supported": sum(
                1 for v in again if v is not None and v.verdict == "not_supported"
            ),
        },
        "passed": ok,
        "needs_second_vendor": bool(overall is not None and overall < PASS["overall"]),
        "thresholds": PASS,
    }


async def run_planted(
    runner: Any,
    research: dict[str, Any],
    evidence: dict[str, Any],
    *,
    n: int = 40,
    seed: int = 1,
    checker: str = "v3",
) -> dict[str, Any]:
    """Run the whole test through ``runner`` (a ``CallRunner``); returns the report as a dict."""
    pages = {p["id"]: _page(p) for p in evidence.get("pages", [])}
    targets = {t["id"]: t for t in evidence.get("targets", [])}
    clean = clean_atoms(research, evidence, n, seed)
    clean = [*clean, *top_up(research, evidence, clean, seed)]
    recheck = partial_atoms(research, evidence, RECHECK_N, seed)
    variants: list[dict[str, Any]] = []
    for a in clean:
        for kind in KINDS:
            place = (targets.get(a.get("target_id", "")) or {}).get("place")
            c = corrupt(a, kind, seed, avoid_region=place)
            if c is not None:
                variants.append(c)
    caught_by_code: dict[str, set[str]] = {}
    pending: list[dict[str, Any]] = [*clean, *recheck]
    for v in variants:
        if code_catches(v, targets.get(v.get("target_id", ""))):
            caught_by_code.setdefault(v["planted"], set()).add(v["id"])
        else:
            pending.append(v)
    verdicts, cost = await _ask_checker(runner, pending, pages, legacy=checker == "v2")
    report = score(clean, variants, caught_by_code, verdicts, recheck)
    report["checker"] = checker
    report["cost_micros"] = cost
    return report


def estimate(n: int = 40) -> dict[str, Any]:
    """The expected size of the test before it runs: every kind through the checker, plus the re-check sample."""
    items = n * (len(KINDS) + 1) + RECHECK_N + len(TOPUP_KINDS) * TOPUP_N * 2
    return {
        "clean_items": n,
        "variants_max": n * len(KINDS),
        "checker_batches_max": -(-items // CHECK_BATCH),
        "usd_estimate": "0.03 to 0.12",
    }
