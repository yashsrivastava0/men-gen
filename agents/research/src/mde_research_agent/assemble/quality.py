"""Lane quality, builder-need coverage and flag counts, computed by CODE from the checked items (doc 13 9.4).

Spec and Verify read these instead of re-deriving trust:

* ``lane_quality`` per lane: ``strong`` (80% or more of the lane's required slots filled AND 60% or more of its items
  official or corroborated), ``partial`` (50% or more of the slots filled), ``thin`` (some items, under 50% of the slots)
  or ``none`` (no item). Thresholds live in ``data/quality.yaml``.
* ``bn_coverage`` per builder need: ``researched`` (an official or corroborated item maps to the need),
  ``single_source_only``, ``defaulted_only`` (no item and nothing was tried: Spec proposes the module default) or
  ``gap`` (the need had topics and every one was not found or not run).
* ``flagged``: how many items carry each trust flag.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from mde_research_agent.calls.extract import JOB_KINDS
from mde_research_agent.catalog.loader import DATA_DIR
from mde_research_agent.catalog.models import LanesConfig
from mde_research_agent.domain.research import BnValue, Item, LaneQuality, LaneQualityValue


@dataclass(frozen=True)
class TargetInfo:
    """What lane quality and builder-need coverage need to know about a topic (also read back from the evidence file)."""

    job: str
    lane: str
    status: str
    slots_required: int
    slots_filled: int


COUNTED_FLAGS = (
    "partial_support",
    "unchecked",
    "other_region",
    "other_entity",
    "duplicate_source",
    "number_widened",
    "single-source",
    "conflict",
    "stale",
)


@lru_cache(maxsize=1)
def thresholds() -> dict[str, float]:
    data = yaml.safe_load((DATA_DIR / "quality.yaml").read_text(encoding="utf-8")) or {}
    return {k: float(v) for k, v in data.get("lane_quality", {}).items()}


def _quality(required: int, filled: int, items: int, strong: int) -> LaneQualityValue:
    th = thresholds()
    if items == 0:
        return "none"
    share_slots = filled / required if required else (1.0 if items else 0.0)
    share_strong = strong / items
    if share_slots >= th["strong_slots"] and share_strong >= th["strong_items"]:
        return "strong"
    if share_slots >= th["partial_slots"]:
        return "partial"
    return "thin"


def lane_quality(
    items_by_lane: dict[str, list[Item]], targets: list[TargetInfo], lanes: list[str]
) -> dict[str, LaneQuality]:
    req: Counter[str] = Counter()
    fil: Counter[str] = Counter()
    for ts in targets:
        req[ts.lane] += ts.slots_required
        fil[ts.lane] += ts.slots_filled
    out: dict[str, LaneQuality] = {}
    for lane in lanes:
        items = items_by_lane.get(lane, [])
        strong = sum(1 for i in items if i.evidence in ("official", "corroborated"))
        out[lane] = LaneQuality(
            quality=_quality(req[lane], fil[lane], len(items), strong),
            slots_required=req[lane],
            slots_filled=fil[lane],
            items=len(items),
            strong_share=round(strong / len(items), 3) if items else 0.0,
        )
    return out


def bn_coverage(
    items: list[Item], targets: list[TargetInfo], cfg: LanesConfig
) -> dict[str, BnValue]:
    needs = sorted({k.need for k in cfg.kinds.values()})
    by_need: dict[str, list[Item]] = {n: [] for n in needs}
    for i in items:
        by_need[cfg.kinds[i.kind].need].append(i)
    job_needs = {
        job: {cfg.kinds[k].need for k in kinds if k in cfg.kinds}
        for job, kinds in JOB_KINDS.items()
    }
    out: dict[str, BnValue] = {}
    for n in needs:
        found = by_need[n]
        if any(i.evidence in ("official", "corroborated") for i in found):
            out[n] = "researched"
        elif found and all(i.evidence != "unverified" for i in found):
            out[n] = "single_source_only"
        elif found:
            out[n] = (
                "single_source_only"  # only unverified or single-source evidence: Spec must not default from it
            )
        else:
            tried = [ts for ts in targets if n in job_needs.get(ts.job, set())]
            out[n] = (
                "gap"
                if tried
                and all(
                    ts.status in ("not_found", "queued", "cancelled", "skipped") for ts in tried
                )
                else "defaulted_only"
            )
    return out


def flagged_counts(items: list[Item]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for i in items:
        for f in i.flags:
            if f in COUNTED_FLAGS:
                c[f] += 1
    return {k: c[k] for k in COUNTED_FLAGS if c[k]}


def lane_items(
    items: list[Item], lanes_cfg: LanesConfig, lane_of_target: dict[str, list[str]]
) -> dict[str, list[Item]]:
    """Main-store items by lane (a kind can feed several lanes; the topic's own lanes count too)."""
    out: dict[str, list[Item]] = {lane: [] for lane in lanes_cfg.lanes}
    for i in items:
        if i.store != "main":
            continue
        for lane in dict.fromkeys(
            [*lanes_cfg.kinds[i.kind].lanes, *lane_of_target.get(i.target_id, [])]
        ):
            if lane in out:
                out[lane].append(i)
    return out


def as_dict(q: dict[str, LaneQuality]) -> dict[str, Any]:
    return {k: v.model_dump() for k, v in q.items()}
