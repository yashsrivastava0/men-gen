"""Interview policy: when to follow up, when the user is tired, when to stop, how big the next batch is (build prompt
02, sections 2.2 and 5; ADR 0015). Small pure decisions over plain data; the model never makes any of them.

* ``followup_decision``: a follow-up fires only when ALL hold: no blocking flag (off topic, unsafe, declined category,
  pivot, contradiction: those have their own paths), the need is high-impact, it is ``partial`` or the answer was
  flagged vague (and not already ``satisfied``), no follow-up was used for it yet, its depth is below
  ``followup_max_depth`` and follow-ups so far are below ``ceil(followup_max_fraction * base questions)``.
  A second thin answer is not chased: the result is ``assume`` (the engine shows a visible assumption chip; no nagging).
* ``fatigue``: the last ``fatigue_streak`` answers were all skips, "you decide" or very short typed answers.
* ``stop_decision``: stop when the user said enough, fatigue shows, the budget is reached, or every ``must`` need is
  satisfied or assumed and the last batch added too few facts. The needs left open are returned so the engine can turn
  them into visible assumptions.
* ``next_batch_size``: the first batch is 4 to 5 (quick), 5 to 7 (standard), 6 to 8 (thorough, and every later batch),
  never past the budget.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from math import ceil
from typing import Literal

from mde_compass.config import InterviewBudget, InterviewPolicy, QuestionPolicy
from mde_compass.domain.interview import NeedStatus, QaRecord

FollowupAction = Literal["follow_up", "assume", "none"]
VAGUE_FLAGS: frozenset[str] = frozenset({"vague", "thin", "incomplete"})
BLOCKING_FLAGS: frozenset[str] = frozenset(
    {"off_topic", "unsafe", "declined_category", "pivot", "contradiction"}
)
DEFAULT_HIGH_IMPACT_AT = QuestionPolicy().high_impact_at
_BUDGETS: tuple[InterviewBudget, ...] = ("quick", "standard", "thorough")


# --------------------------------------------------------------------------------------------- follow-up
@dataclass(frozen=True)
class FollowupDecision:
    action: FollowupAction
    reason: str


def followup_cap(base_asked_total: int, policy: InterviewPolicy) -> int:
    """Most follow-ups allowed for ``base_asked_total`` base questions: ``ceil(fraction * base)`` (rounded first so
    floating point noise never adds one: 0.4 * 15 is 6.000000000000001)."""
    return ceil(round(policy.followup_max_fraction * max(0, base_asked_total), 9))


def followup_decision(
    need_status: NeedStatus,
    completeness_flags: Collection[str],
    followups_used_for_need: int,
    followups_total: int,
    base_asked_total: int,
    policy: InterviewPolicy,
    *,
    high_impact_at: int = DEFAULT_HIGH_IMPACT_AT,
    depth: int = 0,
) -> FollowupDecision:
    """One decision per answered question. ``depth`` is the follow-up depth of the question just answered (0 for a
    base question); ``high_impact_at`` is ``QuestionPolicy.high_impact_at``."""
    flags = {f.strip().casefold() for f in completeness_flags}
    blocking = sorted(flags & BLOCKING_FLAGS)
    if blocking:
        return FollowupDecision("none", f"flag {blocking[0]}: handled by its own path")
    vague = bool(flags & VAGUE_FLAGS)
    if need_status.state == "satisfied":
        return FollowupDecision("none", "the need is satisfied")
    if not (vague or need_status.state == "partial"):
        return FollowupDecision("none", "the answer is specific")
    if need_status.impact < high_impact_at:
        return FollowupDecision("none", "low-impact need: take what we have")
    if followups_used_for_need >= 1:
        return FollowupDecision(
            "assume", "still thin after a follow-up: assume visibly, do not nag"
        )
    if depth >= policy.followup_max_depth:
        return FollowupDecision("assume", "follow-up depth reached: assume visibly")
    if followups_total >= followup_cap(base_asked_total, policy):
        return FollowupDecision("none", "follow-up budget used")
    return FollowupDecision("follow_up", "high-impact need is partial or the answer was vague")


# ------------------------------------------------------------------------------------------------ fatigue
@dataclass(frozen=True)
class AnswerSignal:
    """What fatigue needs to know about one answer."""

    kind: Literal["answered", "skipped", "you_decide"]
    chars: int = 0  # length of the typed answer
    tapped: bool = False  # chose an option: never a "short answer"

    @classmethod
    def from_qa(cls, qa: QaRecord) -> AnswerSignal | None:
        """``None`` for records that are not the user's answer (open, assumed, dropped, superseded)."""
        if qa.status in ("skipped", "you_decide"):
            return cls(qa.status)
        if qa.status != "answered":
            return None
        text = (qa.custom_text or "").strip()
        if not text and isinstance(qa.answer, str) and qa.input_mode != "tap":
            text = qa.answer.strip()
        return cls("answered", chars=len(text), tapped=qa.input_mode == "tap" and not text)


