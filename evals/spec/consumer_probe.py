"""PS-6, the consumer probe (doc 16 section 4): can the weakest configured consumer build a screen from what it is given?

Three arms per sampled screen: A the task card's reads, B the whole pack (only where it fits `whole_pack_tokens`; else
skipped with the reason), C the card alone (no prefix, no global files). The consumer returns
`{outline, blocking_questions, assumptions, acceptance_coverage}` (the canary's own call, run through the guarded
runner). Metrics per arm, the two comparisons of the doc, the developer-test hook and the owner's sample file.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.spec.common import (
    DATA_DIR,
    Check,
    Tally,
    checks_pass,
    compare,
    thresholds,
    verdict_for,
    write_json,
)
from evals.spec.packs import (
    ProofPack,
    card_reads,
    count,
    pick_samples,
    task_for_screen,
)
from mde_spec_agent.calls.base import CallRunner, RunResult, SpecCallSpec
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.libraries import form_word_findings
from mde_spec_core.vocab import behavior_categories
from mde_verify_agent.calls.canary.schema import CanaryIn, CanaryOut
from mde_verify_agent.calls.canary.spec import SPEC as CANARY_PORT_SPEC
from mde_verify_agent.digest import criteria_from_reads

ARMS = ("A", "B", "C")
ARM_NAMES = {"A": "task card reads", "B": "whole pack", "C": "card alone"}
PROBE = SpecCallSpec(
    spec=CANARY_PORT_SPEC.spec,
    kind="PROOF-CONSUMER",
    timeout_s=CANARY_PORT_SPEC.timeout_s,
    fallback=CANARY_PORT_SPEC.fallback,
    validators=CANARY_PORT_SPEC.validators,
)
ReadsFor = Callable[[str, str], "Mapping[str, str] | None"]


@dataclass
class ScreenProbe:
    screen: str
    arm: str
    reads: list[str]
    read_tokens: int
    outline: list[str]
    blocking_questions: list[str]
    assumptions: list[str]
    acceptance_coverage: list[dict[str, Any]]
    used_fallback: bool = False
    live: bool = False
    cost_micro_usd: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "screen": self.screen,
            "arm": self.arm,
            "arm_name": ARM_NAMES[self.arm],
            "reads": self.reads,
            "read_tokens": self.read_tokens,
            "outline": self.outline,
            "blocking_questions": self.blocking_questions,
            "assumptions": self.assumptions,
            "acceptance_coverage": self.acceptance_coverage,
            "used_fallback": self.used_fallback,
        }


@dataclass
class ArmResult:
    arm: str
    skipped: str = ""  # the reason, when the arm did not run
    probes: list[ScreenProbe] = field(default_factory=list)

    def metrics(self) -> dict[str, Any]:
        return arm_metrics(self.probes) if self.probes else {}


@dataclass
class ConsumerReport:
    pack: str
    size: str
    screens: list[str]
    arms: dict[str, ArmResult]
    tally: Tally
    single_run: bool = True
    thresholds: dict[str, float] = field(default_factory=dict)

    def arm_metrics(self) -> dict[str, dict[str, Any]]:
        return {a: r.metrics() for a, r in self.arms.items() if r.probes}

    def comparisons(self) -> dict[str, bool | None]:
        return compare_arms(self.arm_metrics(), self.thresholds)

    def checks(self) -> list[Check]:
        t = self.thresholds
        m = self.arm_metrics().get("A")
        cmp_ = self.comparisons()
        out = [
            compare(
                "A blocking questions per screen",
                m["blocking_per_screen"] if m else None,
                "<=",
                t["blocking_questions_per_screen_max"],
            ),
            compare(
                "A assumptions per screen",
                m["assumptions_per_screen"] if m else None,
                "<=",
                t["assumptions_per_screen_max"],
            ),
            compare(
                "A acceptance satisfiable share",
                m["acceptance_satisfiable_share"] if m else None,
                ">=",
                t["acceptance_satisfiable_min"],
            ),
        ]
        if cmp_["slice_not_worse_than_whole"] is not None:
            out.append(
                compare(
                    "slice arm not worse than whole pack",
                    cmp_["slice_not_worse_than_whole"],
                    "==",
                    True,
                )
            )
        out.append(
            compare("card-alone arm clearly worse", cmp_["card_alone_clearly_worse"], "==", True)
        )
        return out

    def would_pass(self) -> bool | None:
        return checks_pass(self.checks())

    def verdict(self, runs: int = 1) -> str:
        return verdict_for(self.would_pass(), live=self.tally.live, runs=runs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": "PS-6",
            "pack": self.pack,
            "size": self.size,
            "screens": self.screens,
            "arms": {
                a: {
                    "name": ARM_NAMES[a],
                    "skipped": r.skipped,
                    "metrics": r.metrics(),
                }
                for a, r in self.arms.items()
            },
            "comparisons": self.comparisons(),
            "checks": [c.as_dict() for c in self.checks()],
            "would_pass": self.would_pass(),
            "verdict": self.verdict(),
            "single_run": self.single_run,
            "money": self.tally.as_dict(),
            "note": (
                "single run: a run decides nothing; repeat and report the range. "
                + (
                    ""
                    if self.tally.live
                    else "OFFLINE STUB: the mock reader only checks that ids used are defined; this proves the harness, not the live rate."
                )
            ),
        }


# ---------------------------------------------------------------------------------------------------- pure core


def arm_metrics(probes: Sequence[ScreenProbe]) -> dict[str, Any]:
    n = len(probes)
    bq = sum(len(p.blocking_questions) for p in probes)
    asm = sum(len(p.assumptions) for p in probes)
    crit = [c for p in probes for c in p.acceptance_coverage]
    sat = sum(1 for c in crit if c["satisfiable"])
    return {
        "screens": n,
        "blocking_questions": bq,
        "blocking_per_screen": bq / n if n else 0.0,
        "assumptions": asm,
        "assumptions_per_screen": asm / n if n else 0.0,
        "criteria": len(crit),
        "acceptance_satisfiable": sat,
        "acceptance_satisfiable_share": sat / len(crit) if crit else 1.0,
        "fallback_calls": sum(1 for p in probes if p.used_fallback),
        "read_tokens_max": max((p.read_tokens for p in probes), default=0),
    }


def compare_arms(
    metrics: Mapping[str, Mapping[str, Any]], t: Mapping[str, float]
) -> dict[str, bool | None]:
    """slice_not_worse_than_whole (needs arms A and B) and card_alone_clearly_worse (needs A and C).

    'Clearly worse' is read from the thresholds themselves: C asks at least one blocking-question threshold more per
    screen than A (0.3), or A satisfies at least one acceptance margin more (1 - 0.90 = 0.10)."""
    a, b, c = metrics.get("A"), metrics.get("B"), metrics.get("C")
    slice_ok: bool | None = None
    if a and b:
        slice_ok = (
            a["blocking_per_screen"] <= b["blocking_per_screen"]
            and a["acceptance_satisfiable_share"] >= b["acceptance_satisfiable_share"]
        )
    worse: bool | None = None
    if a and c:
        worse = (
            c["blocking_per_screen"] - a["blocking_per_screen"]
            >= t["blocking_questions_per_screen_max"]
            or a["acceptance_satisfiable_share"] - c["acceptance_satisfiable_share"]
            >= 1.0 - t["acceptance_satisfiable_min"]
        )
    return {"slice_not_worse_than_whole": slice_ok, "card_alone_clearly_worse": worse}


def arm_reads(pack: ProofPack, sid: str, arm: str) -> tuple[dict[str, str] | None, str]:
    """(reads, skip reason). A: the card's own reads; B: every file, if the pack fits; C: the card alone."""
    if arm == "A":
        task = task_for_screen(pack, sid)
        if task is None:
            return None, f"no task card reads {sid}"
        return card_reads(pack, task), ""
    if arm == "B":
        whole = int(pack.manifest.get("whole_pack_tokens", 0))
        total = pack.total_tokens
        if not whole or total > whole:
            return (
                None,
                f"the pack is {total} tokens and the profile's whole_pack_tokens is {whole}: it does not fit",
            )
        return {
            p: t for p, t in sorted(pack.files.items()) if p not in ("MANIFEST.json", "index.json")
        }, ""
    path = f"screens/{sid}.md"
    return ({path: pack.files[path]}, "") if path in pack.files else (None, f"no file {path}")


