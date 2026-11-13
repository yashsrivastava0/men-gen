"""Re-extract and re-check the STORED pages of a recorded run (D-159): the experiment harness for claim fit.

A recorded run keeps the full text of every page it read and the items it got from them. This harness asks the extraction
model again about each (topic, page) pair that gave items, runs the same code verification (quote on the page, numbers in
the quote), and then puts the result through each checker "arm" on the SAME extraction, so the arms differ in one thing
only. It needs no search and no fetch: the cost is model calls (about USD 0.02 to 0.04 a repeat on a Small run).

Arms (the checker side; the extraction is shared):

* ``A``  the v2 checker, no tightening: what the recorded runs did.
* ``A3`` the v3 checker (numbered sentences, country and company shown), no tightening.
* ``B``  the v3 checker, then tighten the partials the checker explained and ask once more.

The topics are rebuilt from the run's stored input by the deterministic planner and matched to the recorded topics by
label; a topic the planner does not rebuild is skipped and counted. The report is by arm and by kind, with the share of
items (groups of atoms with one kind and one key) that no atom of which was judged supported: those are the ones
assembly calls ``unverified``. Compare ranges over repeats, never one run against one run.
"""

from __future__ import annotations

import asyncio
import copy
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from mde_research_agent.calls import check as check_call
from mde_research_agent.calls.extract import JOB_KINDS, KIND_GUIDE, ExtractIn
from mde_research_agent.calls.extract import SPECS as EXTRACT_SPECS
from mde_research_agent.catalog import load_catalog
from mde_research_agent.core.norm_keys import clean_key
from mde_research_agent.core.passages import render, select_passages
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.plan import Target
from mde_research_agent.engine.checking import (
    CHECK_BATCH,
    MAX_DATA_CHARS,
    meets_minimum,
    null_paths,
    pair_key,
    passage_window,
    tighten_and_recheck,
)
from mde_research_agent.engine.context import PASSAGE_CHARS
from mde_research_agent.engine.state import AtomRecord, PageRecord
from mde_research_agent.engine.verify import Candidate, candidates_from_output, check_candidate
from mde_research_agent.eval.planted import _page
from mde_research_agent.plan.build import build_plan

LANES = 8  # extraction calls in flight


@dataclass(frozen=True)
class Arm:
    name: str
    spec: Any
    legacy: bool
    tighten: bool


ARMS: dict[str, Arm] = {
    "A": Arm("v2 checker, no tightening (the recorded runs)", check_call.SPEC_V2, True, False),
    "A3": Arm("v3 checker, no tightening", check_call.SPEC, False, False),
    "B": Arm("v3 checker, tighten and re-check once", check_call.SPEC, False, True),
}


def pairs_of(
    evidence: dict[str, Any], targets_by_label: dict[str, Target]
) -> tuple[list[tuple[Target, str]], int]:
    """The (topic, page id) pairs that gave atoms in the recorded run, and how many topics could not be rebuilt."""
    label_of = {t["id"]: t["label"] for t in evidence.get("targets", [])}
    seen: set[tuple[str, str]] = set()
    out: list[tuple[Target, str]] = []
    missing = 0
    for a in evidence.get("atoms", []):
        label = label_of.get(a["target_id"], "")
        tgt = targets_by_label.get(label)
        if tgt is None:
            missing += 1
            continue
        key = (tgt.id, a["page_id"])
        if key not in seen:
            seen.add(key)
            out.append((tgt, a["page_id"]))
    return sorted(out, key=lambda p: (p[0].id, p[1])), missing


