"""``cmp.rephrase_question``: spec, examples, mock labelling, validator, the flagged fallback, adapter mapping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.rephrase_question.adapter import (
    RephraseAdapter,
    build_input,
    recent_words,
    to_result,
    unchanged,
)
from mde_compass.calls.rephrase_question.fallback import wire_fallback
from mde_compass.calls.rephrase_question.mock import handle
from mde_compass.calls.rephrase_question.schema import OptionWire, RephraseInput, RephraseOutput
from mde_compass.calls.rephrase_question.spec import SPEC
from mde_compass.calls.rephrase_question.validate import MAX_QUESTION_WORDS, structural
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.cards import Card, CardOption
from mde_compass.engine.ports import RephraseRequest, TurnContext, TurnLine
from mde_core.errors import ProviderError

EXAMPLES_DIR = Path(__file__).parents[3] / "src/mde_compass/calls/rephrase_question/examples"
EXAMPLES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES_DIR.glob("*.json"))]
CATALOG = load_catalog()
CTX = TurnContext(session_id="ses_1", tenant_id="t1")


def _input(raw: dict[str, object]) -> RephraseInput:
    return RephraseInput.model_validate(raw["input"])


def _output(raw: dict[str, object]) -> RephraseOutput:
    return RephraseOutput.model_validate(raw["output"])


def _card(
    question: str = "When a patient cancels late, what should happen to the slot?",
    labels: tuple[str, ...] = (
        "Offer it to a waiting list",
        "Keep it for walk-ins",
        "Leave it empty",
    ),
) -> Card:
    options = [
        CardOption(
            id=chr(ord("A") + i),
            label=label,
            basis="typical" if i == 0 else "suggestion",
            recommended=i == 1,
            reason="a short reason" if i == 1 else "",
            sets={"slot_rule": label} if i == 0 else {},
            ticked=i == 2,
            description="old description" if i == 0 else "",
        )
        for i, label in enumerate(labels)
    ]
    return Card(
        id="Q-004",
        frame="planned",
        slots=[],
        type="choose_one" if options else "open_prompt",
        question=question,
        why_we_ask="It decides if the app needs a waiting list.",
        options=options,
        intent="learn what happens to a cancelled slot",
        answer_spec="Whether a freed slot goes to a waiting list, to walk-ins or stays empty",
        need_ids=["scope.features"],
    )


def _out(
    question: str = "A patient cancels this morning. What happens to that time?",
    labels: tuple[str, ...] = (
        "Give it to someone waiting",
        "Keep it for walk-ins",
        "Leave it empty",
    ),
    **kw: object,
) -> RephraseOutput:
    base: dict[str, object] = {
        "question": question,
        "why_we_ask": "This decides if the app needs a waiting list.",
        "options": [OptionWire(label=label, description="") for label in labels],
        "examples": [],
    }
    base.update(kw)
    return RephraseOutput.model_validate(base)


def _wire(mode: str = "simplify", card: Card | None = None) -> RephraseInput:
    c = card or _card()
    return build_input(CTX, RephraseRequest(card=c, mode=mode))  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------------- spec, examples
def test_spec_matches_profile() -> None:
    s = SPEC.spec
    assert s.id == "cmp.rephrase_question" and s.version == 1
    assert s.reasoning_effort == "medium" and s.max_output_tokens == 1500 and s.route == "standard"
    assert SPEC.lane == "writer" and SPEC.timeout_s == 45 and not s.tools
    assert s.untrusted_inputs == ("recent_user_words",)
    assert set(s.untrusted_inputs) <= set(RephraseInput.model_fields)


def test_there_are_three_examples_in_three_languages_that_pass_the_validator() -> None:
    assert len(EXAMPLES) >= 3
    assert {e["input"]["language"] for e in EXAMPLES} >= {"en", "hi-Latn", "hi"}
    assert {e["input"]["mode"] for e in EXAMPLES} == {"simplify", "rephrase"}
    assert len({e["name"] for e in EXAMPLES}) == len(EXAMPLES)
    for e in EXAMPLES:
        inp, out = _input(e), _output(e)
        assert structural(inp, out) == [], e["name"]
        # the examples keep to the prompt's own limits, not only the validator's
        assert len(out.question.split()) <= 20 and all(
            len(o.label.split()) <= 8 for o in out.options
        )
        assert [bool(o.label) for o in out.options] == [True] * len(inp.options)
    # one example has no options at all, and shows placeholder hints instead
    free = next(e for e in EXAMPLES if not e["input"]["options"])
    assert _output(free).examples and _output(free).options == []


def test_the_prompt_states_what_stays_fixed() -> None:
    system = (EXAMPLES_DIR.parent / "system.jinja").read_text(encoding="utf-8")
    for needle in (
        "EXACTLY as many options",
        "same order",
        "intent and the answer spec are fixed",
        "no invented numbers",
        'mode "simplify"',
        'mode "rephrase"',
        "at most about 20 words",
        "at most 8 words",
    ):
        assert needle in system, needle


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
async def test_templates_render_and_the_mock_runs_through_the_real_pipeline(
    raw: dict[str, object],
) -> None:
    runner = CallRunner(make_client([SPEC]))
    inp = _input(raw)
    res = await runner.run(SPEC, inp, session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert res.output.question.startswith(
        "[mock] simpler: " if inp.mode == "simplify" else "[mock] rephrased: "
    )
    assert [o.label for o in res.output.options] == [o.label for o in inp.options]


def test_the_user_template_shows_the_question_the_options_and_the_untrusted_block() -> None:
    from mde_llm_gateway.render import PromptRenderer

    inp = _input(EXAMPLES[0])
    rendered = PromptRenderer().render(SPEC.spec, inp)
    assert inp.question in rendered.user and f"return exactly {len(inp.options)}" in rendered.user
    assert "<untrusted_content" in rendered.user and inp.recent_user_words in rendered.user
    assert "1. " in rendered.user and "3. " in rendered.user
    free = next(e for e in EXAMPLES if not e["input"]["options"])
    assert "Options: none" in PromptRenderer().render(SPEC.spec, _input(free)).user


# -------------------------------------------------------------------------------------------- the mock
def test_mock_prefixes_the_question_and_keeps_every_label_and_description() -> None:
    inp = _input(EXAMPLES[0])
    for mode, prefix in (("simplify", "[mock] simpler: "), ("rephrase", "[mock] rephrased: ")):
        out = handle(inp.model_copy(update={"mode": mode}))
        assert out.question == prefix + inp.question
        assert out.options == inp.options and out.why_we_ask == inp.why_we_ask
        assert structural(inp.model_copy(update={"mode": mode}), out) == []


# ----------------------------------------------------------------------------------------- the validator
def test_structural_accepts_a_proper_rewrite() -> None:
    inp = _wire()
    assert structural(inp, _out()) == []


def test_must_not_change_the_option_count() -> None:
    inp = _wire()
    fewer = _out(labels=("Give it to someone waiting", "Keep it for walk-ins"))
    more = _out(labels=("A", "B", "C", "D"))
    none = _out(labels=())
    for bad in (fewer, more, none):
        assert any("option count changed" in e for e in structural(inp, bad))
    # and a free-text question must stay without options
    free = _wire(card=_card(labels=()))
    assert structural(free, _out(labels=())) == []
    assert any("option count changed" in e for e in structural(free, _out(labels=("x",))))


def test_structural_checks_question_words_and_labels() -> None:
    inp = _wire()
    assert any("empty question" in e for e in structural(inp, _out(question="  ")))
    long_q = " ".join(["word"] * (MAX_QUESTION_WORDS + 1)) + "?"
    assert any("at most 30" in e for e in structural(inp, _out(question=long_q)))
    at_limit = " ".join(["word"] * MAX_QUESTION_WORDS) + "?"
    assert structural(inp, _out(question=at_limit)) == []
    long_label = " ".join(["word"] * 13)
    assert any(
        "more than 12 words" in e for e in structural(inp, _out(labels=(long_label, "b", "c")))
    )
    assert structural(inp, _out(labels=(" ".join(["w"] * 12), "b", "c"))) == []
    assert any("empty label" in e for e in structural(inp, _out(labels=("", "b", "c"))))
    assert any("same label" in e for e in structural(inp, _out(labels=("Same", "same!", "c"))))


@pytest.mark.parametrize("mode", ["simplify", "rephrase"])
def test_the_question_must_actually_change(mode: str) -> None:
    inp = _wire(mode)
    same = _out(question=inp.question)
    shouting = _out(question=inp.question.upper().replace("?", " ?"))
    for bad in (same, shouting):
        assert any("unchanged" in e and mode in e for e in structural(inp, bad))


# ------------------------------------------------------------------------------------------ adapter
def test_build_input_carries_the_card_and_the_recent_words_of_the_person_only() -> None:
    ctx = TurnContext(
        session_id="ses_1",
        tenant_id="t1",
        recent_turns=[
            TurnLine(role="user", text="first"),
            TurnLine(role="assistant", text="the assistant says hello"),
            TurnLine(role="user", text="second"),
            TurnLine(role="user", text="  "),
            TurnLine(role="user", text="third"),
            TurnLine(role="user", text="fourth"),
        ],
    )
    card = _card()
    wire = build_input(ctx, RephraseRequest(card=card, mode="simplify", language="hi-Latn"))
    assert (wire.language, wire.mode, wire.type) == ("hi-Latn", "simplify", "choose_one")
    assert wire.question == card.question and wire.intent == card.intent
    assert wire.answer_spec == card.answer_spec and wire.why_we_ask == card.why_we_ask
    assert [(o.label, o.description) for o in wire.options] == [
        (o.label, o.description) for o in card.options
    ]
    assert wire.project_terms == []
    assert wire.recent_user_words == "second | third | fourth"  # the last three of the person
    assert recent_words(TurnContext(session_id="s", tenant_id="t")) == ""


def test_build_input_for_an_older_card_without_intent_still_validates() -> None:
    card = Card(
        id="Q-001",
        frame="idea",
        slots=["idea.summary"],
        type="open_prompt",
        question="Describe it.",
    )
    wire = build_input(CTX, RephraseRequest(card=card))
    assert wire.mode == "rephrase" and wire.intent == "" and wire.options == []


def test_the_wire_input_redacts_secrets_in_everything_that_carries_words() -> None:
    key = "AKIAIOSFODNN7EXAMPLE"  # secret-scan: allow
    card = _card(f"Is {key} the slot key?", labels=(f"use {key}", "b", "c")).model_copy(
        update={"why_we_ask": f"because {key}", "intent": f"key {key}"}
    )
    ctx = TurnContext(
        session_id="s", tenant_id="t", recent_turns=[TurnLine(role="user", text=f"my key is {key}")]
    )
    wire = build_input(ctx, RephraseRequest(card=card))
    assert key not in wire.model_dump_json()
    assert "[REDACTED]" in wire.question and "[REDACTED]" in wire.options[0].label
    assert "[REDACTED]" in wire.why_we_ask and "[REDACTED]" in wire.recent_user_words


def test_to_result_keeps_the_original_option_objects_and_replaces_only_wording() -> None:
    card = _card()
    out = _out(
        labels=("Give it to someone waiting", "Keep it for walk-ins", "Leave it empty"),
    ).model_copy(
        update={
            "options": [
                OptionWire(label=" Give it to someone waiting ", description=" a note "),
                OptionWire(label="Keep it for walk-ins", description=""),
                OptionWire(label="Leave it empty", description=""),
            ]
        }
    )
    result = to_result(card, out)
    assert not result.used_fallback
    assert result.question == out.question and result.why_we_ask == out.why_we_ask
    for original, new in zip(card.options, result.options, strict=True):
        assert (new.id, new.basis, new.recommended, new.reason, new.sets, new.ticked) == (
            original.id,
            original.basis,
            original.recommended,
            original.reason,
            original.sets,
            original.ticked,
        )
    assert [o.label for o in result.options] == [
        "Give it to someone waiting",
        "Keep it for walk-ins",
        "Leave it empty",
    ]
    assert [o.description for o in result.options] == ["a note", "", ""]
    assert result.options[1].recommended and result.options[0].sets == {
        "slot_rule": "Offer it to a waiting list"
    }
    # the originals are untouched
    assert card.options[0].label == "Offer it to a waiting list"


def test_to_result_keeps_the_old_reason_for_asking_when_the_model_leaves_it_empty() -> None:
    card = _card()
    result = to_result(card, _out(why_we_ask="  "))
    assert result.why_we_ask == card.why_we_ask


def test_must_not_to_result_never_changes_the_option_count() -> None:
    card = _card()
    for labels in (("a", "b"), ("a", "b", "c", "d"), ()):
        result = to_result(card, _out(labels=labels))
        assert result.used_fallback  # refused: the card comes back as it was
        assert result.options == card.options and result.question == card.question


def test_unchanged_returns_the_original_wording() -> None:
    card = _card()
    r = unchanged(card, used_fallback=True)
    assert (r.question, r.why_we_ask, r.options, r.used_fallback) == (
        card.question,
        card.why_we_ask,
        card.options,
        True,
    )


def test_the_wire_fallback_is_the_original_unchanged() -> None:
    inp = _input(EXAMPLES[0])
    out = wire_fallback(inp)
    assert out.question == inp.question and out.options == inp.options and out.examples == []


# ------------------------------------------------------------------------------------- adapter end to end
async def test_the_mock_adapter_labels_the_question_and_keeps_the_option_ids() -> None:
    card = _card()
    adapter = RephraseAdapter(CallRunner(make_client([SPEC])), CATALOG)
    for mode, prefix in (("simplify", "[mock] simpler: "), ("rephrase", "[mock] rephrased: ")):
        result = await adapter.rephrase(CTX, RephraseRequest(card=card, mode=mode))  # type: ignore[arg-type]
        assert not result.used_fallback and result.question == prefix + card.question
        assert [o.id for o in result.options] == ["A", "B", "C"]
        assert [o.label for o in result.options] == [o.label for o in card.options]
        assert result.options[1].recommended


async def test_a_scripted_rewrite_is_mapped_onto_the_original_options() -> None:
    card = _card()
    gateway = ScriptedGateway(by_spec={"cmp.rephrase_question": [_out()]})
    result = await RephraseAdapter(CallRunner(gateway), CATALOG).rephrase(
        CTX, RephraseRequest(card=card, mode="simplify")
    )
    gateway.assert_consumed()
    assert result.question.startswith("A patient cancels this morning")
    assert result.options[0].label == "Give it to someone waiting"
    assert [o.id for o in result.options] == ["A", "B", "C"] and result.options[1].recommended
    sent = gateway.calls[0][1]
    assert isinstance(sent, RephraseInput) and sent.mode == "simplify"


async def test_a_failed_call_gives_the_original_card_flagged_as_a_fallback() -> None:
    card = _card()
    gateway = ScriptedGateway(ProviderError("boom"))
    result = await RephraseAdapter(CallRunner(gateway), CATALOG).rephrase(
        CTX, RephraseRequest(card=card, mode="simplify")
    )
    assert result.used_fallback
    assert result.question == card.question and result.options == card.options
    assert result.why_we_ask == card.why_we_ask


@pytest.mark.parametrize(
    ("bad", "expected"),
    [
        (_out(labels=("a", "b")), "option count changed"),
        (_out(question=" "), "empty question"),
        (
            _out(question="When a patient cancels late, what should happen to the slot?"),
            "unchanged",
        ),
        (_out(question=" ".join(["w"] * 31)), "at most 30"),
    ],
)
async def test_a_structurally_bad_answer_falls_back_to_the_unchanged_card(
    bad: RephraseOutput, expected: str
) -> None:
    card = _card()
    adapter = RephraseAdapter(CallRunner(ScriptedGateway(bad)), CATALOG)
    result = await adapter.rephrase(CTX, RephraseRequest(card=card, mode="rephrase"))
    assert result.used_fallback and result.question == card.question
    assert result.options == card.options
    # the reason is in the wire-level fallback; check it through the runner too
    run = await CallRunner(ScriptedGateway(bad)).run(
        SPEC, _wire("rephrase"), session_id="ses_1", tenant_id="t1"
    )
    assert run.used_fallback and expected in run.reason


def test_project_terms_go_in_and_text_question_examples_come_back() -> None:
    from mde_compass.calls.rephrase_question.adapter import build_input, to_result
    from mde_compass.calls.rephrase_question.schema import RephraseOutput
    from mde_compass.domain.cards import Card
    from mde_compass.engine.ports import RephraseRequest, TurnContext

    card = Card(
        id="Q-010",
        frame="planned",
        slots=[],
        type="open_prompt",
        question="Where do shops keep their stock list today?",
        origin="planner",
    )
    request = RephraseRequest(card=card, mode="simplify", project_terms=["pharmacy", "stock"])
    wire = build_input(TurnContext(session_id="ses_1", tenant_id="t1"), request)
    assert wire.project_terms == ["pharmacy", "stock"]
    out = RephraseOutput(
        question="Where is the stock list kept?",
        why_we_ask="It lets us start from what you have.",
        options=[],
        examples=["e.g. a register", " ", "e.g. an Excel sheet"],
    )
    result = to_result(card, out)
    assert (
        result.examples == ["e.g. a register", "e.g. an Excel sheet"] and not result.used_fallback
    )
