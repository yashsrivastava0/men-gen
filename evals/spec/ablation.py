"""PS-10, the ablation arms (doc 17 section 10.4): does each content bundle earn its place?

The consumer probe is run on cumulative arms built from the pack by code: MUST only; plus `usually`; plus stories; plus
emotion (journeys and personas); plus COULD; plus mock and assets. Sections are stripped at the card format of
`mde_spec_core.render` (`usually:`, `stories:`, `mock:` lines of the card; the `screens/<id>.could.md` file; the stories,
journeys, personas, assets and mock files). Where the pack has no file for a bundle but the graph has the content, the
bundle is built from the graph and the report says so (`source: graph`); where there is nothing, the bundle is
`no_content` (nothing to ablate in this pack). A bundle earns its place when blocking questions or assumptions go down,
or the satisfiable share goes up, against the arm before it; otherwise it is to be cut or made optional by profile.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.spec.common import (
    Check,
    Tally,
    checks_pass,
    compare,
    thresholds,
    verdict_for,
    write_json,
)
from evals.spec.consumer_probe import ConsumerReport, run_consumer_probe
from evals.spec.packs import ProofPack, card_reads, count, parse_card, pick_samples, task_for_screen
from mde_spec_agent.calls.base import CallRunner
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.render import render_could

BUNDLES = ("usually", "stories", "emotion", "could", "mock_assets")
ARMS = ("must", *[f"+{b}" for b in BUNDLES])  # cumulative: "+stories" includes "+usually"


# ---------------------------------------------------------------------------------------------------- pure core: sections


def strip_usually(text: str) -> str:
    out: list[str] = []
    skipping = False
    for ln in text.split("\n"):
        if ln.startswith("usually:"):
            skipping = True
            continue
        if skipping and ln.startswith("  - "):
            continue
        skipping = False
        out.append(ln)
    return "\n".join(out)


def strip_line(text: str, key: str) -> str:
    return "\n".join(ln for ln in text.split("\n") if not ln.startswith(f"{key}:"))


def strip_screen(text: str, keep: set[str]) -> str:
    """A screen file with the bundles NOT in `keep` removed (its closure stays: it is part of the card's reads)."""
    if "usually" not in keep:
        text = strip_usually(text)
    if "stories" not in keep:
        text = strip_line(text, "stories")
    if "mock_assets" not in keep:
        text = strip_line(text, "mock")
    return text


def bundle_text_in_screen(text: str, bundle: str) -> str:
    """The part of a screen file a bundle owns (empty when the card has none): what stripping would remove."""
    if bundle == "usually":
        m = re.search(r"^usually:\n(?:  - .*\n?)+", text, re.M)
        return m.group(0) if m else ""
    if bundle == "stories":
        return next((ln for ln in text.split("\n") if ln.startswith("stories:")), "")
    if bundle == "mock_assets":
        return next((ln for ln in text.split("\n") if ln.startswith("mock:")), "")
    return ""


def keep_for(arm: str) -> set[str]:
    if arm == "must":
        return set()
    upto = arm[1:]
    return set(BUNDLES[: BUNDLES.index(upto) + 1])


# ---------------------------------------------------------------------------------------------------- bundles as files


def bundle_files(pack: ProofPack, sid: str, bundle: str) -> tuple[dict[str, str], str]:
    """The extra files a bundle adds to the reads of screen `sid`, and where they came from ('pack' or 'graph')."""
    f = pack.files
    g = pack.graph
    card = parse_card(f[f"screens/{sid}.md"]) if f"screens/{sid}.md" in f else None
    if bundle == "stories":
        found = {
            p: t
            for p, t in f.items()
            if p.startswith("stories/") and (not card or card.module in p or "/" in p)
        }
        if found:
            return found, "pack"
        if g and sid in g.screens:
            ids = list(g.screens[sid].stories) or [
                s.id for s in g.stories.values() if sid in s.screen_ids
            ]
            lines = [
                f"{s.id} as {s.role_id}: I want {s.want}, so that {s.so_that}"
                for i in ids
                if (s := g.stories.get(i))
            ]
            return ({"stories/_from_graph.md": "\n".join(lines)} if lines else {}), "graph"
        return {}, "none"
    if bundle == "emotion":
        found = {
            p: t for p, t in f.items() if p.startswith("journeys/") or p == "global/personas.md"
        }
        if found:
            return found, "pack"
        if g:
            lines = []
            for fl in sorted(g.flows.values(), key=lambda x: x.id):
                for st in fl.stages:
                    if sid in st.screen_ids:
                        lines.append(
                            f"{fl.id} {fl.name}, stage {st.n}: {st.goal}; feels {st.emotion}"
                            + (" (assumed)" if st.emotion_assumed else "")
                            + (f"; pain: {st.pain}" if st.pain else "")
                            + ("; a moment that matters" if st.n in fl.moments_that_matter else "")
                        )
            return ({"journeys/_from_graph.md": "\n".join(lines)} if lines else {}), "graph"
        return {}, "none"
    if bundle == "could":
        p = f"screens/{sid}.could.md"
        if p in f:
            return {p: f[p]}, "pack"
        if g and sid in g.screens and g.screens[sid].could:
            return {p: render_could(g.screens[sid])}, "graph"
        return {}, "none"
    if bundle == "mock_assets":
        found = {p: t for p, t in f.items() if p.startswith("contract/mock/")}
        if "global/assets.json" in f and f["global/assets.json"].strip() not in ("", "[]"):
            found["global/assets.json"] = f["global/assets.json"]
        return found, "pack" if found else "none"
    return {}, "none"


def arm_reads(pack: ProofPack, sid: str, arm: str) -> dict[str, str] | None:
    """The reads of one ablation arm for one screen: the card's own reads with the bundles of later arms stripped, plus
    the files of the bundles the arm includes. None when no task card reads the screen."""
    task = task_for_screen(pack, sid)
    if task is None:
        return None
    keep = keep_for(arm)
    reads = card_reads(pack, task)
    out: dict[str, str] = {}
    for p, t in reads.items():
        if p == "global/assets.json" and "mock_assets" not in keep:
            continue
        if p.startswith("contract/mock/") and "mock_assets" not in keep:
            continue
        out[p] = strip_screen(t, keep) if p.startswith("screens/") and p.endswith(".md") else t
    for b in BUNDLES:
        if b in keep:
            files, _src = bundle_files(pack, sid, b)
            out.update(files)
    return out


def bundle_inventory(pack: ProofPack, samples: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Per bundle: how many tokens it adds over the samples, from where, and whether the pack has any (`content`)."""
    inv: dict[str, dict[str, Any]] = {}
    for b in BUNDLES:
        tokens = 0
        sources: set[str] = set()
        for sid in samples:
            text = pack.files.get(f"screens/{sid}.md", "")
            tokens += count(bundle_text_in_screen(text, b)) if bundle_text_in_screen(text, b) else 0
            files, src = bundle_files(pack, sid, b)
            if files:
                sources.add(src)
                tokens += sum(count(t) for t in files.values())
        inv[b] = {
            "tokens": tokens,
            "content": tokens > 0,
            "source": "+".join(sorted(sources)) or "none",
        }
    return inv


def decide(prev: Mapping[str, Any], cur: Mapping[str, Any], content: bool) -> dict[str, Any]:
    """Earns its place: questions down, or assumptions down, or satisfiable share up (against the arm before)."""
    if not content:
        return {
            "decision": "no_content",
            "recommendation": "nothing to ablate: this pack has no such content",
        }
    d_bq = cur["blocking_per_screen"] - prev["blocking_per_screen"]
    d_as = cur["assumptions_per_screen"] - prev["assumptions_per_screen"]
    d_sat = cur["acceptance_satisfiable_share"] - prev["acceptance_satisfiable_share"]
    earns = d_bq < 0 or d_as < 0 or d_sat > 0
    return {
        "decision": "earns_place" if earns else "no_lift",
        "recommendation": "keep" if earns else "cut it, or make it optional by profile",
        "delta_blocking_per_screen": d_bq,
        "delta_assumptions_per_screen": d_as,
        "delta_satisfiable_share": d_sat,
    }


# ---------------------------------------------------------------------------------------------------- runner and report


@dataclass
class AblationReport:
    pack: str
    screens: list[str]
    arms: dict[str, dict[str, Any]]
    bundles: dict[str, dict[str, Any]]
    tally: Tally
    thresholds: dict[str, float]
    consumer: dict[str, ConsumerReport] = field(default_factory=dict)

    def checks(self) -> list[Check]:
        out: list[Check] = []
        for b, v in self.bundles.items():
            if v["decision"] == "no_content":
                continue
            out.append(compare(f"{b}: earns its place", v["decision"] == "earns_place", "==", True))
        if not out:
            out.append(Check("any bundle with content in this pack", False, "==", True, False))
        return out

    def would_pass(self) -> bool | None:
        return checks_pass(self.checks())

    def verdict(self, runs: int = 1) -> str:
        return verdict_for(self.would_pass(), live=self.tally.live, runs=runs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": "PS-10.ablation",
            "pack": self.pack,
            "screens": self.screens,
            "arms": self.arms,
            "bundles": self.bundles,
            "cut_or_optional": sorted(
                b for b, v in self.bundles.items() if v["decision"] == "no_lift"
            ),
            "no_content": sorted(
                b for b, v in self.bundles.items() if v["decision"] == "no_content"
            ),
            "checks": [c.as_dict() for c in self.checks()],
            "would_pass": self.would_pass(),
            "verdict": self.verdict(),
            "single_run": True,
            "money": self.tally.as_dict(),
            "note": (
                "single run; quality and consistency gains need the diversity tool and the owner's reading, not this probe. "
                + (
                    ""
                    if self.tally.live
                    else "OFFLINE STUB: the mock reader looks only for undefined ids, so no bundle can lift it; the decisions below prove the harness, not the bundles."
                )
            ),
        }


async def run_ablation(
    runner: CallRunner,
    pack: ProofPack,
    size: str = "quick",
    *,
    samples: Sequence[str] | None = None,
    arms: Sequence[str] = ARMS,
    cfg: SpecConfig | None = None,
) -> AblationReport:
    cfg = cfg or default_config()
    screens = list(samples) if samples is not None else pick_samples(pack, size)
    inv = bundle_inventory(pack, screens)
    start = len(runner.calls)
    metrics: dict[str, dict[str, Any]] = {}
    reports: dict[str, ConsumerReport] = {}
    for arm in arms:
        rep = await run_consumer_probe(
            runner,
            pack,
            size,
            samples=screens,
            arms=("A",),
            reads_for=lambda _a, sid, arm=arm: arm_reads(pack, sid, arm),
            cfg=cfg,
        )
        reports[arm] = rep
        m = rep.arms["A"].metrics()
        metrics[arm] = {**m, "added_tokens_vs_must": None}
    base_tokens = metrics[arms[0]].get("read_tokens_max", 0) if metrics else 0
    for arm in arms:
        metrics[arm]["added_tokens_vs_must"] = metrics[arm].get("read_tokens_max", 0) - base_tokens
    bundles: dict[str, dict[str, Any]] = {}
    for i, arm in enumerate(arms[1:], start=1):
        b = arm[1:]
        bundles[b] = {
            **inv[b],
            **decide(metrics[arms[i - 1]], metrics[arm], inv[b]["content"]),
        }
    return AblationReport(
        pack=pack.name,
        screens=screens,
        arms=metrics,
        bundles=bundles,
        tally=Tally.since(runner, start),
        thresholds=thresholds(cfg),
        consumer=reports,
    )


def write_report(report: AblationReport, out_dir: Path) -> Path:
    return write_json(out_dir / "ablation.json", report.as_dict())
