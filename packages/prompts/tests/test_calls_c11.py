"""C11.write_summary: spec contract, templates, mock handler, golden fixtures."""

from __future__ import annotations

from typing import Any

import pytest

from mde_contracts.calls import C11Input, C11Output
from mde_contracts.registry import CALL_IO
from mde_prompts.calls.c11_write_summary.spec import SPEC
from mde_prompts.discover_testkit import handler, load_cases, matches, model_depth, render
from mde_prompts.registry import load_spec

CASES = load_cases(SPEC)


def test_spec_is_registered_and_consistent() -> None:
    assert load_spec("C11.write_summary") is SPEC
    assert (SPEC.input_model, SPEC.output_model) == CALL_IO["C11.write_summary"]
    assert SPEC.route == "standard"
    assert SPEC.reasoning_effort == "medium"
    assert SPEC.max_output_tokens == 800  # live L2 used up to 290 (Hindi) at medium effort (D-082)
    assert SPEC.tools == ()
    assert SPEC.untrusted_inputs == ()
    assert SPEC.cacheable is True
    assert SPEC.fallback == "discovery.template_sentence"


def test_output_converts_to_strict_schema_shape() -> None:
    assert model_depth(C11Output) <= 5


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_golden_cases(case: dict[str, Any]) -> None:
    inp = C11Input.model_validate(case["input"])
    out = handler(SPEC)(inp)
    assert isinstance(out, C11Output)
    assert matches(case["expect"], out.model_dump(mode="json")), (case["name"], out.text)
    assert len(out.text.split()) <= inp.max_words
    assert handler(SPEC)(inp) == out  # deterministic


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_templates_render_with_strict_undefined(case: dict[str, Any]) -> None:
    system, user = render(SPEC, C11Input.model_validate(case["input"]))
    assert "Never add features" in system
    assert "Facts:" in user


def test_every_fact_reaches_the_prompt() -> None:
    inp = C11Input(kind="brief_sentence", facts=["alpha fact", "beta fact"], app_name="Zed")
    _, user = render(SPEC, inp)
    assert "alpha fact" in user and "beta fact" in user and "Zed" in user
