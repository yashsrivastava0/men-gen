"""Lane decisions: for every decision a Spec lane must make (``decisions`` in ``data/lanes.yaml``), the main-store items that
inform it, or an explicit gap (D-159, doc 13 section 9.5).

Spec reads this table to see, per decision, what Research gave with which evidence, and what it must ASSUME because Research
has nothing usable: a decision with no item of a usable label (``official``, ``corroborated`` or ``single_source``) carries a
``gap`` with a reason, ``unverified_only`` (items exist, none may be used), ``not_found`` (the topics ran and found nothing),
``not_run`` (the topics did not run) or ``not_planned`` (no topic of the plan can inform it). Pure function of the items, the
lane slices and the topic statuses, so a validator recomputes it (RV-27).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.domain.research import DecisionGap, Item, LaneDecisionOut

EVIDENCE_ORDER = {"official": 0, "corroborated": 1, "single_source": 2, "unverified": 3}


def _informs(
    item: Item, kinds: Sequence[str], fields: Sequence[str], flags_any: Sequence[str]
) -> bool:
    if item.kind not in kinds:
        return False
    if fields and item.kind == "provider_fact" and item.data.get("field") not in fields:
        return False
    return not flags_any or bool(set(item.flags) & set(flags_any))


def build_decisions(
    items: list[Item],
    lane_item_ids: Mapping[str, Sequence[str]],
    catalog: Catalog,
    topics: Sequence[tuple[str, str]],
) -> list[LaneDecisionOut]:
    """``lane_item_ids``: main-store item ids per lane (``slices``); ``topics``: ``(job, status)`` of every planned topic."""
    by_id = {i.id: i for i in items}
    notes = catalog.lanes.decision_gaps
    out: list[LaneDecisionOut] = []
    for lane in catalog.lanes.lanes:
        for d in catalog.lanes.decisions.get(lane, ()):
            mine = [
                by_id[iid]
                for iid in lane_item_ids.get(lane, ())
                if iid in by_id and _informs(by_id[iid], d.kinds, d.fields, d.flags_any)
            ]
            usable = [i for i in mine if i.evidence != "unverified"]
            weak = [i for i in mine if i.evidence == "unverified"]
            gap: DecisionGap | None = None
            if not usable:
                if weak:
                    reason = "unverified_only"
                else:
                    jobs = {j for j, spec in catalog.jobs.items() if set(spec.kinds) & set(d.kinds)}
                    states = [s for j, s in topics if j in jobs]
                    reason = (
                        "not_planned"
                        if not states
                        else "not_found"
                        if any(s in ("covered", "not_found") for s in states)
                        else "not_run"
                    )
                gap = DecisionGap(reason=reason, note=notes.get(reason, ""))  # type: ignore[arg-type]
            best = min((i.evidence for i in usable), key=lambda e: EVIDENCE_ORDER[e], default=None)
            out.append(
                LaneDecisionOut(
                    id=d.id,
                    lane=lane,  # type: ignore[arg-type]
                    text=d.text,
                    item_ids=[i.id for i in usable],
                    unverified_item_ids=[i.id for i in weak],
                    best_evidence=best,  # type: ignore[arg-type]
                    gap=gap,
                )
            )
    return out
