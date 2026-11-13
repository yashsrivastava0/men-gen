"""PS-4, freedom: the pack must not bind the front end (doc 16 section 2, 7).

Two parts. (1) The freedom probe: the SAME card built twice by the weakest consumer under two style seeds; the call
returns an outline of region arrangements (one token of a closed list, plus the region order); the share of screens
whose arrangement differs is compared with the threshold (30 percent). (2) Schema and lint statistics of the cards
(validators V-24, V-25, V-38: schema validity, tier ceilings, form-word warnings per card with the explained list).
A file of five cards for the owner (`owner-reading/cards.json`) is written for the 'no layout instruction' reading.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.spec.calls.freedom.schema import FreedomIn, FreedomOut
from evals.spec.calls.freedom.spec import SPEC as FREEDOM_CALL
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
from evals.spec.packs import ProofPack, card_text, cards_of, pick_samples
from mde_spec_agent.calls.base import CallRunner
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.tokens import direction_fields, pick_directions
from mde_spec_core.validators.base import run_all
from mde_spec_core.validators.context import make_context

OWNER_CARDS = 5


def arrangement_tokens() -> list[str]:
    return list(json.loads((DATA_DIR / "arrangements.json").read_text(encoding="utf-8"))["tokens"])


def style_seeds(n: int = 2, seed: int = 11) -> list[str]:
    """Two plain-words style seeds that differ on several direction axes (chosen by code from the direction vocabulary)."""
    picks = pick_directions(n, rnd=random.Random(seed))  # noqa: S311 - seeded choice, not security
    out = []
    for p in picks:
        f = direction_fields(p)
        out.append(
            ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(f.items()) if k != "seed_hue")
        )
    return out


# ---------------------------------------------------------------------------------------------------- pure core


@dataclass
class ScreenPair:
    screen: str
    a: FreedomOut
    b: FreedomOut
    used_fallback: bool = False

    @property
    def token_differs(self) -> bool:
        return self.a.arrangement != self.b.arrangement

    @property
    def order_differs(self) -> bool:
        return list(self.a.region_order) != list(self.b.region_order)

    @property
    def differs(self) -> bool:
        return self.token_differs or self.order_differs


def distinct_shares(pairs: Sequence[ScreenPair]) -> dict[str, float]:
    n = len(pairs)
    if not n:
        return {
            "distinct_layout_share": 0.0,
            "distinct_token_share": 0.0,
            "distinct_order_share": 0.0,
        }
    return {
        "distinct_layout_share": sum(p.differs for p in pairs) / n,
        "distinct_token_share": sum(p.token_differs for p in pairs) / n,
        "distinct_order_share": sum(p.order_differs for p in pairs) / n,
    }


def card_stats(pack: ProofPack, cfg: SpecConfig | None = None) -> dict[str, Any]:
    """Schema validity, tier ceilings and form-word warnings per card (V-24, V-25, V-38 on the graph). Needs the graph."""
    cfg = cfg or default_config()
    g = pack.graph
    if g is None:
        return {
            "measured": False,
            "why": "no graph for this pack (a pack folder alone has no node tiers or free-text fields)",
        }
    ctx = make_context(g, pack=pack.pack)
    found = run_all(ctx, only={"V-24", "V-25", "V-38"})
    screens = sorted(g.screens)

    def screen_of(where: str) -> str:
        return where.split(".", 1)[0]

    bad_schema = {
        screen_of(f.where) for f in found if f.code in ("V-24", "V-38") and f.severity == "blocker"
    }
    ceiling = [f for f in found if f.code == "V-24" and "tier above its ceiling" in f.why]
    warns = [f for f in found if f.code == "V-25" and f.severity == "warning"]
    per_card = Counter(screen_of(f.where) for f in warns)
    cap = cfg.checks.freedom_lint_warnings_per_card_max
    return {
        "measured": True,
        "cards": len(screens),
        "schema_valid_cards": len([s for s in screens if s not in bad_schema]),
        "schema_validity": 1.0 - len(bad_schema & set(screens)) / len(screens) if screens else 1.0,
        "tier_ceiling_violations": len(ceiling),
        "lint_warnings_total": len(warns),
        "lint_warnings_max_per_card": max(per_card.values(), default=0),
        "lint_cap_per_card": cap,
        "cards_over_lint_cap": sorted(s for s, n in per_card.items() if n > cap),
        "lint_warnings": [
            {
                "where": f.where,
                "why": f.why,
                "fix": f.fix,
                "explanation": f"V-25: {f.why}",
                "reviewed_by_person": "",
            }
            for f in sorted(warns, key=lambda x: x.where)
        ],
        "schema_findings": [
            {"code": f.code, "where": f.where, "why": f.why}
            for f in found
            if f.code in ("V-24", "V-38") and f.severity == "blocker"
        ],
    }


def owner_cards(pack: ProofPack, n: int = OWNER_CARDS) -> list[dict[str, Any]]:
    """n cards for the owner to read for layout instructions: one per screen type first, heaviest of each, by code."""
    cards = cards_of(pack)
    by_type: dict[str, list[str]] = {}
    for sid, c in cards.items():
        by_type.setdefault(c.screen_type, []).append(sid)
    picks: list[str] = []
    for stype in sorted(
        by_type, key=lambda t: (t in ("auth", "legal", "permission", "onboarding"), t)
    ):
        best = sorted(by_type[stype], key=lambda s: (-cards[s].weight, s))[0]
        picks.append(best)
        if len(picks) >= n:
            break
    return [
        {
            "screen": sid,
            "screen_type": cards[sid].screen_type,
            "card": card_text(pack.files[f"screens/{sid}.md"]),
            "question": "Is there any instruction about where something sits, how it looks or how big it is? Quote it.",
            "owner_finding": "",
        }
        for sid in picks
    ]


# ---------------------------------------------------------------------------------------------------- runner


@dataclass
class FreedomReport:
    pack: str
    pairs: list[ScreenPair]
    stats: dict[str, Any]
    seeds: list[str]
    tally: Tally
    thresholds: dict[str, float]
    arrangements: list[str] = field(default_factory=list)

    def shares(self) -> dict[str, float]:
        return distinct_shares(self.pairs)

    def checks(self) -> list[Check]:
        t = self.thresholds
        st = self.stats
        out = [
            compare(
                "screens with a different arrangement",
                self.shares()["distinct_layout_share"] if self.pairs else None,
                ">=",
                t["freedom_probe_distinct_layout_min"],
            )
        ]
        if st.get("measured"):
            out += [
                compare("schema validity", st["schema_validity"], ">=", 1.0),
                compare("tier ceiling violations", st["tier_ceiling_violations"], "==", 0),
                compare(
                    "lint warnings, max per card",
                    st["lint_warnings_max_per_card"],
                    "<=",
                    st["lint_cap_per_card"],
                ),
            ]
        else:
            out += [Check("schema, tier ceilings and lint", None, "info", None, None)]
        return out

    def would_pass(self) -> bool | None:
        return checks_pass(self.checks())

    def verdict(self, runs: int = 1) -> str:
        return verdict_for(self.would_pass(), live=self.tally.live, runs=runs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": "PS-4",
            "pack": self.pack,
            "style_seeds": self.seeds,
            "screens": [p.screen for p in self.pairs],
            "probe": {
                **self.shares(),
                "per_screen": [
                    {
                        "screen": p.screen,
                        "arrangement_a": p.a.arrangement,
                        "arrangement_b": p.b.arrangement,
                        "order_differs": p.order_differs,
                        "fallback": p.used_fallback,
                    }
                    for p in self.pairs
                ],
            },
            "card_stats": self.stats,
            "checks": [c.as_dict() for c in self.checks()],
            "would_pass": self.would_pass(),
            "verdict": self.verdict(),
            "owner_check": "the owner reads the 5 cards in owner-reading/cards.json and finds no layout instruction (not measured by code)",
            "single_run": True,
            "money": self.tally.as_dict(),
            "note": (
                "single run. "
                + (
                    ""
                    if self.tally.live
                    else "OFFLINE STUB: the mock picks an arrangement from a hash of the style seed; this proves the harness, not the consumer."
                )
            ),
        }


async def run_freedom_probe(
    runner: CallRunner,
    pack: ProofPack,
    size: str = "standard",
    *,
    samples: Sequence[str] | None = None,
    seeds: Sequence[str] | None = None,
    cfg: SpecConfig | None = None,
) -> FreedomReport:
    cfg = cfg or default_config()
    seeds = list(seeds) if seeds else style_seeds(2)
    toks = arrangement_tokens()
    screens = list(samples) if samples is not None else pick_samples(pack, size)
    cards = cards_of(pack)
    start = len(runner.calls)
    pairs: list[ScreenPair] = []
    for sid in screens:
        card = cards[sid]
        keys = sorted(r for r in _region_keys(card.text))
        outs: list[FreedomOut] = []
        fell = False
        for i, seed in enumerate(seeds[:2]):
            inp = FreedomIn(
                screen_id=sid,
                style_seed=seed,
                arrangements=toks,
                card=card.text,
                region_keys=keys,
            )
            res = await runner.run(
                FREEDOM_CALL, inp, node_ids=(f"FREEDOM:{sid}:{i}",), step="proof.freedom"
            )
            outs.append(res.output)
            fell = fell or res.used_fallback
        pairs.append(ScreenPair(sid, outs[0], outs[1], fell))
    return FreedomReport(
        pack=pack.name,
        pairs=pairs,
        stats=card_stats(pack, cfg),
        seeds=list(seeds[:2]),
        tally=Tally.since(runner, start),
        thresholds=thresholds(cfg),
        arrangements=toks,
    )


def _region_keys(card: str) -> list[str]:
    keys: list[str] = []
    sect = ""
    for ln in card.splitlines():
        if ln and not ln.startswith(" "):
            sect = ln.split(":", 1)[0]
        elif sect == "regions" and ln.startswith("  - {key: "):
            keys.append(ln[len("  - {key: ") :].split(",", 1)[0].strip())
    return keys


def write_report(report: FreedomReport, pack: ProofPack, out_dir: Path) -> Path:
    path = write_json(out_dir / "freedom-probe.json", report.as_dict())
    write_json(
        out_dir / "owner-reading" / "cards.json",
        {
            "what": "Five screen cards for the owner (doc 16 PS-4): find any instruction about layout, position, look or size. "
            "A card says WHAT a screen must communicate and do; it must never say where or how.",
            "cards": owner_cards(pack),
        },
    )
    return path
