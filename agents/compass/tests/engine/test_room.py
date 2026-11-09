"""``interview_policy.Room``: the question-budget arithmetic of a tier (ADR 0016).

The guarantees the owner asked for, as properties: the hard cap is never exceeded whatever the answers do, one place is
always kept for the closing question, follow-ups never take a base place, and nothing is asked after the closing
question."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from mde_compass.config import DEFAULT_CONFIG
from mde_compass.engine.interview_policy import Room
from mde_compass.scenarios.doubles import make_card, make_qa

QUICK = (7, 10)
STANDARD = (12, 15)
THOROUGH = (25, 30)


def fresh(tier: tuple[int, int]) -> Room:
    return Room(target=tier[0], hard_cap=tier[1])


def test_the_policy_carries_the_hard_caps_of_the_three_tiers() -> None:
    policy = DEFAULT_CONFIG.interview
    assert [policy.hard_cap(b) for b in ("quick", "standard", "thorough")] == [10, 15, 30]


def test_a_fresh_quick_interview_has_six_base_places_three_follow_ups_and_keeps_one_for_the_closing() -> (
    None
):
    room = fresh(QUICK)
    assert (room.base_room, room.followup_room, room.committed) == (6, 3, 0)
    assert not room.closing_due
    assert room.batch_size(3) == 3 and room.batch_size(9) == 6 and room.batch_size(0) == 0


def test_when_the_base_places_are_used_only_the_closing_is_due() -> None:
    room = fresh(QUICK).add(base=6)
    assert room.base_room == 0 and room.closing_due
    assert room.followup_room == 3  # follow-ups use the slack, not a base place
    assert room.batch_size(3) == 0


def test_follow_ups_use_only_the_slack_and_never_pass_the_cap_minus_one() -> None:
    room = fresh(QUICK).add(base=6, followups=3)
    assert room.followup_room == 0 and room.committed == 9
    assert fresh(STANDARD).add(base=11, followups=3).followup_room == 0
    # base questions above their share (a fallback batch, a gap question) still respect the cap
    assert fresh(QUICK).add(base=8).followup_room == 1  # cap - 1 - base


def test_after_the_closing_question_nothing_is_allowed() -> None:
    room = fresh(QUICK).add(base=4, closing=True)
    assert room.closing_committed and room.committed == 5
    assert room.base_room == 0 and room.followup_room == 0 and not room.closing_due
    assert room.batch_size(5) == 0


def test_room_counts_what_the_ledger_holds_and_skips_dropped_and_superseded() -> None:
    qa = [
        make_qa(make_card("Q-001")),
        make_qa(make_card("Q-002", origin="fallback")),
        make_qa(make_card("Q-003", origin="followup", parent="Q-001", depth=1), "skipped"),
        make_qa(make_card("Q-004"), "open", asked=False),  # waiting in a batch: already committed
        make_qa(make_card("Q-005"), "dropped"),
        make_qa(make_card("Q-006"), "superseded"),
    ]
    room = Room.of(qa, target=12, hard_cap=15)
    assert (room.base, room.followups, room.closing_committed) == (3, 1, False)
    closing = Room.of(
        [*qa, make_qa(make_card("Q-007", origin="closing"), "open", asked=False)],
        target=12,
        hard_cap=15,
    )
    assert closing.closing_committed and closing.committed == 5


@given(
    tier=st.sampled_from([QUICK, STANDARD, THOROUGH]),
    steps=st.lists(
        st.tuples(st.sampled_from(["base", "followup"]), st.integers(1, 8)), max_size=60
    ),
)
def test_whatever_happens_the_cap_holds_and_the_closing_is_last(
    tier: tuple[int, int], steps: list[tuple[str, int]]
) -> None:
    target, hard = tier
    room = fresh(tier)
    for kind, n in steps:
        if kind == "base":
            room = room.add(base=room.batch_size(n))
        else:
            room = room.add(followups=min(n, room.followup_room))
        assert room.committed <= hard - 1  # the closing place is never taken by anything else
        assert room.base <= target - 1 and room.followups <= hard - target
    room = room.add(closing=True)  # the closing question is asked once, last
    assert room.committed <= hard
    assert room.base_room == 0 and room.followup_room == 0
    assert (
        room.add(base=room.batch_size(5), followups=min(5, room.followup_room)).committed
        == room.committed
    )
