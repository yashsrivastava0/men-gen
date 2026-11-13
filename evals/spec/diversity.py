"""PS-10, diversity and consistency, measured SEPARATELY (doc 17 section 10.4), with COULD on and off.

Between-app diversity: different apps from the same generator differ (direction axes, token seeds, COULD signature).
Within-app consistency: the screens of one app share a design language (the shapes and intents they use, the motion
intents their COULD entries pick for the same kind of moment). Both are deterministic code metrics; an optional critic
call (stub offline) gives the owner something to sample. The packs must come from a Design and Screens lane to mean
anything: a baseline pack without directions reports `not_applicable`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from evals.spec.calls.diversity.schema import DiversityIn
from evals.spec.calls.diversity.spec import SPEC as CRITIC_CALL
from evals.spec.common import (
    Check,
    Tally,
    checks_pass,
    compare,
    thresholds,
    verdict_for,
    write_json,
)
from evals.spec.packs import ProofPack, cards_of
from mde_spec_agent.calls.base import CallRunner
from mde_spec_core import vocab
from mde_spec_core.config import SpecConfig, default_config

_KIND = re.compile(r"\bkind: (\w+)")
_MOTION = re.compile(r"\bmotion: (\w+)")
_SHAPE = re.compile(r"\bshape: ([\w:]+)")
_INTENT = re.compile(r"\bintent: (\w+)")


@dataclass
class Signature:
    directions: list[dict[str, str]]
    hues: list[float]
    token_names: set[str]
    could: list[tuple[str, str]]  # (kind, motion) of every COULD entry
    source: str = "graph"

    @property
    def has_directions(self) -> bool:
        return bool(self.directions)


# ---------------------------------------------------------------------------------------------------- reading a pack


def signature(pack: ProofPack) -> Signature:
    """Directions, token seeds and COULD entries of a pack: from its graph when it has one, else from its files."""
    g = pack.graph
    axes = vocab.direction_axes_file().diff_axes
    fld = {a: vocab.direction_axes()[a].field for a in axes}
    dirs: list[dict[str, str]] = []
    hues: list[float] = []
    names: set[str] = set()
    could: list[tuple[str, str]] = []
    if g is not None:
        for did in sorted(g.directions):
            d = g.directions[did]
            dirs.append({a: str(getattr(d, fld[a], "")) for a in axes})
            t = g.tokens.get(did) or {}
            names |= set(t.get("names", []))
            h = ((t.get("report") or {}).get("seed") or {}).get("hue")
            if h is not None:
                hues.append(float(h))
        for s in g.screens.values():
            could += [(c.kind, c.motion or "") for c in s.could]
        return Signature(dirs, hues, names, could, "graph")
    text = pack.files.get("global/design-direction.md", "")
    for ln in text.splitlines():
        m = re.search(r"mood (\S+?);.*?motion (\S+?);", ln)
        if m:
            dirs.append({"mood_family": m.group(1), "motion_personality": m.group(2)})
    try:
        toks = json.loads(pack.files.get("global/tokens.dtcg.json", "{}"))
    except ValueError:
        toks = {}
    for t in toks.values() if isinstance(toks, dict) else []:
        if isinstance(t, dict):
            names |= set(t.get("names", []))
            h = ((t.get("report") or {}).get("seed") or {}).get("hue")
            if h is not None:
                hues.append(float(h))
    for p, t in pack.files.items():
        if p.endswith(".could.md"):
            for ln in t.splitlines():
                k, m2 = _KIND.search(ln), _MOTION.search(ln)
                if k and "id: CP-" in ln:
                    could.append((k.group(1), m2.group(1) if m2 else ""))
    return Signature(dirs, hues, names, could, "files")


# ---------------------------------------------------------------------------------------------------- pure metrics


def circular_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def jaccard(a: set[Any], b: set[Any]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def between_app(
    a: Signature, b: Signature, *, could: bool, cfg: SpecConfig | None = None
) -> dict[str, Any]:
    """Do two apps differ? Compared against the generator's own rule for a direction set (axis and hue gaps)."""
    cfg = cfg or default_config()
    need_axes = cfg.checks.min_direction_axis_differences
    need_hue = cfg.checks.min_seed_hue_gap_degrees
    if not (a.has_directions and b.has_directions):
        return {"applicable": False, "why": "a pack has no direction (the Design lane has not run)"}
    diffs = [
        len(vocab.direction_axis_differences(x, y)) for x in a.directions for y in b.directions
    ]
    gaps = [circular_gap(h1, h2) for h1 in a.hues for h2 in b.hues]
    parts = [min(1.0, min(diffs) / need_axes)]
    if gaps:
        parts.append(min(1.0, min(gaps) / need_hue))
    could_diff: float | None = None
    if could and (a.could or b.could):
        could_diff = 1.0 - jaccard(set(a.could), set(b.could))
        parts.append(could_diff)
    return {
        "applicable": True,
        "could": could,
        "min_axis_diff": min(diffs),
        "mean_axis_diff": sum(diffs) / len(diffs),
        "axis_diff_required": need_axes,
        "min_seed_hue_gap": min(gaps) if gaps else None,
        "hue_gap_required": need_hue,
        "token_name_overlap": jaccard(a.token_names, b.token_names),
        "could_signature_difference": could_diff,
        "diversity": sum(parts) / len(parts),
    }


