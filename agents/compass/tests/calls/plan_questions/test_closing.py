"""``cmp.plan_questions`` mode ``closing`` (ADR 0016): the reserved last question, one priority card."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.audit_questions.adapter import AuditAdapter
from mde_compass.calls.audit_questions.spec import SPEC as AUDIT_SPEC
from mde_compass.calls.base import CallRunner
from mde_compass.calls.plan_questions.adapter import PlanAdapter, build_input, to_batch
from mde_compass.calls.plan_questions.fallback import closing_fallback
from mde_compass.calls.plan_questions.mock import handle
from mde_compass.calls.plan_questions.schema import ClosingWire, PlanInput, PlanOutput
from mde_compass.calls.plan_questions.spec import SPEC
from mde_compass.calls.plan_questions.validate import structural
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.config import InterviewPolicy
from mde_compass.engine.ports import ClosingContext, PlanContext, PlanRequest, TurnContext
from mde_compass.engine.question_pipeline import plan_validated
from mde_compass.engine.question_validators import (
    CLOSING_MAX_OPTIONS,
    CLOSING_NEEDS,
    ValidationContext,
    validate_batch,
)
from mde_core.errors import ProviderError

EXAMPLE = json.loads(
    (
        Path(__file__).parents[3]
        / "src/mde_compass/calls/plan_questions/examples/en_tiffin_pune_closing.json"
    ).read_text(encoding="utf-8")
)
CATALOG = load_catalog()
CTX = TurnContext(session_id="ses_1", tenant_id="t1")
IDEA = "A subscription app for my tiffin delivery service in Pune"
CLOSING = ClosingContext(
    features=["weekly meal plans", "daily menu", "morning delivery routes", "pause a delivery"],
    roles=["customer", "cook"],
    goals=["first 100 subscribers by December"],
    constraints=["works on low-cost Android phones"],
    assumptions=["payments are collected outside the app at first"],
)


def _request(**changes: object) -> PlanRequest:
    base: dict[str, object] = {
        "mode": "closing",
        "language": "en",
        "context": PlanContext(project_card=IDEA, fact_digest="FCT-001 weekly plans"),
        "project_terms": ["tiffin", "Pune", "subscription", "delivery"],
        "want_min": 1,
        "want_max": 1,
        "next_card_n": 12,
        "closing": CLOSING,
    }
    base.update(changes)
    return PlanRequest.model_validate(base)


def _example_input() -> PlanInput:
    return PlanInput.model_validate(EXAMPLE["input"])


def _example_output() -> PlanOutput:
    return PlanOutput.model_validate(EXAMPLE["output"])


def _vctx(request: PlanRequest, **kw: object) -> ValidationContext:
    return ValidationContext.build(
        CATALOG,
        request.project_terms,
        language="en",
        user_text=f"{IDEA}. {' '.join(CLOSING.goals + CLOSING.constraints)}",
        batch_limit=1,
        closing=request.closing,
        **kw,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------------------------------ input and spec
def test_the_old_guard_is_gone_and_the_input_carries_the_closing_context() -> None:
    wire = build_input(CATALOG, _request())
    assert wire.mode == "closing" and wire.needs == [] and wire.next_card_n == 12
    assert wire.closing.features[0] == "weekly meal plans"
    assert wire.closing.goals == ["first 100 subscribers by December"]
    assert wire.closing.assumptions == ["payments are collected outside the app at first"]
    assert "closing" in SPEC.spec.untrusted_inputs and not SPEC.spec.tools


def test_other_modes_carry_an_empty_closing_context() -> None:
    wire = build_input(CATALOG, _request(mode="initial", closing=None))
    assert wire.closing == ClosingWire()


def test_the_closing_context_is_redacted_like_every_user_word() -> None:
    key = "AKIAIOSFODNN7EXAMPLE"  # secret-scan: allow
    closing = ClosingContext(features=[f"use key {key}", "  ", "daily menu"])
    wire = build_input(CATALOG, _request(closing=closing))
    assert key not in " ".join(wire.closing.features) and len(wire.closing.features) == 2


# --------------------------------------------------------------------------- example, mock, structural
def test_the_example_is_a_valid_closing_batch_and_passes_the_code_validators() -> None:
    inp, out = _example_input(), _example_output()
    assert inp.mode == "closing" and structural(inp, out) == []
    (q,) = out.questions
    assert q.type == "priority" and q.serves == list(CLOSING_NEEDS) and 3 <= len(q.options) <= 8
    request = _request()
    batch = to_batch(CATALOG, out, request)
    result = validate_batch(batch.cards(), _vctx(request))
    assert [r.reasons for r in result.rejected] == [] and len(result.accepted) == 1


def test_the_mock_builds_one_labelled_priority_question_from_the_context_lists() -> None:
    out = handle(_example_input())
    assert structural(_example_input(), out) == []
    (q,) = out.questions
    assert q.type == "priority" and q.text.startswith("[mock]") and "tiffin" in q.text
    assert q.serves == ["scope.priority", "goals.primary"] and q.id == "Q-012"
    assert all(o.label.startswith("[mock]") for o in q.options)
    labels = [o.label.removeprefix("[mock] ") for o in q.options]
    assert labels[:3] == EXAMPLE["input"]["closing"]["features"][:3]
    assert {o.basis for o in q.options} == {"your_words"}
    assert q.group_title.startswith("[mock]") and q.examples


def test_the_mock_adds_at_most_two_labelled_suggestions_when_the_lists_are_short() -> None:
    inp = _example_input().model_copy(update={"closing": ClosingWire(features=["daily menu"])})
    (q,) = handle(inp).questions
    assert [o.basis for o in q.options] == ["your_words", "suggestion", "suggestion"]
    bare = _example_input().model_copy(update={"closing": ClosingWire()})
    (nothing,) = handle(bare).questions
    assert len(nothing.options) == 2  # too few: the structural check sends it to the fallback
    assert any("at least 3" in e for e in structural(bare, handle(bare)))


async def test_the_mock_runs_through_the_real_pipeline_and_the_templates_render() -> None:
    runner = CallRunner(make_client([SPEC]))
    res = await runner.run(SPEC, _example_input(), session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert res.output.questions[0].type == "priority"


def test_structural_needs_a_priority_question_with_enough_options() -> None:
    inp, good = _example_input(), _example_output()
    q = good.questions[0]
    assert any(
        "no priority question" in e
        for e in structural(inp, good.model_copy(update={"questions": []}))
    )
    as_choice = good.model_copy(update={"questions": [q.model_copy(update={"type": "choose_one"})]})
    assert structural(inp, as_choice)
    few = good.model_copy(update={"questions": [q.model_copy(update={"options": q.options[:2]})]})
    assert any("at least 3" in e for e in structural(inp, few))
    blank = q.model_copy(
        update={"options": [*q.options[:3], q.options[3].model_copy(update={"label": " "})]}
    )
    assert any(
        "no label" in e for e in structural(inp, good.model_copy(update={"questions": [blank]}))
    )


# ------------------------------------------------------------------------------------------- the adapter
def test_the_output_card_is_the_closing_question() -> None:
    request = _request()
    batch = to_batch(CATALOG, _example_output(), request)
    assert batch.mode == "replan"  # stored mode: the database CHECK has no 'closing'
    (card,) = batch.cards()
    assert card.id == "Q-012" and card.origin == "closing" and card.type == "priority"
    assert card.need_ids == list(CLOSING_NEEDS) and card.slots == list(CLOSING_NEEDS)
    assert card.custom_input.enabled and card.custom_input.placeholder
    assert not card.fallback and card.priority == 1 and card.group_id == "G1"
    assert not any(
        o.recommended for o in card.options
    )  # a priority card carries no recommended option
    top = next(o for o in card.options if o.label == "Weekly meal plans")
    assert card.you_decide.assumes == top.id
    assert [o.id for o in card.options] == list("ABCDEFG")


def test_the_adapter_keeps_only_the_first_priority_question() -> None:
    out = _example_output()
    q = out.questions[0]
    second = q.model_copy(update={"id": "Q-013", "text": "Another question?"})
    batch = to_batch(CATALOG, out.model_copy(update={"questions": [q, second]}), _request())
    assert [c.question for c in batch.cards()] == [q.text]
    none = out.model_copy(update={"questions": [q.model_copy(update={"type": "text"})]})
    assert to_batch(CATALOG, none, _request()).cards() == []


def test_code_decides_which_options_are_the_projects_own() -> None:
    """An option that matches no candidate is a suggestion whatever the model claimed; beyond two they are dropped."""
    q = _example_output().questions[0]
    own = q.options[:2]
    claimed = [
        q.options[0].model_copy(update={"label": f"Invented thing {i}", "basis": "your_words"})
        for i in range(4)
    ]
    out = _example_output().model_copy(
        update={"questions": [q.model_copy(update={"options": [*own, *claimed]})]}
    )
    (card,) = to_batch(CATALOG, out, _request()).cards()
    assert [o.basis for o in card.options] == [
        "your_words",
        "your_words",
        "suggestion",
        "suggestion",
    ]


def test_a_typical_claim_on_an_own_candidate_becomes_a_suggestion() -> None:
    q = _example_output().questions[0]
    typical = q.options[0].model_copy(update={"basis": "typical"})
    out = _example_output().model_copy(
        update={"questions": [q.model_copy(update={"options": [typical, *q.options[1:]]})]}
    )
    (card,) = to_batch(CATALOG, out, _request()).cards()
    assert card.options[0].basis == "suggestion"


def test_options_are_capped_at_eight() -> None:
    q = _example_output().questions[0]
    many = [
        q.options[0].model_copy(update={"label": f"daily menu {i}", "basis": "your_words"})
        for i in range(12)
    ]
    out = _example_output().model_copy(
        update={"questions": [q.model_copy(update={"options": many})]}
    )
    (card,) = to_batch(CATALOG, out, _request()).cards()
    assert len(card.options) == CLOSING_MAX_OPTIONS


async def test_the_adapter_runs_the_call_and_returns_the_closing_card() -> None:
    gateway = ScriptedGateway(_example_output())
    result = await PlanAdapter(CallRunner(gateway), CATALOG).plan(CTX, _request())
    assert not result.used_fallback
    (card,) = result.batch.cards()
    assert card.origin == "closing" and card.type == "priority"
    (name, wire) = gateway.calls[0]
    assert name == "cmp.plan_questions" and wire.mode == "closing"  # type: ignore[attr-defined]


# -------------------------------------------------------------------------------------------- fallback
async def test_a_failed_call_gives_the_flagged_closing_fallback_from_the_contexts_own_features() -> (
    None
):
    result = await PlanAdapter(CallRunner(ScriptedGateway(ProviderError("boom"))), CATALOG).plan(
        CTX, _request()
    )
    assert result.used_fallback and "ProviderError" in result.reason
    (card,) = result.batch.cards()
    assert card.fallback and card.origin == "closing" and card.type == "priority"
    assert card.need_ids == list(CLOSING_NEEDS) and card.frame == "planned" and card.id == "Q-012"
    labels = [o.label for o in card.options]
    assert labels[:4] == CLOSING.features and "first 100 subscribers by December" in labels
    assert card.question == CATALOG.slots["goals.primary"].fallback_card.question.en
    assert card.custom_input.enabled and result.batch.mode == "replan"


async def test_a_structurally_bad_closing_answer_falls_back_too() -> None:
    q = _example_output().questions[0]
    bad = _example_output().model_copy(
        update={"questions": [q.model_copy(update={"type": "text"})]}
    )
    result = await PlanAdapter(CallRunner(ScriptedGateway(bad)), CATALOG).plan(CTX, _request())
    assert result.used_fallback and "no priority question" in result.reason
    (card,) = result.batch.cards()
    assert card.origin == "closing" and card.fallback


def test_without_features_the_fallback_is_the_registry_card_for_the_main_goal() -> None:
    request = _request(closing=ClosingContext(roles=["customer"]))
    batch = closing_fallback(CATALOG, request, batch_id="BAT-000", group_title="Your project")
    (card,) = batch.cards()
    registry = CATALOG.slots["goals.primary"].fallback_card
    assert card.question == registry.question.en and card.type == registry.type
    assert [o.label for o in card.options] == [o.label.en for o in registry.options]
    assert card.fallback and card.origin == "closing" and card.need_ids == list(CLOSING_NEEDS)
    none = closing_fallback(
        CATALOG, _request(closing=None), batch_id="BAT-000", group_title="Your project"
    )
    assert none.cards()[0].question == registry.question.en


def test_the_fallback_speaks_hindi_and_shortens_long_labels() -> None:
    long = "one two three four five six seven eight nine ten"
    request = _request(
        language="hi", closing=ClosingContext(features=[long, "daily menu", "pause a delivery"])
    )
    (card,) = closing_fallback(CATALOG, request, batch_id="B", group_title="t").cards()
    assert card.question == CATALOG.slots["goals.primary"].fallback_card.question.hi
    assert card.options[0].label == "one two three four five six seven eight"
    assert card.language == "hi"


def test_duplicate_features_do_not_make_duplicate_options() -> None:
    closing = ClosingContext(
        features=["Daily menu", "daily menu", "pause a delivery", "pause a delivery!"]
    )
    (card,) = closing_fallback(
        CATALOG, _request(closing=closing), batch_id="B", group_title="t"
    ).cards()
    # two distinct features are too few for a priority card: the registry card is used instead
    assert card.type == "choose_one" and card.fallback


async def test_the_planners_own_fallback_port_gives_the_same_closing_card() -> None:
    result = await PlanAdapter(CallRunner(ScriptedGateway()), CATALOG).fallback(CTX, _request())
    (card,) = result.batch.cards()
    assert result.used_fallback and card.origin == "closing" and card.type == "priority"


# ------------------------------------------------------------------------------- through the pipeline
@pytest.mark.parametrize("closing", [CLOSING, ClosingContext(features=["daily menu"])])
async def test_the_mock_closing_question_survives_the_validators_and_the_auditor(
    closing: ClosingContext,
) -> None:
    client = make_client([SPEC, AUDIT_SPEC])
    runner = CallRunner(client)
    request = _request(closing=closing)
    result = await plan_validated(
        planner=PlanAdapter(runner, CATALOG),
        auditor=AuditAdapter(runner, CATALOG),
        ctx=CTX,
        request=request,
        vctx=_vctx(request),
        policy=InterviewPolicy(),
        catalog=CATALOG,
        statuses={},
        obligations=[],
        needs_table="",
        asked_list="",
    )
    assert result.rejected == [] and not result.used_fallback
    (card,) = result.batch.cards()
    assert card.origin == "closing" and card.type == "priority"
    assert [v.maps_to_need for v in result.verdicts] == [True]


def test_the_rendered_closing_prompt_is_short_and_carries_the_candidates_and_repair_notes() -> None:
    from mde_llm_gateway.render import PromptRenderer

    inp = _example_input().model_copy(update={"style_notes": ["Repair: Q-012 was rejected (x)"]})
    rendered = PromptRenderer().render(SPEC.spec, inp)
    assert "LAST question" in rendered.system and "Modes" not in rendered.system
    assert (
        "weekly meal plans" in rendered.user
        and "first 100 subscribers by December" in rendered.user
    )
    assert "untrusted_content" in rendered.user  # the candidate lists are wrapped as data
    assert "Q-012" in rendered.user and "Repair: Q-012 was rejected" in rendered.user
    batch = PromptRenderer().render(SPEC.spec, inp.model_copy(update={"mode": "initial"}))
    assert "Modes" in batch.system and "LAST question" not in batch.system


# ---------------------------------------------------------------- suggested features (the person named few)
SUGGESTED = ["weekly meal plans", "daily menu", "pause a delivery", "delivery tracking"]


def _suggested_request(**closing: object) -> PlanRequest:
    return _request(closing=ClosingContext(**closing))  # type: ignore[arg-type]


def test_the_input_prints_the_suggested_features_as_a_separate_list() -> None:
    request = _suggested_request(features=["daily menu"], suggested_features=SUGGESTED)
    wire = build_input(CATALOG, request)
    assert wire.closing.features == ["daily menu"] and wire.closing.suggested_features == SUGGESTED
    from mde_llm_gateway.render import PromptRenderer

    rendered = PromptRenderer().render(SPEC.spec, wire)
    assert "suggested_features" in rendered.user and "pause a delivery" in rendered.user
    assert "suggested_features" in rendered.system and "NOT named by the person" in rendered.system


def test_the_suggestion_cap_depends_on_how_many_features_the_person_named() -> None:
    from mde_compass.engine.question_validators import closing_suggestion_cap

    assert closing_suggestion_cap(None) == 2
    three = ClosingContext(features=["a one", "a two", "a three"], goals=["g"])
    assert closing_suggestion_cap(three) == 2  # enough of their own: the old cap
    none = ClosingContext()
    assert closing_suggestion_cap(none) == 8  # nothing stated: up to eight suggestions
    two = ClosingContext(features=["a one", "a two"], goals=["g"], constraints=["c"])
    assert closing_suggestion_cap(two) == 4  # 8 - 4 stated candidates
    crowded = ClosingContext(features=["a one", "a two"], goals=["g"] * 3, constraints=["c"] * 3)
    assert closing_suggestion_cap(crowded) == 2  # never below two


def test_no_stated_features_but_suggestions_give_a_valid_card_without_the_fallback() -> None:
    request = _suggested_request(suggested_features=SUGGESTED)
    inp = build_input(CATALOG, request)
    out = handle(inp)
    assert structural(inp, out) == []
    (q,) = out.questions
    assert len(q.options) == 4 and {o.basis for o in q.options} == {"suggestion"}
    batch = to_batch(CATALOG, out, request)
    (card,) = batch.cards()
    assert card.origin == "closing" and card.type == "priority" and not card.fallback
    assert validate_batch([card], _vctx(request)).rejected == []


async def test_no_stated_features_survives_the_pipeline_with_no_fallback() -> None:
    runner = CallRunner(make_client([SPEC, AUDIT_SPEC]))
    request = _suggested_request(suggested_features=SUGGESTED)
    result = await plan_validated(
        planner=PlanAdapter(runner, CATALOG),
        auditor=AuditAdapter(runner, CATALOG),
        ctx=CTX,
        request=request,
        vctx=_vctx(request),
        policy=InterviewPolicy(),
        catalog=CATALOG,
        statuses={},
        obligations=[],
        needs_table="",
        asked_list="",
    )
    assert result.rejected == [] and not result.used_fallback and result.fallbacks == 0
    (card,) = result.batch.cards()
    assert len(card.options) >= 3 and not card.fallback and card.origin == "closing"


def test_only_stated_features_the_mock_never_reaches_for_the_suggested_ones() -> None:
    stated = ["weekly meal plans", "daily menu", "morning delivery routes"]
    request = _suggested_request(features=stated, suggested_features=["live chat"])
    out = handle(build_input(CATALOG, request))
    labels = [o.label.removeprefix("[mock] ") for o in out.questions[0].options]
    assert labels == stated and {o.basis for o in out.questions[0].options} == {"your_words"}


def test_both_stated_and_suggested_own_first_then_suggestions_labelled() -> None:
    request = _suggested_request(
        features=["weekly meal plans", "daily menu"],
        suggested_features=["daily menu", "delivery tracking", "pause a delivery"],
    )
    inp = build_input(CATALOG, request)
    (q,) = handle(inp).questions
    labels = [o.label.removeprefix("[mock] ") for o in q.options]
    assert labels == ["weekly meal plans", "daily menu", "delivery tracking", "pause a delivery"]
    assert [o.basis for o in q.options] == ["your_words", "your_words", "suggestion", "suggestion"]
    (card,) = to_batch(CATALOG, handle(inp), request).cards()
    assert validate_batch([card], _vctx(request)).rejected == []


def _closing_card_with(options: list[tuple[str, str]], request: PlanRequest):  # type: ignore[no-untyped-def]
    q = _example_output().questions[0]
    from mde_compass.calls.plan_questions.schema import OptionWire

    wire = [
        OptionWire(label=label, description="", basis=basis, reason="") for label, basis in options
    ]  # type: ignore[arg-type]
    out = _example_output().model_copy(
        update={"questions": [q.model_copy(update={"options": wire})]}
    )
    return to_batch(CATALOG, out, request).cards()[0]


def test_an_option_built_from_a_suggested_feature_is_always_a_suggestion() -> None:
    request = _suggested_request(features=["daily menu"], suggested_features=SUGGESTED)
    card = _closing_card_with(
        [
            ("Daily menu", "your_words"),
            ("Pausing a delivery", "your_words"),
            ("Delivery tracking", "your_words"),
        ],
        request,
    )
    assert [o.basis for o in card.options] == ["your_words", "suggestion", "suggestion"]


def test_suggested_features_fill_the_card_up_to_the_cap_but_invented_ones_stay_at_two() -> None:
    request = _suggested_request(features=["daily menu"], suggested_features=SUGGESTED)
    options = [
        ("Daily menu", "your_words"),
        ("Weekly meal plans", "suggestion"),
        ("Pausing a delivery", "suggestion"),
        ("Delivery tracking", "suggestion"),
        ("Invented one", "suggestion"),
        ("Invented two", "suggestion"),
        ("Invented three", "suggestion"),
    ]
    card = _closing_card_with(options, request)
    assert [o.label for o in card.options] == [
        label for label, _ in options[:6]
    ]  # third invented one dropped
    assert validate_batch([card], _vctx(request)).rejected == []


def _hand_built(request: PlanRequest, labels: list[tuple[str, str]]):  # type: ignore[no-untyped-def]
    """A closing card with exactly these options, bypassing the adapter's own trimming."""
    from mde_compass.domain.cards import CardOption

    base = _closing_card_with([("Daily menu", "your_words")], request)
    options = [
        CardOption(id=chr(65 + i), label=label, basis=basis)  # type: ignore[arg-type]
        for i, (label, basis) in enumerate(labels)
    ]
    return base.model_copy(update={"options": options})


