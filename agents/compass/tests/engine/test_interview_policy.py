from __future__ import annotations

from dataclasses import dataclass

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.config import InterviewPolicy
from mde_compass.domain.cards import Card
from mde_compass.domain.interview import NeedState, NeedStatus, QaRecord
from mde_compass.engine.interview_policy import (
    AnswerSignal,
    StopDecision,
    batch_size_range,
    fatigue,
    followup_cap,
    followup_decision,
    next_batch_size,
    stop_decision,
)

POLICY = InterviewPolicy()


def need(
    state: NeedState = "partial",
    impact: int = 5,
    level: str = "must",
    need_id: str = "audience.segments",
) -> NeedStatus:
    return NeedStatus(need_id=need_id, state=state, impact=impact, level=level)


# ------------------------------------------------------------------------------------------- follow-up
@dataclass(frozen=True)
class Case:
    name: str
    kind: str  # "vague" or "specific"
    state: NeedState
    impact: int
    flags: tuple[str, ...]
    used: int = 0
    total: int = 0
    base: int = 5


_OPEN_STATES: tuple[NeedState, ...] = ("partial", "missing")
VAGUE_CASES = [
    Case(
        f"{state}-{impact}-{'+'.join(flags) or 'noflag'}",
        "vague",
        state,
        impact,
        flags,
        0,
        total,
        base,
    )
    for state in _OPEN_STATES
    for impact in (4, 5)
    for flags in (("vague",), ("vague", "new_topic"), ("thin",), ("incomplete",))
    for total, base in ((0, 5), (1, 8), (2, 12))
] + [
    Case("partial-no-flag", "vague", "partial", impact, (), 0, 0, 6) for impact in (4, 5)
]  # partial against the answer_spec counts as vague even when the interpreter raised no flag

_SPECIFIC_ANSWERS: list[tuple[NeedState, tuple[str, ...]]] = [
    ("satisfied", ()),
    ("satisfied", ("new_topic",)),
    ("satisfied", ("vague",)),  # the code says every aspect is covered: code wins over the flag
    ("missing", ()),  # nothing was said (a skip): not a follow-up
    ("assumed", ()),
]
SPECIFIC_CASES = [
    Case(
        f"{state}-{impact}-{'+'.join(flags) or 'noflag'}", "specific", state, impact, flags, 0, 0, 6
    )
    for state, flags in _SPECIFIC_ANSWERS
    for impact in (4, 5)
]


def _decide(c: Case) -> str:
    return followup_decision(
        need(c.state, c.impact), c.flags, c.used, c.total, c.base, POLICY
    ).action


def test_followup_fires_on_at_least_90_percent_of_vague_cases_and_on_no_specific_case() -> None:
    fired = [_decide(c) == "follow_up" for c in VAGUE_CASES]
    assert len(VAGUE_CASES) >= 40
    assert sum(fired) / len(fired) >= 0.9
    assert not any(_decide(c) == "follow_up" for c in SPECIFIC_CASES)
    assert all(c.kind == "vague" for c in VAGUE_CASES) and all(
        c.kind == "specific" for c in SPECIFIC_CASES
    )


@pytest.mark.parametrize("case", VAGUE_CASES, ids=lambda c: c.name)
def test_every_vague_high_impact_first_answer_gets_a_follow_up(case: Case) -> None:
    assert _decide(case) == "follow_up"


def test_second_thin_answer_is_assumed_not_chased() -> None:
    d = followup_decision(need("partial"), ["vague"], 1, 1, 5, POLICY)
    assert d.action == "assume" and "do not nag" in d.reason
    done = followup_decision(need("satisfied"), [], 1, 1, 5, POLICY)
    assert done.action == "none"  # the follow-up fixed it


def test_depth_cap() -> None:
    assert followup_decision(need(), ["vague"], 0, 0, 5, POLICY, depth=1).action == "follow_up"
    assert followup_decision(need(), ["vague"], 0, 0, 5, POLICY, depth=2).action == "assume"
    tight = InterviewPolicy(followup_max_depth=1)
    assert followup_decision(need(), ["vague"], 0, 0, 5, tight, depth=1).action == "assume"


def test_total_followup_cap_is_ceil_of_the_fraction_of_base_questions() -> None:
    assert [followup_cap(b, POLICY) for b in (0, 1, 2, 5, 10, 15, 25)] == [0, 1, 1, 2, 4, 6, 10]
    # 5 base questions allow 2 follow-ups
    assert followup_decision(need(), ["vague"], 0, 1, 5, POLICY).action == "follow_up"
    assert followup_decision(need(), ["vague"], 0, 2, 5, POLICY).action == "none"
    assert followup_decision(need(), ["vague"], 0, 0, 0, POLICY).action == "none"
    assert (
        followup_decision(need(), ["vague"], 0, 5, 15, POLICY).action == "follow_up"
    )  # 6 allowed, floats are safe
    assert followup_decision(need(), ["vague"], 0, 6, 15, POLICY).action == "none"


