"""C3.respond: spec contract, templates, mock handler, golden fixtures."""

from __future__ import annotations

import re
from typing import Any

import pytest

from mde_content.questions import load_question_banks
from mde_contracts.calls import C3Input, C3Output, PendingCardLite
from mde_contracts.registry import CALL_IO
from mde_prompts.calls.c3_respond.spec import SPEC
from mde_prompts.discover_testkit import handler, load_cases, matches, model_depth, render
from mde_prompts.registry import load_spec

CASES = load_cases(SPEC)


def build_input(raw: dict[str, Any]) -> C3Input:
    data = dict(raw)
    lang = data.get("language", "en")
    data["knowledge"] = load_question_banks().knowledge(lang)
    return C3Input.model_validate(data)


def test_spec_is_registered_and_consistent() -> None:
    assert load_spec("C3.respond") is SPEC
    assert (SPEC.input_model, SPEC.output_model) == CALL_IO["C3.respond"]
    assert SPEC.route == "standard"
    assert SPEC.reasoning_effort == "low"
    assert SPEC.max_output_tokens == 500
    assert SPEC.tools == ()
    assert SPEC.untrusted_inputs == ()
    assert SPEC.fallback == "discovery.canned_reply"


def test_output_converts_to_strict_schema_shape() -> None:
    assert model_depth(C3Output) <= 5


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_golden_cases(case: dict[str, Any]) -> None:
    inp = build_input(case["input"])
    out = handler(SPEC)(inp)
    assert isinstance(out, C3Output)
    dump = out.model_dump(mode="json")
    assert matches(case["expect"], dump), (case["name"], dump)
    assert handler(SPEC)(inp) == out  # deterministic
    assert len(out.suggested_chips) <= 3


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_templates_render_with_strict_undefined(case: dict[str, Any]) -> None:
    system, user = render(SPEC, build_input(case["input"]))
    assert "never invent" in system.lower()
    assert case["input"]["message"] in user
    assert "kb-cost" in user  # the knowledge is given to the call


def test_replies_never_state_a_price_or_a_duration() -> None:
    for message in ("what is the price", "how long does it take", "do I own the code"):
        out = handler(SPEC)(build_input({"message": message, "intent": "question"}))
        assert not re.search(r"\d", out.reply), out.reply
        assert "$" not in out.reply


def test_unknown_reply_says_how_the_user_will_see_the_plan() -> None:
    out = handler(SPEC)(
        build_input({"message": "which database will you use?", "intent": "question"})
    )
    assert out.unknown and "plan" in out.reply.lower()


def test_pending_card_is_given_to_the_prompt() -> None:
    inp = build_input(
        {
            "message": "why do you ask?",
            "intent": "question",
            "pending_card": PendingCardLite(
                card_id="Q-auth-01",
                slot="auth.method",
                type="choice3",
                text="How should people sign in?",
            ),
        }
    )
    _, user = render(SPEC, inp)
    assert "How should people sign in?" in user