def _mean_pairwise(sets: Sequence[set[str]]) -> float | None:
    pairs = [jaccard(x, y) for x, y in combinations(sets, 2)]
    return sum(pairs) / len(pairs) if pairs else None


def within_app(
    pack: ProofPack, screens: Sequence[str] | None = None, *, could: bool
) -> dict[str, Any]:
    """Do the screens of one app share a language? Over the pilot screens (or the given ones)."""
    cards = cards_of(pack)
    ids = (
        list(screens)
        if screens is not None
        else sorted({s for v in pack.pilots.values() for s in v}) or sorted(cards)
    )
    ids = [s for s in ids if s in cards]
    by_type: dict[str, list[str]] = {}
    for sid in ids:
        by_type.setdefault(cards[sid].screen_type, []).append(sid)

    def per_type(extract: Any) -> float | None:
        vals = []
        for group in by_type.values():
            sets = [
                extract(pack.files[f"screens/{sid}.md"].split("\n## closure")[0]) for sid in group
            ]
            m = _mean_pairwise(sets)
            if m is not None:
                vals.append(m)
        if vals:
            return sum(vals) / len(vals)
        return _mean_pairwise(
            [extract(pack.files[f"screens/{sid}.md"].split("\n## closure")[0]) for sid in ids]
        )

    shape_c = per_type(lambda t: set(_SHAPE.findall(t)))
    intent_c = per_type(lambda t: set(_INTENT.findall(t)))
    parts = [v for v in (shape_c, intent_c) if v is not None]
    motion_c: float | None = None
    if could:
        g = pack.graph
        entries: list[tuple[str, str]] = []
        for sid in ids:
            if g is not None and sid in g.screens:
                entries += [(c.kind, c.motion or "") for c in g.screens[sid].could]
            else:
                for ln in pack.files.get(f"screens/{sid}.could.md", "").splitlines():
                    k, m = _KIND.search(ln), _MOTION.search(ln)
                    if k and "id: CP-" in ln:
                        entries.append((k.group(1), m.group(1) if m else ""))
        kinds: dict[str, list[str]] = {}
        for k, m in entries:
            kinds.setdefault(k, []).append(m)
        if kinds:
            motion_c = sum(max(Counter(v).values()) / len(v) for v in kinds.values()) / len(kinds)
            parts.append(motion_c)
    if not parts:
        return {"applicable": False, "why": "too few screens to compare"}
    return {
        "applicable": True,
        "could": could,
        "screens": ids,
        "shape_consistency": shape_c,
        "intent_consistency": intent_c,
        "motion_consistency": motion_c,
        "consistency": sum(parts) / len(parts),
    }


def records_for_critic(pack: ProofPack, label: str, *, could: bool) -> list[str]:
    sig = signature(pack)
    lines = [
        f"{label}: direction {' '.join(f'{k} {v}' for k, v in sorted(d.items()))}"
        for d in sig.directions
    ]
    if could:
        lines += [f"{label}: signature moment {k}, motion {m}" for k, m in sorted(set(sig.could))]
    return lines or [f"{label}: (no direction in this pack)"]


