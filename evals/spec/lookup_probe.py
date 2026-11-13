"""PS-10, the library-lookup probe (doc 17 section 10.4): are the needs specific enough for a lookup in a component
library, without Spec ever naming a library?

The weakest configured consumer maps HELD-OUT needs of `global/needs.json` to the PUBLIC catalogue of a component library
and declares gaps. Measured: recall at 3, wrong-gap rate, and the same with a card-only baseline (no needs inventory: the
consumer reads the region's own words). A synthetic catalogue of about 25 components plants gaps and checks they are
found. The answer key is the snapshot's `shapes_it_can_show` mapping (high and medium confidence only); a need only a
low-confidence component could serve is left out of scoring (counted as `ambiguous`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.spec.calls.lookup.schema import LookupIn, LookupOut
from evals.spec.calls.lookup.spec import SPEC as LOOKUP_CALL
from evals.spec.catalogues import Catalogue, real_catalogues, synthetic_catalogue
from evals.spec.common import (
    Check,
    Tally,
    checks_pass,
    compare,
    stable_int,
    thresholds,
    verdict_for,
    write_json,
)
from evals.spec.packs import ProofPack, cards_of
from mde_spec_agent.calls.base import CallRunner
from mde_spec_core.config import SpecConfig, default_config

HELD_OUT_PERCENT = 50  # a need is held out when its id hash lands below this (the rest may be used to tune examples)
CHUNK = 25
ARMS = ("needs", "card_only")


# ---------------------------------------------------------------------------------------------------- pure core


def load_needs(pack: ProofPack) -> list[dict[str, Any]]:
    raw = pack.files.get("global/needs.json", "[]")
    return list(json.loads(raw))


def is_held_out(need_id: str) -> bool:
    return stable_int("held-out", need_id) % 100 < HELD_OUT_PERCENT


def split_needs(
    needs: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(tune, held_out): deterministic by id hash, disjoint, together all the needs."""
    tune = [dict(n) for n in needs if not is_held_out(str(n["id"]))]
    held = [dict(n) for n in needs if is_held_out(str(n["id"]))]
    return tune, held


def need_line(n: Mapping[str, Any]) -> str:
    parts = [f"shape={n['shape']}"]
    if n.get("kind"):
        parts.append(f"kind={n['kind']}")
    if n.get("attrs"):
        parts.append("attrs=" + ",".join(n["attrs"]))
    parts += [f"cardinality={n.get('cardinality', 'unknown')}", f"live={n.get('live', 'none')}"]
    if n.get("states"):
        parts.append(f"states={n['states']}")
    if n.get("intents"):
        parts.append("intents=" + ",".join(n["intents"]))
    return f"{n['id']}: " + "; ".join(parts)


_REGION = re.compile(r"^  - \{key: ([^,]+), shape: [^,]+, what: (?:\"([^\"]*)\"|([^,}]*))")


