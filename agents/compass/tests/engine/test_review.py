"""``engine.review.flag_review``: scan findings become review assumptions (the person decides) and non-blocking open
questions, with stable ids and decisions across scans, and a high-impact finding blocks Gate 1."""

from __future__ import annotations

import dataclasses
from typing import Any

from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain.events import UserMessage
from mde_compass.engine.assumptions import review
from mde_compass.engine.compile_types import ReviewFinding
from mde_compass.engine.ports import AmbiguityFinding
from mde_compass.engine.review import flag_review
from mde_compass.engine.service import Service, _Turn
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock


def planner_service() -> Service:
    interview = dataclasses.replace(DEFAULT_CONFIG.interview, engine="planner")
    config = dataclasses.replace(DEFAULT_CONFIG, interview=interview)
    return build_service(memory=True, clock=FakeClock(), compass_config=config)


async def make_turn(svc: Service) -> _Turn:
    conv = await Conversation.start(svc)
    return await svc._load("t1", conv.session_id, UserMessage(event_id="evt_test", text="x"))


class RichScanner:
    def __init__(self, *rounds: list[ReviewFinding]) -> None:
        self.rounds = list(rounds)

    async def scan_review(self, ctx: Any, brief: Any) -> list[ReviewFinding]:
        return self.rounds.pop(0)


def install(svc: Service, scanner: Any) -> None:
    assert svc.ports is not None
    object.__setattr__(svc.ports, "ambiguity_scanner", scanner)


async def test_what_does_not_add_up_becomes_an_assumption_and_the_unclear_an_open_question() -> (
    None
):
    svc = planner_service()
    turn = await make_turn(svc)
    install(
        svc,
        RichScanner(
            [
                ReviewFinding(slot_id="context.geo", text="Pune / Mumbai", kind="conflicting"),
                ReviewFinding(slot_id=None, text="150% of users return", kind="unrealistic"),
                ReviewFinding(slot_id="idea.summary", text="fast", kind="vague"),
                ReviewFinding(slot_id=None, text="SAP", kind="undefined"),
            ]
        ),
    )
    found = await flag_review(svc, turn)
    assert len(found) == 4
    mine = [a for a in turn.brief.assumptions if a.basis == "review"]
    assert [(a.claim, a.impact) for a in mine] == [
        ("These two do not fit together: Pune / Mumbai.", "high"),  # context.geo has impact 5
        ("This may not be realistic: 150% of users return.", "low"),
    ]
    assert all(a.slot_id is None and a.what_changes for a in mine)
    assert [(q.text, q.blocking) for q in turn.brief.open_questions] == [
        ("fast", False),
        ("SAP", False),
    ]
    # the high-impact one blocks Gate 1 until the person decides
    assert review(turn.brief).unreviewed_high_ids == [mine[0].id]
    svc._rebuild(turn)
    assert [a.id for a in turn.brief.assumptions if a.basis == "review"] == [a.id for a in mine]


async def test_a_second_scan_keeps_decided_findings_and_drops_the_undecided_that_went_away() -> (
    None
):
    svc = planner_service()
    turn = await make_turn(svc)
    first = ReviewFinding(slot_id="context.geo", text="Pune / Mumbai", kind="conflicting")
    second = ReviewFinding(slot_id=None, text="150% of users return", kind="unrealistic")
    install(svc, RichScanner([first, second], [second]))
    await flag_review(svc, turn)
    ids = {a.claim: a.id for a in turn.brief.assumptions if a.basis == "review"}
    decided = next(
        a
        for a in turn.brief.assumptions
        if a.id == ids["These two do not fit together: Pune / Mumbai."]
    )
    turn.brief.assumptions = [
        a.model_copy(update={"decision": "accepted"}) if a.id == decided.id else a
        for a in turn.brief.assumptions
    ]
    await flag_review(svc, turn)
    after = {a.claim: a for a in turn.brief.assumptions if a.basis == "review"}
    assert (
        after["These two do not fit together: Pune / Mumbai."].decision == "accepted"
    )  # the audit trail stays
    assert (
        after["This may not be realistic: 150% of users return."].id
        == ids["This may not be realistic: 150% of users return."]
    )
    # nothing is proposed twice: the same open question is not added again
    assert len(turn.brief.assumptions) == 2


async def test_a_scanner_with_only_the_basic_port_still_works() -> None:
    svc = planner_service()
    turn = await make_turn(svc)

    class Basic:
        async def scan(self, ctx: Any, brief: Any) -> list[AmbiguityFinding]:
            return [
                AmbiguityFinding(
                    slot_id=None, text="works offline / login needs internet", kind="conflicting"
                ),
                AmbiguityFinding(slot_id=None, text="quick", kind="vague"),
            ]

    install(svc, Basic())
    await flag_review(svc, turn)
    assert [a.claim for a in turn.brief.assumptions if a.basis == "review"] == [
        "These two do not fit together: works offline / login needs internet."
    ]
    assert [q.text for q in turn.brief.open_questions] == ["quick"]


async def test_at_most_three_open_questions_and_five_review_assumptions() -> None:
    svc = planner_service()
    turn = await make_turn(svc)
    many = [ReviewFinding(slot_id=None, text=f"term{i}", kind="vague") for i in range(6)]
    many += [
        ReviewFinding(slot_id=None, text=f"odd number {i}", kind="plausibility") for i in range(8)
    ]
    install(svc, RichScanner(many))
    await flag_review(svc, turn)
    assert len(turn.brief.open_questions) == 3
    assert len([a for a in turn.brief.assumptions if a.basis == "review"]) == 5