def _tired(signal: AnswerSignal, short_chars: int) -> bool:
    if signal.kind in ("skipped", "you_decide"):
        return True
    return not signal.tapped and signal.chars <= short_chars


def fatigue(history: Sequence[AnswerSignal | QaRecord], policy: InterviewPolicy) -> bool:
    """True when the last ``fatigue_streak`` answers were all skips, "you decide" or very short typed answers."""
    signals = [
        s
        for s in (h if isinstance(h, AnswerSignal) else AnswerSignal.from_qa(h) for h in history)
        if s is not None
    ]
    streak = policy.fatigue_streak
    if streak <= 0 or len(signals) < streak:
        return False
    return all(_tired(s, policy.fatigue_short_chars) for s in signals[-streak:])


# -------------------------------------------------------------------------------------------------- stop
@dataclass(frozen=True)
class StopDecision:
    stop: bool
    reason: str  # user_enough | fatigue | budget_reached | coverage_reached | continue
    remaining_needs_to_assume: tuple[str, ...] = ()


def _budget(name: str) -> InterviewBudget:
    for budget in _BUDGETS:
        if budget == name:
            return budget
    return "standard"


def _remaining(statuses: Mapping[str, NeedStatus]) -> tuple[str, ...]:
    """Open needs (missing or partial, never ``generated``) in order of impact, highest first."""
    open_needs = [
        (i, s)
        for i, s in enumerate(statuses.values())
        if s.level != "generated" and s.state in ("missing", "partial")
    ]
    open_needs.sort(key=lambda pair: (-pair[1].impact, pair[0]))
    return tuple(s.need_id for _, s in open_needs)


def stop_decision(
    *,
    statuses: Mapping[str, NeedStatus],
    budget_name: str,
    asked_total: int,
    last_batch_new_facts: int,
    user_said_enough: bool,
    fatigue_flag: bool,
    policy: InterviewPolicy,
    room: Room | None = None,
) -> StopDecision:
    """Whether to stop asking. Order: the user said enough, fatigue, budget reached, coverage reached.

    ``last_batch_new_facts`` is how many facts the last batch added (pass a large number before any batch has been
    answered). Coverage is reached when every ``must`` need is satisfied or visibly assumed and that number is below
    ``policy.marginal_new_facts_min``. When it stops, the needs still open come back in
    ``remaining_needs_to_assume`` (highest impact first) so the engine can make them visible assumptions.
    """
    reason = ""
    if user_said_enough:
        reason = "user_enough"
    elif fatigue_flag:
        reason = "fatigue"
    elif (
        room.closing_due
        if room is not None
        else asked_total >= policy.budget_max(_budget(budget_name))
    ):
        reason = "budget_reached"  # with a Room: every base place is committed, only the closing question is left
    else:
        musts = [s for s in statuses.values() if s.level == "must"]
        done = all(s.state in ("satisfied", "assumed") for s in musts)
        if done and last_batch_new_facts < policy.marginal_new_facts_min:
            reason = "coverage_reached"
    if not reason:
        return StopDecision(False, "continue")
    return StopDecision(True, reason, _remaining(statuses))


