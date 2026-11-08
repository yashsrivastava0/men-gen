"""The probe_handoff templates pick the job by ``kind`` and wrap the digest as data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.probe_handoff.schema import ProbeHandoffInput
from mde_compass.calls.probe_handoff.spec import SPEC
from mde_llm_gateway.render import PromptRenderer

EX = Path(__file__).parents[3] / "src/mde_compass/calls/probe_handoff/examples"
JOB = {
    "research": "the RESEARCH planner",
    "blueprint": "the BLUEPRINT planner",
    "design": "the DESIGN planner",
}


@pytest.mark.parametrize("kind", sorted(JOB))
def test_the_prompt_asks_for_exactly_one_job(kind: str) -> None:
    base = json.loads((EX / "en_research_food_pune.json").read_text(encoding="utf-8"))["input"]
    inp = ProbeHandoffInput.model_validate({**base, "kind": kind})
    prompt = PromptRenderer().render(SPEC.spec, inp)
    assert JOB[kind] in prompt.system
    assert all(text not in prompt.system for k, text in JOB.items() if k != kind)
    assert prompt.user.count("<untrusted_content") == 1 and f"Probe: {kind}" in prompt.user
    assert "ASK, never guess" in prompt.system
    assert SPEC.spec.tools == () and SPEC.spec.untrusted_inputs == ("digest",)
