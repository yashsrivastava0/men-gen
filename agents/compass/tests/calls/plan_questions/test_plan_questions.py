"""``cmp.plan_questions``: spec, examples, mock labelling, the flagged fallback, adapter conversion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.plan_questions.adapter import PlanAdapter, build_input, to_batch
from mde_compass.calls.plan_questions.mock import handle
from mde_compass.calls.plan_questions.schema import PlanInput, PlanOutput
from mde_compass.calls.plan_questions.spec import SPEC
from mde_compass.calls.plan_questions.validate import structural
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.engine.ports import AnswerFocus, NeedBrief, PlanContext, PlanRequest, TurnContext
from mde_core.errors import ProviderError

EXAMPLES_DIR = Path(__file__).parents[3] / "src/mde_compass/calls/plan_questions/examples"
EXAMPLES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES_DIR.glob("*.json"))]
CATALOG = load_catalog()
CTX = TurnContext(session_id="ses_1", tenant_id="t1")


def _input(raw: dict[str, object]) -> PlanInput:
    return PlanInput.model_validate(raw["input"])


def _output(raw: dict[str, object]) -> PlanOutput:
    return PlanOutput.model_validate(raw["output"])


def _request(inp: PlanInput, **changes: object) -> PlanRequest:
    needs = [
        NeedBrief(
            need_id=n.need_id,
            label=n.label,
            sufficient=n.sufficient,
            aspects=n.aspects,
            state=n.state,
            missing_aspects=n.missing_aspects,
            impact=n.impact,
        )
        for n in inp.needs
    ]
    base: dict[str, object] = {
        "mode": inp.mode,
        "language": inp.language,
        "context": PlanContext(project_card=inp.project_card, fact_digest=inp.fact_digest),
        "needs": needs,
        "project_terms": inp.project_terms,
        "want_min": inp.want_min,
        "want_max": inp.want_max,
        "next_card_n": inp.next_card_n,
    }
    base.update(changes)
    return PlanRequest.model_validate(base)


def test_spec_matches_profile() -> None:
    s = SPEC.spec
    assert s.id == "cmp.plan_questions" and s.version == 2
    assert s.reasoning_effort == "low" and s.max_output_tokens == 5000 and s.route == "standard"
    assert s.untrusted_inputs and not s.tools and SPEC.lane == "writer"
    assert "focus_answer" in s.untrusted_inputs and "fact_digest" in s.untrusted_inputs


def test_there_are_three_diverse_examples_that_pass_the_validator() -> None:
    assert len(EXAMPLES) >= 3
    openings = {e["input"]["project_card"].split("\n")[0][:40] for e in EXAMPLES}
    assert len(openings) == len(EXAMPLES)
    assert {e["input"]["language"] for e in EXAMPLES} >= {"en", "hi-Latn"}
    assert {e["input"]["mode"] for e in EXAMPLES} >= {"initial", "replan"}
    for e in EXAMPLES:
        inp, out = _input(e), _output(e)
        assert structural(inp, out) == [], e["name"]
        assert inp.want_min <= len(out.questions) <= inp.want_max + 2
        # every question says what it is for and what it serves
        assert all(q.intent and q.serves and q.unlocks and q.answer_spec for q in out.questions)


def test_examples_do_not_share_question_wording() -> None:
    texts = [q["text"] for e in EXAMPLES for q in e["output"]["questions"]]
    assert len(texts) == len(set(texts))


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
async def test_templates_render_and_the_mock_runs_through_the_real_pipeline(
    raw: dict[str, object],
) -> None:
    runner = CallRunner(make_client([SPEC]))
    res = await runner.run(SPEC, _input(raw), session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    questions = res.output.questions
    assert questions and all(q.text.startswith("[mock]") for q in questions)
    assert all(q.group_title.startswith("[mock]") for q in questions)


def test_mock_questions_are_built_from_the_idea_terms_and_differ_between_ideas() -> None:
    by_idea = {e["name"]: handle(_input(e)) for e in EXAMPLES}
    texts = {k: {q.text for q in v.questions} for k, v in by_idea.items()}
    names = list(texts)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            assert texts[names[i]].isdisjoint(texts[names[j]])
    clinic = _input(next(e for e in EXAMPLES if "clinic" in e["name"]))
    out = handle(clinic)
    terms = [t.lower() for t in clinic.project_terms]
    assert all(any(t in q.text.lower() for t in terms) for q in out.questions)
    assert out.domain_hypothesis.category == "" and out.domain_hypothesis.confidence == 0.0


def test_mock_follows_up_with_exactly_one_question_on_the_focus_need() -> None:
    inp = _input(EXAMPLES[0]).model_copy(
        update={"mode": "followup", "focus_needs": ["roles.list"], "next_card_n": 9}
    )
    questions = handle(inp).questions
    assert len(questions) == 1 and questions[0].serves == ["roles.list"]
    assert questions[0].id == "Q-009" and questions[0].type == "text" and not questions[0].options


def test_mock_does_not_re_propose_a_need_an_earlier_batch_already_asked() -> None:
    """GAP-1: a real planner reads the asked list and never repeats a need; the mock now behaves the same, so a
    replan does not hand the validators a batch of repeats (which ends in the flagged fallback)."""
    inp = _input(EXAMPLES[0])
    first = handle(inp).questions
    assert first
    asked = "\n".join(f"{q.id} | open | {q.intent}" for q in first)
    second = handle(inp.model_copy(update={"asked_list": asked})).questions
    assert {n for q in first for n in q.serves}.isdisjoint({n for q in second for n in q.serves})


def test_mock_follow_up_may_still_target_a_need_that_was_asked() -> None:
    inp = _input(EXAMPLES[0])
    first = handle(inp).questions
    need = first[0].serves[0]
    asked = "\n".join(f"{q.id} | open | {q.intent}" for q in first)
    follow = inp.model_copy(
        update={"mode": "followup", "focus_needs": [need], "asked_list": asked, "next_card_n": 20}
    )
    questions = handle(follow).questions
    assert len(questions) == 1 and questions[0].serves == [need]
    assert "learn more about" in questions[0].intent


def test_structural_validator_rejects_what_is_not_a_batch() -> None:
    inp = _input(EXAMPLES[0])
    good = _output(EXAMPLES[0])
    assert structural(inp, good) == []
    empty = good.model_copy(update={"questions": []})
    assert any("no questions" in e for e in structural(inp, empty))
    assert (
        structural(inp.model_copy(update={"mode": "replan"}), empty) == []
    )  # zero is a valid replan
    q0, q1 = good.questions[0], good.questions[1]
    dup = good.model_copy(update={"questions": [q0, q0]})
    assert any("unique" in e for e in structural(inp, dup))
    bad = good.model_copy(update={"questions": [q0.model_copy(update={"options": []})]})
    assert any("needs options" in e for e in structural(inp, bad))
    # harmless slips are not fatal (a live model once failed a whole batch on `priority: 6`)
    wrong = good.model_copy(update={"questions": [q0.model_copy(update={"recommended": "nope"})]})
    assert structural(inp, wrong) == []
    odd = good.model_copy(update={"questions": [q0.model_copy(update={"priority": 9})]})
    assert structural(inp, odd) == []
    two_titles = q1.model_copy(update={"group_id": q0.group_id, "group_title": "Another title"})
    assert any(
        "two different titles" in e
        for e in structural(inp, good.model_copy(update={"questions": [q0, two_titles]}))
    )
    untitled = q0.model_copy(update={"group_title": ""})
    assert any(
        "no group id or title" in e
        for e in structural(inp, good.model_copy(update={"questions": [untitled]}))
    )


def test_to_batch_assigns_ids_letters_and_card_types() -> None:
    raw = EXAMPLES[0]
    batch = to_batch(CATALOG, _output(raw), _request(_input(raw), next_card_n=20))
    cards = batch.cards()
    assert [c.id for c in cards] == [f"Q-{n:03d}" for n in range(20, 20 + len(cards))]
    assert [g.id for g in batch.groups] == ["G1", "G2", "G3"] and cards[0].group_id == "G1"
    assert batch.groups[0].title == "How your clinic runs"
    first = cards[0]
    assert first.frame == "planned" and first.origin == "planner" and first.depth == 0
    assert [o.id for o in first.options] == ["A", "B", "C"]
    assert [o.recommended for o in first.options].count(True) == 1 and first.you_decide.assumes
    assert first.need_ids == ["scope.features", "domain:doctor_schedules"]
    assert first.slots == ["scope.features"]  # domain topics are not slots
    text_card = next(c for c in cards if c.intent.startswith("learn what happens when a patient"))
    assert text_card.type == "open_prompt" and text_card.options == [] and text_card.examples
    assert batch.domain_hypothesis is not None
    assert batch.domain_hypothesis.category.startswith("clinic")
    assert batch.assumed_instead and batch.expert_gaps and batch.backlog


def test_the_planners_priority_reaches_the_card_and_a_wild_value_is_clamped_not_fatal() -> None:
    raw = EXAMPLES[0]
    out = _output(raw)
    wire = [q.priority for q in out.questions]
    cards = to_batch(CATALOG, out, _request(_input(raw))).cards()
    assert [c.priority for c in cards] == wire and all(1 <= p <= 5 for p in wire)
    wild = out.model_copy(
        update={
            "questions": [
                out.questions[0].model_copy(update={"priority": 9}),
                out.questions[1].model_copy(update={"priority": -2}),
            ]
        }
    )
    clamped = to_batch(CATALOG, wild, _request(_input(raw))).cards()
    assert [c.priority for c in clamped] == [5, 1]


async def test_a_flagged_fallback_card_ranks_by_the_impact_of_its_need() -> None:
    raw = EXAMPLES[0]
    gateway = ScriptedGateway(ProviderError("boom"))
    result = await PlanAdapter(CallRunner(gateway), CATALOG).plan(CTX, _request(_input(raw)))
    cards = result.batch.cards()
    assert cards and all(
        c.priority == max(1, min(5, 6 - CATALOG.slots[c.need_ids[0]].impact)) for c in cards
    )


def test_hypothesis_is_only_kept_for_the_initial_mode() -> None:
    raw = EXAMPLES[0]
    replan = to_batch(CATALOG, _output(raw), _request(_input(raw), mode="replan"))
    assert replan.domain_hypothesis is None


async def test_adapter_returns_the_planned_batch_and_marks_a_followup() -> None:
    raw = next(e for e in EXAMPLES if e["input"]["mode"] == "replan")
    out = _output(raw)
    one = out.model_copy(update={"questions": out.questions[:1]})
    focus = AnswerFocus(
        card_id="Q-003", question="Who uses it?", answer_text="staff", need_ids=["roles.list"]
    )
    request = _request(_input(raw), mode="followup", focus=focus, followup_depth=1, next_card_n=8)
    result = await PlanAdapter(CallRunner(ScriptedGateway(one)), CATALOG).plan(CTX, request)
    assert not result.used_fallback
    (card,) = result.batch.cards()
    assert card.id == "Q-008" and card.origin == "followup" and card.parent_card_id == "Q-003"
    assert card.depth == 1


async def test_a_failed_call_gives_the_flagged_fallback_never_a_silent_canned_question() -> None:
    raw = EXAMPLES[0]
    gateway = ScriptedGateway(ProviderError("boom"))
    result = await PlanAdapter(CallRunner(gateway), CATALOG).plan(CTX, _request(_input(raw)))
    assert result.used_fallback and "ProviderError" in result.reason
    cards = result.batch.cards()
    assert 1 <= len(cards) <= 3
    assert all(c.fallback and c.origin == "fallback" and c.frame == "planned" for c in cards)
    assert all(c.need_ids and c.need_ids[0] in CATALOG.slots for c in cards)
    needs = {n.need_id: n for n in _request(_input(raw)).needs}
    assert all(needs[c.need_ids[0]].state in ("missing", "partial") for c in cards)
    assert result.batch.groups[0].title == CATALOG.t("interview.group_fallback", "en")


async def test_followup_and_simplify_fallbacks_ask_nothing() -> None:
    raw = EXAMPLES[0]
    for mode in ("followup", "simplify"):
        gateway = ScriptedGateway(ProviderError("boom"))
        request = _request(_input(raw), mode=mode)
        result = await PlanAdapter(CallRunner(gateway), CATALOG).plan(CTX, request)
        assert result.used_fallback and result.batch.cards() == []


async def test_a_structurally_bad_answer_falls_back_too() -> None:
    raw = EXAMPLES[0]
    empty = _output(raw).model_copy(update={"questions": []})
    result = await PlanAdapter(CallRunner(ScriptedGateway(empty)), CATALOG).plan(
        CTX, _request(_input(raw))
    )
    assert result.used_fallback and "no questions" in result.reason


def test_the_wire_input_redacts_secrets_in_the_users_words() -> None:
    raw = EXAMPLES[0]
    key = "AKIAIOSFODNN7EXAMPLE"  # secret-scan: allow
    request = _request(
        _input(raw),
        context=PlanContext(
            project_card=f"my key is {key}", fact_digest="x", last_qa=f"typed {key}"
        ),
        focus=AnswerFocus(card_id="Q-001", question="q", answer_text=f"also {key}"),
    )
    wire = build_input(CATALOG, request)
    assert key not in wire.project_card and key not in wire.last_qa and key not in wire.focus_answer


def test_open_needs_carry_their_registry_text_into_the_wire_input() -> None:
    wire = build_input(CATALOG, _request(_input(EXAMPLES[0])))
    geo = next(n for n in wire.needs if n.need_id == "context.geo")
    assert geo.sufficient and geo.aspects and geo.level == "must" and geo.card_type