async def _extract(
    runner: Any,
    cat: Any,
    load_class: str,
    tgt: Target,
    page: PageRecord,
    sem: asyncio.Semaphore,
) -> list[Candidate]:
    job = cat.jobs[tgt.job]
    passages = select_passages(page.text, tgt.terms, max_chars=PASSAGE_CHARS[load_class])
    kinds = list(JOB_KINDS[tgt.job])
    inp = ExtractIn(
        job=tgt.job,
        question=tgt.question,
        anchor=tgt.anchors[0],
        place=tgt.place.text if tgt.place else "",
        language=page.language or "en",
        focus=job.extract_focus.strip(),
        kinds=kinds,
        want_kinds=kinds,
        kind_guides=[KIND_GUIDE[k] for k in kinds],
        slots=[s.key for s in tgt.slots],
        page_title=page.title,
        page_domain=page.domain,
        page_published=page.published.isoformat() if page.published else None,
        passages=render(passages),
        retry_keys=[],
    )
    async with sem:
        res = await runner.run(EXTRACT_SPECS[tgt.job], inp)
    out: list[Candidate] = []
    for cand in candidates_from_output(res.output, page):
        if isinstance(cand, Candidate):
            checked = check_candidate(cand, page, cat.figure_drop.fields)
            if isinstance(checked, Candidate):
                out.append(checked)
    return out


async def _ask(
    runner: Any, arm: Arm, atoms: list[AtomRecord], pages: dict[str, PageRecord]
) -> dict[str, check_call.CheckVerdict | None]:
    """The arm's checker on a list of items, in batches; ``{pair key: verdict or None}``."""
    out: dict[str, check_call.CheckVerdict | None] = {}
    keyed = [(pair_key(a), a) for a in atoms]
    for i in range(0, len(keyed), CHECK_BATCH):
        batch = keyed[i : i + CHECK_BATCH]
        ids = {f"C{n + 1}": key for n, (key, _) in enumerate(batch)}
        wire = []
        for n, (_, a) in enumerate(batch):
            page = pages[a.page_id]
            wire.append(
                check_call.CheckItem(
                    id=f"C{n + 1}",
                    claim=a.claim,
                    quote=a.quote,
                    data=json.dumps(a.data, ensure_ascii=False, separators=(",", ":"))[
                        :MAX_DATA_CHARS
                    ],
                    passage=passage_window(page, a.quote),
                    page_title=page.title,
                    page_domain=page.domain,
                    about_region=""
                    if arm.legacy or a.about_region == "unknown"
                    else a.about_region,
                    about_entity="" if arm.legacy else (a.about_entity or ""),
                )
            )
        res = await runner.run(arm.spec, check_call.CheckIn(items=wire, legacy=arm.legacy))
        for v in res.output.verdicts:
            if v.id in ids:
                out[ids[v.id]] = v
    return out


def _apply(a: AtomRecord, v: check_call.CheckVerdict | None, cat: Any) -> None:
    """The verdict on one item, as the engine applies it (not_supported rejects, unsupported fields are nulled)."""
    if v is None:
        a.flags.add("unchecked")
        return
    a.checked, a.verdict = True, v.verdict
    if v.verdict == "not_supported":
        a.status, a.reject_reason = "rejected", "not_supported"
        return
    if v.unsupported_fields:
        data, applied = null_paths(a.data, v.unsupported_fields)
        if applied:
            a.data, a.unsupported_fields = data, applied
            if not meets_minimum(a.kind, data, cat.lanes):
                a.status, a.reject_reason = "rejected", "unsupported_field"
                return
    if v.verdict == "partial":
        a.flags.add("partial_support")
        a.unsupported_sentences = list(v.unsupported_sentences)


def summarise(atoms: list[AtomRecord]) -> dict[str, Any]:
    verified = [a for a in atoms if a.status == "verified"]
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # kind -> [partial, checked]
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for a in verified:
        if a.verdict:
            by_kind[a.kind][1] += 1
            by_kind[a.kind][0] += a.verdict == "partial"
            groups[(a.kind, clean_key(a.norm_key))].add(a.verdict)
    unverified = sum(1 for v in groups.values() if "supported" not in v)
    checked = sum(c for _, c in by_kind.values())
    partial = sum(p for p, _ in by_kind.values())
    return {
        "atoms": len(atoms),
        "verified": len(verified),
        "rejected_not_supported": sum(1 for a in atoms if a.reject_reason == "not_supported"),
        "rejected_unsupported_field": sum(
            1 for a in atoms if a.reject_reason == "unsupported_field"
        ),
        "checked": checked,
        "partial": partial,
        "partial_share": round(partial / checked, 4) if checked else None,
        "tightened": sum(1 for a in verified if "tightened" in a.flags),
        "items": len(groups),
        "unverified_items": unverified,
        "unverified_share": round(unverified / len(groups), 4) if groups else None,
        "partial_by_kind": dict(sorted(by_kind.items())),
    }