# ------------------------------------------------------------------------------------------------- room
@dataclass(frozen=True)
class Room:
    """What is left of a tier's question budget (ADR 0016): the one place the arithmetic lives.

    ``target`` is the tier's number (7, 12 or 25) and counts the closing question; ``hard_cap`` is the most the person
    is ever asked. Questions are counted when they are PLANNED (asked plus waiting), so a batch that is still on screen
    already uses its places. Base questions stop at ``target - 1``: one place is always kept for the closing question.
    Follow-ups use only the slack (``hard_cap - target``) and never take a base place, and base plus follow-ups never
    pass ``hard_cap - 1``. Once the closing question is committed nothing else may be added: it is always last."""

    target: int
    hard_cap: int
    base: int = 0  # planner, fallback and frame questions committed so far
    followups: int = 0  # follow-ups committed so far
    closing_committed: bool = False

    @property
    def committed(self) -> int:
        return self.base + self.followups + (1 if self.closing_committed else 0)

    @property
    def base_room(self) -> int:
        """Base questions the planner may still add (the closing place is never offered)."""
        if self.closing_committed:
            return 0
        return max(0, self.target - 1 - self.base)

    @property
    def followup_room(self) -> int:
        """Follow-ups that may still be added: the slack above the target, and never past ``hard_cap - 1`` overall."""
        if self.closing_committed:
            return 0
        slack = max(0, self.hard_cap - self.target - self.followups)
        return max(0, min(slack, self.hard_cap - 1 - self.base - self.followups))

    @property
    def closing_due(self) -> bool:
        """Only the closing question is left to ask (it is asked once and nothing follows it)."""
        return not self.closing_committed and self.base_room == 0

    def batch_size(self, wanted: int) -> int:
        """How many base questions to ask the planner for: ``wanted``, never past the room."""
        return max(0, min(wanted, self.base_room))

    def add(self, *, base: int = 0, followups: int = 0, closing: bool = False) -> Room:
        """The room after committing more questions (a copy; ``Room`` is immutable)."""
        return Room(
            self.target,
            self.hard_cap,
            self.base + base,
            self.followups + followups,
            self.closing_committed or closing,
        )

    @classmethod
    def of(cls, qa: Sequence[QaRecord], *, target: int, hard_cap: int) -> Room:
        """Count what the ledger already holds: every question that was planned and neither dropped nor superseded."""
        base = followups = 0
        closing = False
        for q in qa:
            if q.status in ("dropped", "superseded"):
                continue
            origin = q.card.origin
            if origin == "closing":
                closing = True
            elif origin == "followup":
                followups += 1
            else:
                base += 1
        return cls(target, hard_cap, base, followups, closing)


# --------------------------------------------------------------------------------------------- batch size
def batch_size_range(
    budget_name: str, asked_total: int, policy: InterviewPolicy
) -> tuple[int, int]:
    """(min, max) questions for the next batch: the budget's batch range, clipped to what is left of the budget.
    (0, 0) when the budget is spent."""
    budget = _budget(budget_name)
    low, high = {
        "quick": policy.quick_first_batch,
        "standard": policy.standard_first_batch,
        "thorough": policy.thorough_batch,
    }[budget]
    left = policy.budget_max(budget) - max(0, asked_total)
    if left <= 0:
        return (0, 0)
    high = min(high, left)
    return (min(low, high), high)


def batch_range(budget_name: str, policy: InterviewPolicy) -> tuple[int, int]:
    """(min, max) questions of one batch of this tier (3 / 4 / 5), before the room clips it (ADR 0016)."""
    return {
        "quick": policy.quick_first_batch,
        "standard": policy.standard_first_batch,
        "thorough": policy.thorough_batch,
    }[_budget(budget_name)]


def next_batch_size(budget_name: str, asked_total: int, policy: InterviewPolicy) -> int:
    """How many questions to ask the planner for next: the top of the batch range, never past the budget."""
    return batch_size_range(budget_name, asked_total, policy)[1]
