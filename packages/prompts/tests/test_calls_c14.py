"""C14.interpret_change: spec contract, templates, mock handler, golden fixtures."""

from __future__ import annotations

from typing import Any

import pytest

from mde_contracts.calls import (
    C14Input,
    C14Output,
    OutlineScreen,
    SlotBrief,
    SpecOutline,
)
from mde_contracts.registry import CALL_IO
from mde_prompts.calls.c14_interpret_change.spec import SPEC
from mde_prompts.discover_testkit import handler, load_cases, matches, model_depth, render
from mde_prompts.registry import load_spec

CASES = load_cases(SPEC)
SLOTS = [
    SlotBrief(slot_id="idea.archetype", value='"commerce"', state="confirmed"),
    SlotBrief(
        slot_id="scope.features",
        value='["catalog_browse", "reviews", "payments"]',
        state="confirmed",
    ),
]


def build_input(raw: dict[str, Any]) -> C14Input:
    return C14Input.model_validate({"slots": SLOTS, **raw})


def test_spec_is_registered_and_consistent() -> None:
    assert load_spec("C14.interpret_change") is SPEC
    assert (SPEC.input_model, SPEC.output_model) == CALL_IO["C14.interpret_change"]
    assert SPEC.route == "standard"
    assert SPEC.reasoning_effort == "medium"
    assert SPEC.max_output_tokens == 700
    assert SPEC.tools == ()
    assert SPEC.untrusted_inputs == ()
    assert SPEC.fallback == "discovery.use_the_buttons"


def test_output_converts_to_strict_schema_shape() -> None:
    assert model_depth(C14Output) <= 5


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_golden_cases(case: dict[str, Any]) -> None:
    inp = build_input(case["input"])
    out = handler(SPEC)(inp)
    assert isinstance(out, C14Output)
    dump = out.model_dump(mode="json")
    assert matches(case["expect"], dump), (case["name"], dump)
    if case["expect"].get("understood") is False:
        assert out.clarify_question and not out.ops
    if case["expect"].get("ops") == []:
        assert out.ops == []
    assert handler(SPEC)(inp) == out  # deterministic


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_templates_render_with_strict_undefined(case: dict[str, Any]) -> None:
    system, user = render(SPEC, build_input(case["input"]))
    assert "Copy only what the text states" in system
    assert case["input"]["message"] in user


def test_prompt_has_examples_including_hindi_and_hinglish() -> None:
    system, _ = render(SPEC, build_input({"message": "x", "state": "BRIEF_REVIEW"}))
    assert any("ऀ" <= ch <= "ॿ" for ch in system)
    assert "nahi chahiye" in system
    assert "Examples" in system


def test_a_screen_is_removed_by_name_from_the_outline() -> None:
    outline = SpecOutline(
        screens=[
            OutlineScreen(id="SCR-002", route="/cart", recipe="list", purpose="Review the cart"),
            OutlineScreen(id="SCR-003", route="/wishlist", recipe="list", purpose="Saved items"),
        ]
    )
    out = handler(SPEC)(
        build_input(
            {
                "message": "remove the wishlist screen",
                "state": "PLAN_REVIEW",
                "gate_open": "plan",
                "outline": outline,
            }
        )
    )
    assert [(o.kind, o.target_id) for o in out.ops] == [("remove_screen", "SCR-003")]


def test_new_screen_is_proposed_as_an_instruction() -> None:
    out = handler(SPEC)(
        build_input(
            {"message": "add a store locator page", "state": "PLAN_REVIEW", "gate_open": "plan"}
        )
    )
    assert [(o.kind, o.instruction) for o in out.ops] == [("add_screen", "store locator")]


def test_outline_and_selection_reach_the_prompt() -> None:
    outline = SpecOutline(
        screens=[
            OutlineScreen(id="SCR-002", route="/cart", recipe="list", purpose="Review the cart")
        ]
    )
    _, user = render(
        SPEC,
        build_input(
            {
                "message": "move the price",
                "state": "PLAN_REVIEW",
                "outline": outline,
                "selected_screen_id": "SCR-002",
            }
        ),
    )
    assert "SCR-002" in user and "Review the cart" in user
