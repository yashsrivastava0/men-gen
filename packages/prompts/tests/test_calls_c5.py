"""C5.write_card: spec contract, templates, mock handler, golden fixtures."""

from __future__ import annotations

from typing import Any

import pytest

from mde_contracts.calls import C5Input, C5Output
from mde_contracts.registry import CALL_IO
from mde_prompts.calls.c5_write_card.spec import SPEC
from mde_prompts.discover_testkit import handler, load_cases, matches, model_depth, render
from mde_prompts.registry import load_spec

CASES = load_cases(SPEC)


def test_spec_is_registered_and_consistent() -> None:
    assert load_spec("C5.write_card") is SPEC
    assert (SPEC.input_model, SPEC.output_model) == CALL_IO["C5.write_card"]
    assert SPEC.route == "standard"
    assert SPEC.reasoning_effort == "low"
    assert SPEC.max_output_tokens == 700
    assert SPEC.tools == ()
    assert SPEC.untrusted_inputs == ()
    assert SPEC.fallback == "discovery.options_card"


def test_output_converts_to_strict_schema_shape() -> None:
    assert model_depth(C5Output) <= 5


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_golden_cases(case: dict[str, Any]) -> None:
    inp = C5Input.model_validate(case["input"])
    out = handler(SPEC)(inp)
    assert isinstance(out, C5Output)
    dump = out.model_dump(mode="json")
    assert matches(case["expect"], dump), (case["name"], dump)
    assert handler(SPEC)(inp) == out  # deterministic
    if inp.card_type == "choice3":
        assert len(out.options) <= 3
        if out.options:
            assert sum(1 for o in out.options if o.recommended) == 1


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_templates_render_with_strict_undefined(case: dict[str, Any]) -> None:
    system, user = render(SPEC, C5Input.model_validate(case["input"]))
    assert "one question card" in system
    assert f"Slot: {case['input']['slot']}" in user


def test_every_option_sets_exactly_one_value_field() -> None:
    out = handler(SPEC)(
        C5Input(slot="data.source", archetype="commerce", options_hint=["generated", "sample"])
    )
    for option in out.options:
        set_fields = [
            v for v in (option.value_text, option.value_list, option.value_bool) if v is not None
        ]
        assert len(set_fields) == 1


def test_slot_hint_becomes_the_help_line() -> None:
    out = handler(SPEC)(
        C5Input(slot="data.source", archetype="commerce", slot_hint="Where the app gets its data")
    )
    assert out.help == "Where the app gets its data"