def test_low_impact_needs_are_not_chased() -> None:
    assert followup_decision(need(impact=3), ["vague"], 0, 0, 5, POLICY).action == "none"
    assert (
        followup_decision(need(impact=3), ["vague"], 0, 0, 5, POLICY, high_impact_at=3).action
        == "follow_up"
    )
    assert (
        followup_decision(need(impact=4), ["vague"], 0, 0, 5, POLICY, high_impact_at=5).action
        == "none"
    )


@pytest.mark.parametrize(
    "flag", ["off_topic", "unsafe", "declined_category", "pivot", "contradiction"]
)
def test_blocking_flags_never_follow_up(flag: str) -> None:
    d = followup_decision(need(), ["vague", flag], 0, 0, 5, POLICY)
    assert d.action == "none" and flag in d.reason


def test_flags_are_case_insensitive() -> None:
    assert followup_decision(need("missing"), [" VAGUE "], 0, 0, 5, POLICY).action == "follow_up"


# ------------------------------------------------------------------------------------------- fatigue
def sig(kind: str, chars: int = 0, tapped: bool = False) -> AnswerSignal:
    return AnswerSignal(kind, chars, tapped)  # type: ignore[arg-type]


def test_fatigue_needs_a_streak_at_the_end() -> None:
    assert fatigue([sig("skipped"), sig("you_decide")], POLICY)
    assert fatigue([sig("answered", 40), sig("skipped"), sig("skipped")], POLICY)
    assert not fatigue([sig("skipped")], POLICY)  # fewer than the streak
    assert not fatigue([sig("skipped"), sig("answered", 40)], POLICY)  # broken by a real answer
    assert not fatigue([], POLICY)


def test_very_short_typed_answers_count_but_taps_do_not() -> None:
    assert fatigue([sig("answered", 2), sig("answered", 1)], POLICY)
    assert not fatigue([sig("answered", 3), sig("answered", 2)], POLICY)
    assert not fatigue([sig("answered", 0, tapped=True), sig("answered", 0, tapped=True)], POLICY)
    assert fatigue([sig("skipped"), sig("answered", 2)], POLICY)


def test_streak_length_comes_from_the_policy() -> None:
    three = InterviewPolicy(fatigue_streak=3)
    assert not fatigue([sig("skipped"), sig("skipped")], three)
    assert fatigue([sig("skipped")] * 3, three)
    assert not fatigue([sig("skipped")] * 5, InterviewPolicy(fatigue_streak=0))


def _qa(
    n: int, status: str, answer: object = None, custom: str | None = None, mode: str = "text"
) -> QaRecord:
    card = Card(id=f"Q-{n:02d}", frame="planner", slots=[], type="open_prompt", question="Q?")
    return QaRecord.model_validate(
        {
            "card_id": card.id,
            "card": card.model_dump(),
            "status": status,
            "answer": answer,
            "custom_text": custom,
            "input_mode": mode,
        }
    )


def test_fatigue_reads_qa_records_and_ignores_non_answers() -> None:
    history = [
        _qa(1, "answered", "a long and useful answer about riders"),
        _qa(2, "skipped"),
        _qa(3, "assumed"),  # the system's own assumption: not the user's fatigue
        _qa(4, "dropped"),
        _qa(5, "you_decide"),
    ]
    assert fatigue(history, POLICY)
    assert not fatigue(history[:2], POLICY)
    taps = [_qa(1, "answered", "a", mode="tap"), _qa(2, "answered", "b", mode="tap")]
    assert not fatigue(taps, POLICY)
    shorts = [_qa(1, "answered", "ok"), _qa(2, "answered", None, custom="no")]
    assert fatigue(shorts, POLICY)


# ---------------------------------------------------------------------------------------------- stop
def statuses(**states: NeedState) -> dict[str, NeedStatus]:
    levels = {"a": "must", "b": "must", "c": "should", "d": "may", "g": "generated"}
    return {k: need(v, 5 if levels[k] == "must" else 2, levels[k], k) for k, v in states.items()}


def stop(**kw: object) -> StopDecision:
    base: dict[str, object] = {
        "statuses": statuses(a="missing", b="partial", c="missing"),
        "budget_name": "standard",
        "asked_total": 3,
        "last_batch_new_facts": 5,
        "user_said_enough": False,
        "fatigue_flag": False,
        "policy": POLICY,
    }
    base.update(kw)
    return stop_decision(**base)  # type: ignore[arg-type]