def criteria_of(pack: ProofPack, sid: str) -> list[str]:
    path = f"screens/{sid}.md"
    return criteria_from_reads({path: pack.files[path]}) if path in pack.files else []


def canary_input(sid: str, arm: str, reads: Mapping[str, str], criteria: list[str]) -> CanaryIn:
    paths = list(reads)
    return CanaryIn(
        task_id=f"PROBE-{sid}-{arm}",
        title=f"Build screen {sid}",
        criteria=criteria,
        read_paths=paths,
        reads=[f"=== {p} ===\n{reads[p]}" for p in paths],
    )


# ---------------------------------------------------------------------------------------------------- runner


async def probe_screen(
    runner: CallRunner,
    pack: ProofPack,
    sid: str,
    arm: str,
    reads: Mapping[str, str],
    *,
    run_index: int = 0,
) -> ScreenProbe:
    inp = canary_input(sid, arm, reads, criteria_of(pack, sid))
    res: RunResult[CanaryOut] = await runner.run(
        PROBE, inp, node_ids=(f"PROBE:{sid}:{arm}:{run_index}",), step=f"proof.consumer.{arm}"
    )
    out = res.output
    return ScreenProbe(
        screen=sid,
        arm=arm,
        reads=list(reads),
        read_tokens=sum(count(t) for t in reads.values()),
        outline=list(out.outline),
        blocking_questions=list(out.blocking_questions),
        assumptions=list(out.assumptions),
        acceptance_coverage=[
            {"criterion": c.criterion, "satisfiable": c.satisfiable, "how": c.how}
            for c in out.acceptance_coverage
        ],
        used_fallback=res.used_fallback,
        live=res.live,
        cost_micro_usd=res.cost_micros,
    )