def region_whats(card_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in card_text.splitlines():
        m = _REGION.match(ln)
        if m:
            out[m.group(1).strip()] = (m.group(2) or m.group(3) or "").strip()
    return out


def card_only_line(n: Mapping[str, Any], pack: ProofPack) -> str:
    """The baseline: what a consumer would read with no needs inventory: the words of the first region that uses the need."""
    cards = cards_of(pack)
    for ref in n.get("used_by", []):
        sid, _, key = ref.partition(".")
        c = cards.get(sid)
        if c:
            what = region_whats(c.text).get(key)
            if what:
                return f"{n['id']}: {what} (screen {c.name})"
    return f"{n['id']}: a region of a screen (no words in the card)"


def truth_class(cat: Catalogue, need: Mapping[str, Any]) -> str:
    """fit: a solid component exists; gap: none could; ambiguous: only a low-confidence mapping exists (not scored)."""
    shape = str(need["shape"])
    if cat.fitting(shape):
        return "fit"
    return "ambiguous" if cat.maybe_fitting(shape) else "gap"


def score(
    cat: Catalogue, needs: Sequence[Mapping[str, Any]], out: LookupOut | None
) -> dict[str, Any]:
    """Recall at 3, wrong-gap rate and gap-found rate of one answer against the answer key."""
    by_item = {m.item: m for m in (out.matches if out else [])}
    fit = [n for n in needs if truth_class(cat, n) == "fit"]
    gaps = [n for n in needs if truth_class(cat, n) == "gap"]
    hits = wrong = 0
    detail: list[dict[str, Any]] = []
    for n in fit:
        m = by_item.get(str(n["id"]))
        keys = set(cat.fitting(str(n["shape"])))
        hit = bool(m and not m.gap and keys & set(m.candidates[:3]))
        wg = bool(m and m.gap)
        hits += hit
        wrong += wg
        detail.append({"need": n["id"], "shape": n["shape"], "hit": hit, "wrong_gap": wg})
    found = sum(1 for n in gaps if (m := by_item.get(str(n["id"]))) and m.gap)
    return {
        "needs": len(needs),
        "fit": len(fit),
        "gap": len(gaps),
        "ambiguous": len(needs) - len(fit) - len(gaps),
        "recall_at_3": hits / len(fit) if fit else None,
        "wrong_gap_rate": wrong / len(fit) if fit else None,
        "gap_found_rate": found / len(gaps) if gaps else None,
        "gaps_found": found,
        "per_need": detail,
    }


def planted_gap_needs(cat: Catalogue) -> list[dict[str, Any]]:
    """One synthetic need per planted gap shape, so the check never depends on what the pack happens to contain."""
    return [
        {
            "id": f"N-GAP-{i + 1:02d}",
            "shape": s,
            "kind": None,
            "attrs": [],
            "cardinality": "few",
            "live": "none",
            "states": "std.detail",
            "intents": [],
            "used_by": [],
            "synthetic": True,
        }
        for i, s in enumerate(cat.planted_gaps)
    ]


# ---------------------------------------------------------------------------------------------------- runner


async def _ask(runner: CallRunner, cat: Catalogue, lines: list[str], tag: str) -> LookupOut | None:
    matches = []
    for i in range(0, len(lines), CHUNK):
        part = lines[i : i + CHUNK]
        res = await runner.run(
            LOOKUP_CALL,
            LookupIn(library=cat.library, catalogue=cat.lines(), items=part),
            node_ids=(f"LOOKUP:{cat.key}:{tag}:{i}",),
            step="proof.lookup",
        )
        matches += list(res.output.matches)
    return LookupOut(matches=matches) if matches else None


@dataclass
class LibraryResult:
    library: str
    key: str
    checked_on: str
    source_url: str
    arms: dict[str, dict[str, Any]]
    planted: dict[str, Any] = field(default_factory=dict)


@dataclass
class LookupReport:
    pack: str
    libraries: list[LibraryResult]
    synthetic: LibraryResult | None
    split: dict[str, Any]
    tally: Tally
    thresholds: dict[str, float]

    def checks(self) -> list[Check]:
        t = self.thresholds
        out: list[Check] = []
        for lib in self.libraries:
            n = lib.arms["needs"]
            b = lib.arms["card_only"]
            out += [
                compare(
                    f"{lib.key}: recall at 3", n["recall_at_3"], ">=", t["lookup_recall_at_3_min"]
                ),
                compare(
                    f"{lib.key}: wrong-gap rate", n["wrong_gap_rate"], "<=", t["wrong_gap_max"]
                ),
                compare(
                    f"{lib.key}: better than the card-only baseline",
                    None
                    if n["recall_at_3"] is None or b["recall_at_3"] is None
                    else n["recall_at_3"] > b["recall_at_3"],
                    "==",
                    True,
                ),
            ]
        if self.synthetic:
            out.append(
                compare(
                    "synthetic: planted gaps found",
                    self.synthetic.planted.get("gap_found_rate"),
                    ">=",
                    1.0,
                )
            )
        return out

    def would_pass(self) -> bool | None:
        return checks_pass(self.checks())

    def verdict(self, runs: int = 1) -> str:
        return verdict_for(self.would_pass(), live=self.tally.live, runs=runs)

    def as_dict(self) -> dict[str, Any]:
        def lib(r: LibraryResult) -> dict[str, Any]:
            return {
                "library": r.library,
                "key": r.key,
                "checked_on": r.checked_on,
                "source_url": r.source_url,
                "arms": {
                    a: {k: v for k, v in m.items() if k != "per_need"} for a, m in r.arms.items()
                },
                "per_need": {a: m["per_need"] for a, m in r.arms.items()},
                "planted": r.planted,
            }

        return {
            "gate": "PS-10.lookup",
            "pack": self.pack,
            "split": self.split,
            "libraries": [lib(r) for r in self.libraries],
            "synthetic": lib(self.synthetic) if self.synthetic else None,
            "checks": [c.as_dict() for c in self.checks()],
            "would_pass": self.would_pass(),
            "verdict": self.verdict(),
            "single_run": True,
            "money": self.tally.as_dict(),
            "note": (
                "single run. The answer key is the snapshot author's shape mapping (high and medium confidence). "
                + (
                    ""
                    if self.tally.live
                    else "OFFLINE STUB: the stub answers by shape name, so recall is high by construction; this proves the harness and the planted-gap check, not the consumer."
                )
            ),
        }


async def run_lookup_probe(
    runner: CallRunner,
    pack: ProofPack,
    *,
    catalogues: Sequence[Catalogue] | None = None,
    cfg: SpecConfig | None = None,
    include_synthetic: bool = True,
) -> LookupReport:
    cfg = cfg or default_config()
    start = len(runner.calls)
    needs = load_needs(pack)
    tune, held = split_needs(needs)
    results: list[LibraryResult] = []
    for cat in catalogues if catalogues is not None else real_catalogues():
        arms: dict[str, dict[str, Any]] = {}
        arms["needs"] = score(
            cat, held, await _ask(runner, cat, [need_line(n) for n in held], "needs")
        )
        arms["card_only"] = score(
            cat, held, await _ask(runner, cat, [card_only_line(n, pack) for n in held], "card")
        )
        results.append(LibraryResult(cat.library, cat.key, cat.checked_on, cat.source_url, arms))
    syn: LibraryResult | None = None
    if include_synthetic:
        cat = synthetic_catalogue()
        probe_needs = [*held, *planted_gap_needs(cat)]
        out = await _ask(runner, cat, [need_line(n) for n in probe_needs], "synthetic")
        sc = score(cat, probe_needs, out)
        syn = LibraryResult(
            cat.library,
            cat.key,
            cat.checked_on,
            cat.source_url,
            {"needs": sc},
            planted={
                "gap_shapes": cat.planted_gaps,
                "gap_found_rate": sc["gap_found_rate"],
                "gaps_found": sc["gaps_found"],
                "wrong_gap_rate": sc["wrong_gap_rate"],
            },
        )
    return LookupReport(
        pack=pack.name,
        libraries=results,
        synthetic=syn,
        split={
            "held_out_percent": HELD_OUT_PERCENT,
            "needs": len(needs),
            "held_out": len(held),
            "tune": len(tune),
        },
        tally=Tally.since(runner, start),
        thresholds=thresholds(cfg),
    )


def write_report(report: LookupReport, out_dir: Path) -> Path:
    return write_json(out_dir / "lookup-probe.json", report.as_dict())