def test_keeps_going_while_needs_are_open() -> None:
    d = stop()
    assert d == stop_decision(
        statuses=statuses(a="missing", b="partial", c="missing"),
        budget_name="standard",
        asked_total=3,
        last_batch_new_facts=5,
        user_said_enough=False,
        fatigue_flag=False,
        policy=POLICY,
    )
    assert d.stop is False and d.reason == "continue" and d.remaining_needs_to_assume == ()


def test_user_enough_and_fatigue_stop_and_list_what_to_assume() -> None:
    d = stop(user_said_enough=True)
    assert d.stop and d.reason == "user_enough"
    assert d.remaining_needs_to_assume == ("a", "b", "c")  # impact first, then order
    f = stop(fatigue_flag=True)
    assert f.stop and f.reason == "fatigue"
    both = stop(user_said_enough=True, fatigue_flag=True)
    assert both.reason == "user_enough"


def test_budget_reached_per_budget() -> None:
    for name, limit in (("quick", 5), ("standard", 12), ("thorough", 25)):
        assert not stop(budget_name=name, asked_total=limit - 1).stop
        d = stop(budget_name=name, asked_total=limit)
        assert d.stop and d.reason == "budget_reached"
    assert stop(budget_name="nonsense", asked_total=12).stop  # unknown names behave as standard


def test_budget_stop_with_unmet_must_needs_lists_them_for_assumption() -> None:
    d = stop(asked_total=12)
    assert d.remaining_needs_to_assume == ("a", "b", "c")


def test_coverage_reached_needs_all_musts_done_and_low_marginal_value() -> None:
    done = statuses(a="satisfied", b="assumed", c="missing", d="partial", g="missing")
    d = stop(statuses=done, last_batch_new_facts=0)
    assert d.stop and d.reason == "coverage_reached"
    assert d.remaining_needs_to_assume == ("c", "d")  # generated needs are never listed
    assert not stop(statuses=done, last_batch_new_facts=3).stop  # still learning
    assert not stop(statuses=statuses(a="satisfied", b="partial"), last_batch_new_facts=0).stop
    strict = InterviewPolicy(marginal_new_facts_min=3)
    assert stop(statuses=done, last_batch_new_facts=2, policy=strict).stop


def test_no_musts_means_coverage_is_trivially_reached() -> None:
    only_should = statuses(c="missing", d="missing")
    assert stop(statuses=only_should, last_batch_new_facts=0).stop


# ------------------------------------------------------------------------------------------ batch size
def test_first_batch_sizes() -> None:
    assert batch_size_range("quick", 0, POLICY) == (4, 5)
    assert batch_size_range("standard", 0, POLICY) == (5, 7)
    assert batch_size_range("thorough", 0, POLICY) == (6, 8)
    assert [next_batch_size(b, 0, POLICY) for b in ("quick", "standard", "thorough")] == [5, 7, 8]


def test_later_batches_are_clipped_to_the_budget() -> None:
    assert next_batch_size("standard", 7, POLICY) == 5  # 12 - 7
    assert batch_size_range("standard", 7, POLICY) == (5, 5)
    assert (
        next_batch_size("thorough", 16, POLICY) == 8
        and next_batch_size("thorough", 24, POLICY) == 1
    )
    assert batch_size_range("thorough", 20, POLICY) == (5, 5)
    assert next_batch_size("quick", 5, POLICY) == 0 and next_batch_size("standard", 40, POLICY) == 0
    assert batch_size_range("quick", 5, POLICY) == (0, 0)
    assert next_batch_size("quick", 3, POLICY) == 2


def test_thorough_runs_in_batches_of_six_to_eight() -> None:
    asked, sizes = 0, []
    while (n := next_batch_size("thorough", asked, POLICY)) > 0:
        sizes.append(n)
        asked += n
    assert asked == 25 and sizes == [8, 8, 8, 1]


@settings(max_examples=100)
@given(st.sampled_from(["quick", "standard", "thorough", "other"]), st.integers(-3, 60))
def test_batch_never_passes_the_budget(budget: str, asked: int) -> None:
    low, high = batch_size_range(budget, asked, POLICY)
    limit = POLICY.budget_max(budget if budget != "other" else "standard")  # type: ignore[arg-type]
    assert 0 <= low <= high
    assert max(0, asked) + high <= max(limit, max(0, asked))
    assert next_batch_size(budget, asked, POLICY) == high