async def run_consumer_probe(
    runner: CallRunner,
    pack: ProofPack,
    size: str = "quick",
    *,
    samples: Sequence[str] | None = None,
    arms: Sequence[str] = ARMS,
    reads_for: ReadsFor | None = None,
    cfg: SpecConfig | None = None,
    run_index: int = 0,
) -> ConsumerReport:
    """Run the arms on the sample. `reads_for(arm, sid)` may replace the reads (the ablation uses it)."""
    cfg = cfg or default_config()
    screens = list(samples) if samples is not None else pick_samples(pack, size)
    start = len(runner.calls)
    result: dict[str, ArmResult] = {}
    for arm in arms:
        ar = ArmResult(arm)
        for sid in screens:
            reads, why = (reads_for(arm, sid), "") if reads_for else (None, "")
            if reads is None:
                reads, why = arm_reads(pack, sid, arm)
            if reads is None:
                ar.skipped = why
                ar.probes = []
                break
            ar.probes.append(await probe_screen(runner, pack, sid, arm, reads, run_index=run_index))
        result[arm] = ar
    return ConsumerReport(
        pack=pack.name,
        size=size,
        screens=screens,
        arms=result,
        tally=Tally.since(runner, start),
        thresholds=thresholds(cfg),
    )


# ---------------------------------------------------------------------------------------------------- developer test


def _gap_classes() -> dict[str, Any]:
    return json.loads((DATA_DIR / "gap-classes.json").read_text(encoding="utf-8"))


def classify_gap(question: str) -> dict[str, str]:
    """A recorded gap becomes a required item: a `behaviors` category or a `rules` entry. A gap about layout is
    rejected (never add layout); a gap that matches nothing is kept for a person to read."""
    if form_word_findings(question):
        return {"kind": "rejected_layout", "category": "", "why": "form words: never add layout"}
    gc = _gap_classes()
    low = question.lower()
    valid = set(behavior_categories())
    for cat, words in gc["behaviors"].items():
        if cat in valid and any(w in low for w in words):
            return {"kind": "behaviors_category", "category": cat, "why": "matched " + cat}
    if any(w in low for w in gc["rules"]):
        return {"kind": "rules_entry", "category": "", "why": "a number or a rule"}
    return {"kind": "needs_review", "category": "", "why": "no class matched: a person reads it"}


async def developer_test(
    runner: CallRunner, pack: ProofPack, screen_id: str, *, arms: Sequence[str] = ("A", "C")
) -> dict[str, Any]:
    """PS-6 on one chosen screen (the doc's SCR-014 of the Standard export): record the questions, and turn each gap
    into a proposal for a required `behaviors` category or `rules` entry. Layout gaps are rejected."""
    rep = await run_consumer_probe(runner, pack, samples=[screen_id], arms=arms)
    rows: list[dict[str, Any]] = []
    for arm, ar in rep.arms.items():
        for p in ar.probes:
            for q in p.blocking_questions:
                rows.append({"arm": arm, "question": q, **classify_gap(q)})
            for q in p.assumptions:
                rows.append({"arm": arm, "question": q, "assumption": True, **classify_gap(q)})
    return {
        "screen": screen_id,
        "questions": rows,
        "proposals": [r for r in rows if r["kind"] in ("behaviors_category", "rules_entry")],
        "rejected": [r for r in rows if r["kind"] == "rejected_layout"],
        "to_read": [r for r in rows if r["kind"] == "needs_review"],
        "money": rep.tally.as_dict(),
    }


# ---------------------------------------------------------------------------------------------------- owner file and writer


def owner_sample(report: ConsumerReport, n: int = 10) -> list[dict[str, Any]]:
    """n outputs for the owner to read: arms in turn (A, B, C) over the screens, so every arm is seen."""
    pools = [
        list(report.arms[a].probes) for a in ARMS if a in report.arms and report.arms[a].probes
    ]
    out: list[dict[str, Any]] = []
    i = 0
    while len(out) < n and any(pools):
        pool = pools[i % len(pools)]
        if pool:
            out.append(pool.pop(0).as_dict())
        i += 1
        if i > 10_000:
            break
    return out


def write_report(report: ConsumerReport, out_dir: Path, *, extra_samples: int = 10) -> Path:
    """`consumer-probe.json` and `owner-reading/probe-outputs.json` (up to 10 outputs, with a place for the owner's note)."""
    path = write_json(out_dir / "consumer-probe.json", report.as_dict())
    write_json(
        out_dir / "owner-reading" / "probe-outputs.json",
        {
            "what": "Ten consumer-probe outputs for the owner to read (doc 16 PS-6). A blocking question is what the weakest "
            "consumer says it cannot decide from its reads. Mark any that is a false alarm.",
            "offline_stub": not report.tally.live,
            "outputs": [{**o, "owner_note": ""} for o in owner_sample(report, extra_samples)],
        },
    )
    return path
