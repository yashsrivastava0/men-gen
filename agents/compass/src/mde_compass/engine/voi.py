"""Value-of-information question planner: which card comes next (prompt 6.3). Pure function, config weights.

For every slot that still needs a decision the planner scores

    score = w_impact * impact + w_risk * risk + (-w_inferability * 5 * inferability) - w_effort * effort

with impact 1..5 from the catalog, risk low/medium/high -> 1/3/5, effort tap/text/upload -> 1/3/5 and
inferability 0..1 (a model or pattern confidence for a proposed value; by default 0.8 when the catalog has a
pattern default, 0.5 for pattern-fillable, 0.4 for inferable, else 0.1). The action follows the thresholds in
:class:`~mde_compass.config.QuestionPolicy`:

* ``ask``     high impact and low inferability, or a risky slot (``must_confirm``: "You decide" and Skip refused);
* ``propose`` high impact and high inferability: show the value and ask for a yes or a correction;
* ``assume``  low impact (or over the budget, or not worth a question): the engine assumes and lists it.

Order follows dependency (the catalog frame order); the score only decides WHICH items survive the budget:
express at most 5 asks, standard about 7 and never more than ``hard_cap`` 10 (only required slots may go past
the target), large per module. A slot that is ``confirmed`` or ``volunteered`` is never re-asked, a frame
already asked is never asked twice, ``ask_if`` preconditions are evaluated with ``engine.expr.holds`` on a
projection in which earlier planned items count as filled, so the queue also gives an honest total.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from mde_compass.catalog.loader import Catalog, FrameDef, SlotDef
from mde_compass.config import QuestionPolicy
from mde_compass.domain.situation import DepthProfile, SituationProfile
from mde_compass.domain.slots import SlotRecord
from mde_compass.engine.expr import holds

Action = Literal["ask", "propose", "assume"]
RISK_SCORE = {"low": 1, "medium": 3, "high": 5}
EFFORT_SCORE = {"tap": 1, "text": 3, "upload": 5}
SETTLED = frozenset({"confirmed", "volunteered"})
PROJECTED_VALUE = "__planned__"
# "Just build it": still ask what it is, for whom, where (country, city, languages) and which apps.
JUST_BUILD_SLOTS: frozenset[str] = frozenset(
    {"idea.summary", "idea.category", "audience.segments", "context.geo", "locales", "apps.list"}
)
ENTERPRISE_GROUPS: frozenset[str] = frozenset({"org"})
_ACTION_RANK = {"ask": 2, "propose": 1, "assume": 0}


@dataclass(frozen=True)
class QueueItem:
    """One planned step: ask (or propose) a card for ``slots`` of ``frame_id``, or assume them."""

    frame_id: str
    slots: list[str]
    action: Action
    score: float
    must_confirm: bool
    reason: str


@dataclass(frozen=True)
class _Cand:
    slot: SlotDef
    frame: FrameDef
    action: Action
    score: float
    reason: str


def default_inferability(slot: SlotDef) -> float:
    """How inferable a value is without asking: pattern default 0.8, pattern-fillable 0.5, inferable 0.4."""
    if slot.default is not None:
        return 0.8
    if "P" in slot.filled_by:
        return 0.5
    if "I" in slot.filled_by:
        return 0.4
    return 0.1


def slot_score(slot: SlotDef, inferability: float, config: QuestionPolicy) -> float:
    """The value-of-information score (higher means more worth a question)."""
    inf = min(max(inferability, 0.0), 1.0)
    return round(
        config.w_impact * slot.impact
        + config.w_risk * RISK_SCORE[slot.risk]
        - config.w_inferability * 5.0 * inf
        - config.w_effort * EFFORT_SCORE[slot.effort],
        4,
    )


def _is_risky(slot: SlotDef) -> bool:
    return slot.must_confirm or slot.risk == "high"


def _classify(slot: SlotDef, inferability: float, config: QuestionPolicy) -> tuple[Action, str]:
    if _is_risky(slot):
        return "ask", "risky: ask and must confirm"
    if slot.level == "may":
        return "assume", "optional: assume and list"
    if slot.impact >= config.high_impact_at:
        if inferability >= config.high_inferability_at:
            return "propose", "high impact, easy to infer: propose and confirm"
        return "ask", "high impact, hard to infer: ask"
    if slot.impact >= 3 and inferability < config.high_inferability_at:
        return "ask", "medium impact, hard to infer: ask if the budget allows"
    return "assume", "low impact or easy to infer: assume and list"


def _depth_caps(depth: DepthProfile, config: QuestionPolicy) -> tuple[int | None, int | None]:
    """(hard, soft) number of questions in total; None means per-module limits instead."""
    if depth == "express":
        return config.express_max, config.express_max
    if depth == "standard":
        return config.hard_cap, config.standard_target
    return None, None


def _projected(real: Mapping[str, SlotRecord | None], slot_id: str) -> SlotRecord:
    existing = real.get(slot_id)
    if existing is not None and existing.status != "empty":
        return existing
    return SlotRecord(slot_id=slot_id, value=PROJECTED_VALUE, status="assumed", confidence=0.5)


def _settled(record: SlotRecord | None) -> bool:
    return record is not None and record.status in SETTLED


def _frames_by_slot(catalog: Catalog) -> dict[str, FrameDef]:
    out: dict[str, FrameDef] = {}
    for frame in sorted(catalog.frames.values(), key=lambda f: f.order):
        for slot_id in frame.slots:
            out.setdefault(slot_id, frame)
    return out


def _candidates(
    slots: Mapping[str, SlotRecord | None],
    catalog: Catalog,
    *,
    just_build: bool,
    mode: str,
    asked: set[str],
    inferability: Mapping[str, float],
    config: QuestionPolicy,
) -> list[_Cand]:
    frame_of = _frames_by_slot(catalog)
    projected: dict[str, SlotRecord | None] = dict(slots)
    out: list[_Cand] = []
    ordered = sorted(
        frame_of.items(), key=lambda kv: (kv[1].order, catalog.slots[kv[0]].order, kv[0])
    )
    for slot_id, frame in ordered:
        slot = catalog.slots[slot_id]
        if slot.level == "generated":
            continue
        if slot.group in ENTERPRISE_GROUPS and mode != "enterprise":
            continue
        record = slots.get(slot_id)
        if _settled(record):
            continue
        if slot.ask_if is not None and not holds(slot.ask_if, projected):
            continue
        inf = inferability.get(slot_id)
        if inf is None:
            inf = (
                record.confidence
                if record is not None and record.status == "assumed" and record.value is not None
                else default_inferability(slot)
            )
        action, reason = _classify(slot, inf, config)
        if just_build and slot_id not in JUST_BUILD_SLOTS:
            action, reason = "assume", "just build it: assume and list"
        elif (
            record is not None
            and record.status == "assumed"
            and slot.impact < config.high_impact_at
        ):
            action, reason = "assume", "already assumed: stays in the assumption review"
        if frame.id in asked and action != "assume":
            action, reason = "assume", "frame already asked: not asked twice"
        out.append(_Cand(slot, frame, action, slot_score(slot, inf, config), reason))
        projected[slot_id] = _projected(slots, slot_id)
    return out


def _priority(group: list[_Cand]) -> int:
    """0: a required slot (level must); 1: a required but risky slot (may go past the target, up to the hard
    cap); 2: everything else (competes for what is left of the target)."""
    must = [c for c in group if c.slot.level == "must"]
    if not must:
        return 2
    return 1 if any(_is_risky(c.slot) for c in must) else 0


def _apply_budget(
    cands: list[_Cand], depth: DepthProfile, asked_count: int, config: QuestionPolicy
) -> list[_Cand]:
    """Demote asks that do not fit the budget to ``assume``.

    Frames are taken by priority (required, then required-and-risky, then the rest), best score first, while
    the total (questions already asked included) stays under the target: express 5, standard about 7 with
    risky required slots allowed up to ``hard_cap``, large ``large_per_module`` per module.
    """
    hard, soft = _depth_caps(depth, config)
    by_frame: dict[str, list[_Cand]] = {}
    for cand in cands:
        if cand.action != "assume":
            by_frame.setdefault(cand.frame.id, []).append(cand)
    frames = sorted(
        by_frame,
        key=lambda fid: (
            _priority(by_frame[fid]),
            -max(c.score for c in by_frame[fid]),
            by_frame[fid][0].frame.order,
        ),
    )
    kept: set[str] = set()
    used = asked_count
    per_module: dict[int, int] = {}
    for fid in frames:
        group = by_frame[fid]
        if hard is None or soft is None:
            module = min(c.slot.order for c in group)
            if per_module.get(module, 0) < config.large_per_module:
                per_module[module] = per_module.get(module, 0) + 1
                kept.add(fid)
            continue
        limit = hard if _priority(group) == 1 else soft
        if used < limit:
            used += 1
            kept.add(fid)
    out: list[_Cand] = []
    for cand in cands:
        if cand.action != "assume" and cand.frame.id not in kept:
            reason = "over the question budget: assume and list"
            cand = _Cand(cand.slot, cand.frame, "assume", cand.score, reason)
        out.append(cand)
    return out


def _items(cands: list[_Cand]) -> list[QueueItem]:
    """One ask/propose item per frame, plus one assume item per frame for the rest; dependency order."""
    order: list[str] = []
    grouped: dict[tuple[str, bool], list[_Cand]] = {}
    for cand in cands:
        if cand.frame.id not in order:
            order.append(cand.frame.id)
        grouped.setdefault((cand.frame.id, cand.action != "assume"), []).append(cand)
    items: list[QueueItem] = []
    for fid in sorted(order, key=lambda f: next(c.frame.order for c in cands if c.frame.id == f)):
        for needs_card in (True, False):
            group = grouped.get((fid, needs_card))
            if not group:
                continue
            action: Action = max((c.action for c in group), key=lambda a: _ACTION_RANK[a])
            lead = max(group, key=lambda c: c.score)
            items.append(
                QueueItem(
                    frame_id=fid,
                    slots=[c.slot.id for c in group],
                    action=action,
                    score=max(c.score for c in group),
                    must_confirm=needs_card and any(_is_risky(c.slot) for c in group),
                    reason=lead.reason,
                )
            )
    return items


def plan_queue(
    slots: Mapping[str, SlotRecord | None],
    catalog: Catalog,
    *,
    profile: SituationProfile | None,
    depth: DepthProfile,
    asked_frames: Sequence[str],
    inferability: Mapping[str, float] | None = None,
    config: QuestionPolicy | None = None,
    mode: str = "guided",
) -> list[QueueItem]:
    """The ordered queue of ask/propose/assume items for the current slot state (pure, recomputed per turn)."""
    cfg = config or QuestionPolicy()
    just_build = profile is not None and profile.urgency == "just_build"
    asked = set(asked_frames)
    cands = _candidates(
        slots,
        catalog,
        just_build=just_build,
        mode=mode,
        asked=asked,
        inferability=inferability or {},
        config=cfg,
    )
    cands = _apply_budget(cands, depth, len(asked), cfg)
    return _items(cands)


def next_item(queue: Sequence[QueueItem]) -> QueueItem | None:
    """The next item that needs a card (``ask`` or ``propose``); ``assume`` items never become cards."""
    for item in queue:
        if item.action != "assume":
            return item
    return None


def estimate_total(queue: Sequence[QueueItem], asked_frames: Sequence[str]) -> int:
    """The N of "Question n of about N": questions asked so far plus the cards still planned (at least 1)."""
    pending = sum(
        1 for item in queue if item.action != "assume" and item.frame_id not in asked_frames
    )
    return max(len(set(asked_frames)) + pending, 1)