def _range(values: list[float | None]) -> dict[str, float | None]:
    xs = [v for v in values if v is not None]
    if not xs:
        return {"min": None, "median": None, "max": None}
    return {"min": min(xs), "median": round(statistics.median(xs), 4), "max": max(xs)}


async def run_reextract(
    base_runner: Any,
    evidence: dict[str, Any],
    input_json: dict[str, Any],
    *,
    arms: list[str],
    repeats: int = 3,
    max_pairs: int | None = None,
) -> dict[str, Any]:
    cat = load_catalog()
    inp = ResearchInput.model_validate(input_json)
    plan = build_plan(inp, cat)
    by_label = {t.label: t for t in plan.targets}
    pairs, missing = pairs_of(evidence, by_label)
    if max_pairs and len(pairs) > max_pairs:
        # evenly spread over the sorted pairs, so a smaller sample still covers every kind of topic
        step = len(pairs) / max_pairs
        pairs = [pairs[int(i * step)] for i in range(max_pairs)]
    pages = {p["id"]: _page(p) for p in evidence.get("pages", [])}
    load_class = inp.load.load_class if inp.load else "standard"
    sem = asyncio.Semaphore(LANES)
    runs: dict[str, list[dict[str, Any]]] = {a: [] for a in arms}
    sample: list[dict[str, str]] = []
    cost_before = sum(r.cost_micros for _, r in base_runner.calls)
    for rep in range(repeats):
        runner = base_runner.for_tenant(
            f"reextract-{rep}"
        )  # no shared cache: each repeat is an independent answer
        batches = await asyncio.gather(
            *[
                _extract(runner, cat, load_class, t, pages[pid], sem)
                for t, pid in pairs
                if pid in pages
            ]
        )
        base: list[AtomRecord] = []
        for (t, pid), cands in zip([p for p in pairs if p[1] in pages], batches, strict=True):
            for c in cands:
                base.append(
                    AtomRecord(
                        id=f"h{len(base)}",
                        kind=c.kind,
                        job=t.job,
                        target_id=t.id,
                        norm_key=c.norm_key,
                        label=c.label,
                        claim=c.claim,
                        quote=c.quote,
                        impact=c.impact,
                        published_on=c.published_on,
                        data=c.data,
                        page_id=pid,
                        round=1,
                        about_region=c.about_region,
                        about_entity=c.about_entity,
                        flags=set(c.flags),
                    )
                )
        for name in arms:
            arm = ARMS[name]
            atoms = copy.deepcopy(base)

            async def ask(
                items: list[AtomRecord], arm: Arm = arm, runner: Any = runner
            ) -> dict[str, check_call.CheckVerdict | None]:
                return await _ask(runner, arm, items, pages)

            verdicts = await ask(atoms)
            for a in atoms:
                _apply(a, verdicts.get(pair_key(a)), cat)
            if arm.tighten:
                for a in await tighten_and_recheck(atoms, ask):
                    if "tightened" in a.flags and len(sample) < 30:
                        sample.append(
                            {
                                "kind": a.kind,
                                "before": a.claim_original or "",
                                "after": a.claim,
                                "quote": a.quote,
                            }
                        )
            runs[name].append(summarise(atoms))
    report: dict[str, Any] = {
        "pairs": len(pairs),
        "topics_not_rebuilt": missing,
        "repeats": repeats,
        "arms": {},
        "tightened_sample": sample,
        "cost_micros": sum(r.cost_micros for _, r in base_runner.calls) - cost_before,
    }
    for name in arms:
        rs = runs[name]
        report["arms"][name] = {
            "what": ARMS[name].name,
            "partial_share": _range([r["partial_share"] for r in rs]),
            "unverified_share": _range([r["unverified_share"] for r in rs]),
            "verified_atoms": _range([float(r["verified"]) for r in rs]),
            "tightened": _range([float(r["tightened"]) for r in rs]),
            "rejected_not_supported": _range([float(r["rejected_not_supported"]) for r in rs]),
            "repeats": rs,
        }
    return report