# ---------------------------------------------------------------------------------------------------- report


@dataclass
class DiversityReport:
    pair: tuple[str, str]
    between: dict[str, dict[str, Any]]  # "could_off" / "could_on"
    within: dict[str, dict[str, Any]]
    critic: dict[str, Any]
    tally: Tally
    thresholds: dict[str, float]
    packs_live: bool = False

    def checks(self) -> list[Check]:
        off, on = self.between["could_off"], self.between["could_on"]
        woff, won = self.within["could_off"], self.within["could_on"]
        out: list[Check] = []
        if off.get("applicable"):
            out.append(
                compare(
                    "between-app: the two apps differ on at least one direction axis",
                    off["min_axis_diff"],
                    ">=",
                    1,
                )
            )
            if on.get("applicable"):
                out.append(
                    compare(
                        "COULD on: between-app diversity not lower",
                        on["diversity"] >= off["diversity"] - 1e-9,
                        "==",
                        True,
                    )
                )
        else:
            out.append(Check("between-app diversity", None, "info", None, None))
        if woff.get("applicable") and won.get("applicable"):
            out.append(
                compare(
                    "COULD on: within-app consistency not lower",
                    won["consistency"] >= woff["consistency"] - 1e-9,
                    "==",
                    True,
                )
            )
        else:
            out.append(Check("within-app consistency", None, "info", None, None))
        return out

    def would_pass(self) -> bool | None:
        return checks_pass(self.checks())

    def verdict(self) -> str:
        # deterministic code metrics: one run is complete, but only packs written by a live lane are evidence
        return verdict_for(self.would_pass(), live=self.packs_live, runs=2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": "PS-10.diversity",
            "packs": list(self.pair),
            "between_app": self.between,
            "within_app": self.within,
            "critic": self.critic,
            "checks": [c.as_dict() for c in self.checks()],
            "would_pass": self.would_pass(),
            "verdict": self.verdict(),
            "packs_live": self.packs_live,
            "single_run": True,
            "money": self.tally.as_dict(),
            "note": (
                "Deterministic code metrics (diversity and consistency measured separately, COULD off and on); the critic call "
                "is for the owner to sample. "
                + (
                    ""
                    if self.packs_live
                    else "The packs were NOT written by a live lane: this proves the harness only."
                )
            ),
        }


async def run_diversity(
    pack_a: ProofPack,
    pack_b: ProofPack,
    runner: CallRunner | None = None,
    *,
    packs_live: bool = False,
    cfg: SpecConfig | None = None,
) -> DiversityReport:
    cfg = cfg or default_config()
    sa, sb = signature(pack_a), signature(pack_b)
    between = {
        "could_off": between_app(sa, sb, could=False, cfg=cfg),
        "could_on": between_app(sa, sb, could=True, cfg=cfg),
    }
    within = {
        "could_off": within_app(pack_a, could=False),
        "could_on": within_app(pack_a, could=True),
    }
    start = len(runner.calls) if runner else 0
    critic: dict[str, Any] = {"asked": False}
    if runner is not None:
        outs: dict[str, Any] = {}
        for tag, q, recs in (
            (
                "between_app",
                "between_app",
                [
                    *records_for_critic(pack_a, "A", could=True),
                    *records_for_critic(pack_b, "B", could=True),
                ],
            ),
            ("within_app", "within_app", records_for_critic(pack_a, "screen", could=True)),
        ):
            res = await runner.run(
                CRITIC_CALL,
                DiversityIn(question=q, records=recs),
                node_ids=(f"DIVERSITY:{tag}",),
                step="proof.diversity",
            )
            outs[tag] = {
                "score": res.output.score,
                "notes": res.output.notes,
                "fallback": res.used_fallback,
                "owner_note": "",
            }
        critic = {"asked": True, **outs}
    return DiversityReport(
        pair=(pack_a.name, pack_b.name),
        between=between,
        within=within,
        critic=critic,
        tally=Tally.since(runner, start) if runner else Tally(),
        thresholds=thresholds(cfg),
        packs_live=packs_live,
    )


def write_report(report: DiversityReport, out_dir: Path) -> Path:
    return write_json(out_dir / "diversity.json", report.as_dict())