def _codes(card, request: PlanRequest) -> list[str]:  # type: ignore[no-untyped-def]
    return [i.code for i in validate_batch([card], _vctx(request)).issues.get(card.id, [])]


def test_with_nothing_stated_the_validator_allows_up_to_eight_suggestions() -> None:
    request = _suggested_request(suggested_features=[*SUGGESTED, "live chat", "gift cards"])
    five = [(label, "suggestion") for label in [*SUGGESTED, "live chat"]]
    assert _codes(_hand_built(request, five), request) == []


def test_but_invented_options_are_still_capped_at_two() -> None:
    request = _suggested_request(suggested_features=SUGGESTED)
    invented = [(f"Invented thing {n}", "suggestion") for n in ("one", "two", "three")]
    assert "closing_suggestions" in _codes(_hand_built(request, invented), request)
    two = [*invented[:2], (SUGGESTED[0], "suggestion")]
    assert _codes(_hand_built(request, two), request) == []


def test_with_three_features_stated_suggested_features_are_capped_at_two_too() -> None:
    request = _suggested_request(
        features=["kitchen schedule", "rider routes", "customer wallet"],
        suggested_features=SUGGESTED,
    )
    own = [("Kitchen schedule", "your_words"), ("Rider routes", "your_words")]
    two = [*own, (SUGGESTED[1], "suggestion"), (SUGGESTED[3], "suggestion")]
    assert _codes(_hand_built(request, two), request) == []
    three = [*two, (SUGGESTED[2], "suggestion")]
    assert "closing_suggestions" in _codes(_hand_built(request, three), request)


