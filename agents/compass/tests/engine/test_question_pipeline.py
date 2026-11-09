"""``engine.question_pipeline``: plan, validate, audit, repair once, drop, truncate, fall back only as a last resort.

The model is replaced by ``StubPlanner`` / ``StubAuditor`` (they implement the engine ports), so each test says
exactly what the planner and the auditor "answered" and checks what the pipeline did with it.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from mde_compass.catalog import load_catalog
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain.cards import Card
from mde_compass.domain.interview import AssumedInstead, AuditVerdict, NeedStatus, PlannedBatch
from mde_compass.engine.ports import PlanResult
from mde_compass.engine.question_pipeline import (
    PipelineResult,
    audit_reasons,
    plan_validated,
    uncovered_obligations,
)
from mde_compass.engine.question_validators import ValidationContext
from mde_compass.scenarios.doubles import (
    TERMS,
    StubAuditor,
    StubPlanner,
    make_batch,
    make_card,
    make_status,
    plan_request,
    turn_context,
)

CATALOG = load_catalog()
POLICY = DEFAULT_CONFIG.interview
TOPICS = [
    "pay", "route", "menu", "cutoff", "refund", "uniform", "photo", "holiday", "weight", "feedback",
]  # fmt: skip


def good(n: int, **kw: Any) -> Card:
    """A valid, distinct, project-specific question (id ``Q-0nn``, its own need and intent)."""
    topic = TOPICS[n % len(TOPICS)]
    return make_card(
        f"Q-{n:03d}",
        f"How should your tiffin riders handle {topic} for office lunch orders in Pune?",
        intent=f"learn how the riders handle {topic}",
        needs=(f"domain:{topic}",),
        **kw,
    )


def grouped(numbers: range) -> list[Card]:
    """Good questions in groups of three (a group holds at most four)."""
    return [good(n, group_id=f"G{(n - numbers.start) // 3 + 1}") for n in numbers]


def generic(n: int) -> Card:
    return make_card(
        f"Q-{n:03d}",
        "What features do you want?",
        intent=f"learn the features wanted {n}",
        needs=("scope.features",),
    )


def ok(*cards: Card, **kw: Any) -> PlanResult:
    return PlanResult(batch=make_batch(cards), **kw)


def vctx(**kw: Any) -> ValidationContext:
    return ValidationContext.build(
        CATALOG,
        TERMS,
        language="en",
        config=DEFAULT_CONFIG,
        user_text="tiffin riders in Pune",
        **kw,
    )


async def run(
    planner: StubPlanner,
    auditor: StubAuditor | None = None,
    *,
    mode: str = "initial",
    want: tuple[int, int] = (3, 7),
    obligations: list[str] | None = None,
    statuses: dict[str, NeedStatus] | None = None,
    context: ValidationContext | None = None,
    policy: Any = POLICY,
    **req: Any,
) -> PipelineResult:
    return await plan_validated(
        planner=planner,
        auditor=auditor or StubAuditor(),
        ctx=turn_context(),
        request=plan_request(mode, want=want, **req),  # type: ignore[arg-type]
        vctx=context or vctx(batch_limit=max(want[1], 1)),
        policy=policy,
        catalog=CATALOG,
        statuses=statuses
        if statuses is not None
        else {"audience.segments": make_status("audience.segments")},
        obligations=obligations or [],
        needs_table="",
        asked_list="",
    )


def ids(result: PipelineResult) -> list[str]:
    return [c.id for c in result.batch.cards()]


# ------------------------------------------------------------------------------------------------ accept
async def test_good_questions_are_accepted_in_order_with_one_planner_call_and_one_audit() -> None:
    planner, auditor = StubPlanner(ok(good(2), good(3), good(4))), StubAuditor()
    result = await run(planner, auditor)
    assert ids(result) == ["Q-002", "Q-003", "Q-004"]
    assert result.planner_calls == 1 and len(auditor.requests) == 1
    assert not result.repaired and result.rejected == [] and result.fallbacks == 0
    assert [v.card_id for v in result.verdicts] == ids(result)


async def test_groups_and_titles_survive_the_pipeline() -> None:
    cards = [good(2, group_id="G1"), good(3, group_id="G2"), good(4, group_id="G1")]
    result = await run(StubPlanner(ok(*cards)))
    assert [(g.id, [c.id for c in g.cards]) for g in result.batch.groups] == [
        ("G1", ["Q-002", "Q-004"]),
        ("G2", ["Q-003"]),
    ]
    assert [g.title for g in result.batch.groups] == ["Group G1", "Group G2"]


# --------------------------------------------------------------------------------- reject, repair, drop
async def test_a_question_the_code_rejects_gets_one_repair_and_never_reaches_the_person() -> None:
    planner = StubPlanner(ok(good(2), generic(3)), ok(good(4)))
    auditor = StubAuditor()
    result = await run(planner, auditor)
    assert ids(result) == ["Q-002", "Q-004"] and result.repaired
    assert [(r.card_id, r.source) for r in result.rejected] == [("Q-003", "code")]
    assert "no_project_term" in result.rejected[0].reasons[0]
    assert result.planner_calls == 2
    repair = planner.requests[1]
    assert repair.want_min == 1 and repair.want_max == 1  # as many as were rejected
    assert any("Repair: Q-003 was rejected" in n for n in repair.style_notes)
    assert [c.id for c in auditor.requests[0].cards] == [
        "Q-002"
    ]  # the generic one never reached the auditor


async def test_the_auditor_is_not_called_when_the_code_rejects_everything() -> None:
    planner = StubPlanner(ok(generic(2), generic(3)), ok(generic(4)))
    auditor = StubAuditor()
    await run(planner, auditor, statuses={})
    assert auditor.requests == []


@pytest.mark.parametrize(
    ("verdict", "reason"),
    [
        ({"specific": 2}, "specificity 2 is below 3"),
        ({"specific": 1}, "specificity 1 is below 3"),
        ({"leading": True}, "leading wording"),
        ({"jargon": True}, "jargon"),
        ({"duplicate_of": "Q-001"}, "repeats Q-001"),
        ({"answerable": False}, "this person could not answer it"),
        ({"maps_to_need": False}, "it serves no need"),
    ],
)
async def test_the_auditor_rejects_on_a_low_score_or_any_flag(
    verdict: dict[str, object], reason: str
) -> None:
    planner = StubPlanner(ok(good(2), good(3)), ok(good(4)))
    result = await run(planner, StubAuditor({"Q-003": verdict}))
    assert ids(result) == ["Q-002", "Q-004"]
    (rejected,) = [r for r in result.rejected if r.card_id == "Q-003"]
    assert rejected.source == "auditor" and reason in rejected.reasons


async def test_a_question_without_an_audit_verdict_is_rejected_not_waved_through() -> None:
    planner = StubPlanner(ok(good(2), good(3)), ok(good(4)))
    result = await run(planner, StubAuditor(omit=["Q-003"]))
    assert "Q-003" not in ids(result)
    (rejected,) = [r for r in result.rejected if r.card_id == "Q-003"]
    assert rejected.reasons == ["the auditor gave no verdict"]


async def test_there_is_exactly_one_repair_then_what_is_still_rejected_is_dropped() -> None:
    planner = StubPlanner(ok(good(2), generic(3)), ok(generic(4)))  # a third plan() would raise
    result = await run(planner, statuses={})
    assert ids(result) == ["Q-002"] and result.repaired and result.planner_calls == 2
    assert [r.card_id for r in result.rejected] == ["Q-003", "Q-004"]
    assert planner.fallback_requests == []  # something survived: no canned question is substituted


async def test_no_repair_when_the_policy_has_none_or_in_simplify_mode() -> None:
    no_repair = dataclasses.replace(POLICY, repair_attempts=0)
    planner = StubPlanner(ok(good(2), generic(3)))
    result = await run(planner, policy=no_repair, statuses={})
    assert ids(result) == ["Q-002"] and result.planner_calls == 1 and not result.repaired
    simplify = StubPlanner(ok(generic(5)))
    result = await run(simplify, mode="simplify", want=(1, 1))
    assert result.planner_calls == 1 and ids(result) == []


async def test_the_audit_runs_again_on_the_repaired_questions_only() -> None:
    planner = StubPlanner(ok(good(2), generic(3)), ok(good(4)))
    auditor = StubAuditor()
    await run(planner, auditor)
    assert [[c.id for c in r.cards] for r in auditor.requests] == [["Q-002"], ["Q-004"]]


# ------------------------------------------------------------------------------------ truncate, backlog
async def test_extra_questions_are_cut_to_the_wanted_size_and_go_to_the_backlog() -> None:
    cards = grouped(range(2, 10))  # eight good questions
    planner = StubPlanner(ok(*cards))
    result = await run(planner, want=(3, 5), context=vctx(batch_limit=12))
    assert ids(result) == ["Q-002", "Q-003", "Q-004", "Q-005", "Q-006"]
    assert result.rejected == []
    topics = [b.topic for b in result.batch.backlog]
    assert len(topics) == 3 and topics[0].startswith("learn how the riders handle")
    assert result.batch.backlog[0].serves == ["domain:" + TOPICS[7 % len(TOPICS)]]


async def test_the_planners_own_backlog_is_kept_and_extras_are_added_after_it() -> None:
    from mde_compass.domain.interview import BacklogItem

    own = BacklogItem(
        topic="ask about holidays later", intent="holidays", serves=["domain:holiday"]
    )
    planner = StubPlanner(
        PlanResult(batch=make_batch(grouped(range(2, 6))).model_copy(update={"backlog": [own]}))
    )
    result = await run(planner, want=(1, 3), context=vctx(batch_limit=9))
    assert next(b.topic for b in result.batch.backlog) == "ask about holidays later"
    assert len(result.batch.backlog) == 2 and len(ids(result)) == 3


async def test_the_batch_limit_of_the_validators_rejects_what_does_not_fit() -> None:
    planner = StubPlanner(ok(*grouped(range(2, 9))), ok(good(9, group_id="G9")))
    result = await run(planner, want=(3, 5), context=vctx(batch_limit=5))
    assert len(ids(result)) == 5
    assert any("over_batch_size" in r for rej in result.rejected for r in rej.reasons)


# ------------------------------------------------------------------------------- priority (GAP-5)
def ranked(card: Card, priority: int) -> Card:
    return card.model_copy(update={"priority": priority})


def test_the_planners_priority_survives_into_the_card_so_truncation_can_use_it() -> None:
    assert "priority" in Card.model_fields
    assert (
        good(2).priority == 3
    )  # a card without one is a middle one: older cards and tests keep their order
    stored = good(2).model_dump(mode="json")
    stored.pop("priority")  # a card stored before the field existed still validates
    assert Card.model_validate(stored).priority == 3
    for bad in (0, 6):
        with pytest.raises(ValueError):
            Card.model_validate({**stored, "priority": bad})


async def test_a_batch_cut_to_size_keeps_the_most_important_questions_in_the_planners_reading_order() -> (
    None
):
    cards = [
        ranked(good(2, group_id="G1"), 5),
        ranked(good(3, group_id="G1"), 4),
        ranked(good(4, group_id="G1"), 3),
        ranked(good(5, group_id="G2"), 1),
        ranked(good(6, group_id="G2"), 2),
        ranked(good(7, group_id="G2"), 5),
    ]
    result = await run(StubPlanner(ok(*cards)), want=(1, 3), context=vctx(batch_limit=12))
    assert ids(result) == [
        "Q-004",
        "Q-005",
        "Q-006",
    ]  # priorities 3, 1, 2: not the first three by order
    assert [b.serves for b in result.batch.backlog] == [
        ["domain:" + TOPICS[n % len(TOPICS)]] for n in (2, 3, 7)
    ]  # what was cut waits in the backlog, in the planner's order
    assert [g.id for g in result.batch.groups] == ["G1", "G2"]  # groups keep the planner's order


async def test_equal_priorities_keep_the_planners_order() -> None:
    cards = [ranked(good(n, group_id=f"G{n}"), 2) for n in range(2, 8)]
    result = await run(StubPlanner(ok(*cards)), want=(1, 4), context=vctx(batch_limit=12))
    assert ids(result) == ["Q-002", "Q-003", "Q-004", "Q-005"]


async def test_the_validators_batch_limit_also_drops_the_least_important_not_the_last() -> None:
    cards = [ranked(good(n, group_id=f"G{n // 3}"), 5 if n < 5 else 1) for n in range(2, 8)]
    result = await run(StubPlanner(ok(*cards)), want=(1, 3), context=vctx(batch_limit=3))
    assert ids(result) == ["Q-005", "Q-006", "Q-007"]  # the three priority 1 questions
    cut = {r.card_id for r in result.rejected}
    assert cut == {"Q-002", "Q-003", "Q-004"}
    assert all("over_batch_size" in reason for r in result.rejected for reason in r.reasons)


async def test_a_less_important_repeat_loses_to_the_more_important_one() -> None:
    first = ranked(good(2, group_id="G1"), 5)
    twin = ranked(good(3, group_id="G1"), 1).model_copy(update={"intent": first.intent})
    result = await run(StubPlanner(ok(first, twin)), want=(1, 3))
    assert ids(result) == ["Q-003"]  # the important one stays, the repeat of it is rejected
    assert [r.card_id for r in result.rejected] == ["Q-002"]


# ----------------------------------------------------------------------------------------- the fallback
async def test_the_flagged_fallback_is_asked_for_only_when_nothing_survives() -> None:
    fb_card = make_card(
        "Q-009", "Who will use the app?", needs=("audience.segments",), origin="fallback"
    )
    fb = PlanResult(
        batch=make_batch([fb_card]),
        used_fallback=True,
        reason="every planned question was rejected",
    )
    planner = StubPlanner(ok(generic(2), generic(3)), ok(generic(4)), fallback=fb)
    result = await run(planner)
    assert result.used_fallback and result.fallbacks == 1 and ids(result) == ["Q-009"]
    assert result.reason == "every planned question was rejected"
    assert len(planner.fallback_requests) == 1 and result.planner_calls == 2


async def test_no_fallback_when_something_survives_or_nothing_is_open_or_the_mode_may_ask_nothing() -> (
    None
):
    survives = StubPlanner(ok(good(2), generic(3)), ok(generic(4)))
    await run(survives, statuses={"audience.segments": make_status("audience.segments")})
    assert survives.fallback_requests == []
    satisfied = {"audience.segments": make_status("audience.segments", "satisfied")}
    closed = StubPlanner(ok(generic(2)), ok(generic(3)))
    result = await run(closed, statuses=satisfied)
    assert closed.fallback_requests == [] and ids(result) == [] and not result.used_fallback
    followup = StubPlanner(ok(generic(2)), ok(generic(3)))
    result = await run(followup, mode="followup", want=(1, 1))
    assert followup.fallback_requests == [] and ids(result) == []


async def test_a_fallback_the_planner_already_made_is_trusted_flagged_and_skips_the_audit() -> None:
    fb = PlanResult(
        batch=make_batch([make_card("Q-002", "Who will use the app?", origin="fallback")]),
        used_fallback=True,
        reason="TimeoutError",
    )
    auditor = StubAuditor()
    result = await run(StubPlanner(fb), auditor)
    assert result.used_fallback and result.fallbacks == 1 and result.reason == "TimeoutError"
    assert auditor.requests == [] and result.planner_calls == 1


async def test_an_auditor_that_ran_on_its_heuristic_counts_as_a_fallback_too() -> None:
    result = await run(StubPlanner(ok(good(2), good(3))), StubAuditor(used_fallback=True))
    assert result.audit_fallback and result.fallbacks == 1 and not result.used_fallback


# -------------------------------------------------------------------------------------------- follow-ups
async def test_a_followup_is_never_a_duplicate_of_its_parent() -> None:
    parent = make_card(
        "Q-002", "Who orders your tiffins?", intent="learn who orders", needs=("audience.segments",)
    )
    follow = make_card(
        "Q-003", "Who orders your tiffins, and which offices?", intent="learn who orders",
        needs=("audience.segments",), origin="followup", parent="Q-002", depth=1,
    )  # fmt: skip
    # the same intent and the same need: a base question would be a duplicate; a follow-up refines its parent
    context = vctx(ledger_cards=[parent], batch_limit=1)
    audit = StubAuditor(
        {"Q-003": {"duplicate_of": "Q-002"}}
    )  # the auditor notices the closeness, too
    result = await run(
        StubPlanner(ok(follow)), audit, mode="followup", want=(1, 1), context=context
    )
    assert ids(result) == ["Q-003"] and result.rejected == []
    base = follow.model_copy(update={"parent_card_id": None, "origin": "planner", "depth": 0})
    control = await run(
        StubPlanner(ok(base), ok(good(4))), mode="followup", want=(1, 1), context=context
    )
    assert any("duplicate_intent" in r for rej in control.rejected for r in rej.reasons)


async def test_a_followup_that_repeats_some_other_question_is_still_rejected() -> None:
    follow = make_card(
        "Q-003", "Which offices do your tiffin riders reach?", intent="learn the offices",
        needs=("audience.segments",), origin="followup", parent="Q-002", depth=1,
    )  # fmt: skip
    auditor = StubAuditor({"Q-003": {"duplicate_of": "Q-007"}})
    result = await run(StubPlanner(ok(follow), ok(good(4))), auditor, mode="followup", want=(1, 1))
    assert [r.source for r in result.rejected] == [
        "auditor"
    ] and "repeats Q-007" in result.rejected[0].reasons


# --------------------------------------------------------------------------------------------- coverage
async def test_a_must_need_nobody_accounts_for_earns_the_repair_and_is_reported_if_still_open() -> (
    None
):
    planner = StubPlanner(ok(good(2), good(3)), ok(good(4)))
    result = await run(planner, obligations=["auth.methods"], statuses={})
    assert planner.requests[1].focus_needs == ["auth.methods"] and result.repaired
    assert result.uncovered == ["auth.methods"]  # the repair did not cover it either


async def test_a_need_the_repair_covers_or_the_planner_assumes_is_not_uncovered() -> None:
    auth = make_card(
        "Q-004", "How should tiffin customers sign in?", needs=("auth.methods",), intent="sign-in"
    )
    planner = StubPlanner(ok(good(2)), ok(auth))
    result = await run(planner, obligations=["auth.methods"], statuses={})
    assert result.uncovered == [] and planner.requests[1].focus_needs == ["auth.methods"]
    assumed = PlannedBatch(
        batch_id="BAT-000", mode="initial",
        groups=make_batch([good(2)]).groups,
        assumed_instead=[AssumedInstead(need_id="auth.methods", assumption="phone number and OTP")],
    )  # fmt: skip
    quiet = StubPlanner(PlanResult(batch=assumed))
    result = await run(quiet, obligations=["auth.methods"], statuses={})
    assert (
        quiet.requests
        and len(quiet.requests) == 1
        and result.uncovered == []
        and not result.repaired
    )


def test_uncovered_obligations_counts_asked_covered_and_assumed_needs_as_accounted() -> None:
    cards = [make_card(needs=("audience.segments",))]
    assert uncovered_obligations(
        cards,
        ["roles.list"],
        ["auth.methods"],
        ["audience.segments", "roles.list", "auth.methods", "apps.list"],
    ) == ["apps.list"]
    assert uncovered_obligations([], [], [], []) == []


def test_audit_reasons_name_each_failure_and_pass_a_clean_verdict() -> None:
    assert audit_reasons(AuditVerdict(card_id="Q-002", specific=3), POLICY) == []
    bad = AuditVerdict(
        card_id="Q-002", specific=2, leading=True, jargon=True, duplicate_of="Q-001",
        answerable=False, maps_to_need=False,
    )  # fmt: skip
    assert audit_reasons(bad, POLICY) == [
        "specificity 2 is below 3", "leading wording", "jargon", "repeats Q-001",
        "this person could not answer it", "it serves no need",
    ]  # fmt: skip


# ------------------------------------------------------------------------------- pinned gaps (strict xfail)
@pytest.mark.xfail(
    strict=True,
    reason="GAP-4: InterviewPolicy.high_confidence_closes_need is never read, so a base question about a need "
    "that facts already satisfy is shown instead of becoming a confirmation (over-asking control, prompt 2.6)",
)
async def test_a_question_about_a_need_the_facts_already_satisfy_is_not_shown_as_a_question() -> (
    None
):
    satisfied = {"audience.segments": make_status("audience.segments", "satisfied")}
    ask_again = make_card(
        "Q-002",
        "Who orders your tiffins, and what do they want from the app?",
        needs=("audience.segments",),
    )
    result = await run(StubPlanner(ok(ask_again), ok(good(3))), statuses=satisfied)
    assert all(c.id != "Q-002" or c.type == "confirm" for c in result.batch.cards())