def test_a_suggestion_claiming_the_persons_words_is_refused_by_the_validator() -> None:
    request = _suggested_request(features=["daily menu"], suggested_features=SUGGESTED)
    lying = [
        ("Daily menu", "your_words"),
        (SUGGESTED[3], "your_words"),
        (SUGGESTED[2], "suggestion"),
    ]
    assert "closing_option_source" in _codes(_hand_built(request, lying), request)


async def test_the_fallback_uses_stated_then_suggested_features_and_only_then_the_registry_card() -> (
    None
):
    gateway = ScriptedGateway(ProviderError("boom"))
    adapter = PlanAdapter(CallRunner(gateway), CATALOG)
    only_suggested = _suggested_request(suggested_features=SUGGESTED)
    result = await adapter.plan(CTX, only_suggested)
    (card,) = result.batch.cards()
    assert result.used_fallback and card.fallback and card.origin == "closing"
    assert card.type == "priority" and [o.label for o in card.options] == SUGGESTED
    assert {o.basis for o in card.options} == {"suggestion"}
    both = _suggested_request(
        features=["my own menu"], suggested_features=["my own menu", *SUGGESTED]
    )
    (mixed,) = closing_fallback(CATALOG, both, batch_id="B", group_title="t").cards()
    assert [o.label for o in mixed.options] == ["my own menu", *SUGGESTED]
    assert [o.basis for o in mixed.options] == ["your_words", *["suggestion"] * 4]
    too_few = _suggested_request(suggested_features=["only one", "only two"])
    (registry,) = closing_fallback(CATALOG, too_few, batch_id="B", group_title="t").cards()
    assert registry.type == CATALOG.slots["goals.primary"].fallback_card.type and registry.fallback
