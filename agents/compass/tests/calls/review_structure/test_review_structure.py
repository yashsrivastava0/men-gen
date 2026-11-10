"""``cmp.review_structure``: spec, examples, the real pipeline in mock mode, the silent floor, the validator, the port."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.review_structure.adapter import ReviewAdapter, build_structure_reviewer
from mde_compass.calls.review_structure.fallback import wire_fallback
from mde_compass.calls.review_structure.mock import handle
from mde_compass.calls.review_structure.schema import FindingOut, ReviewInput, ReviewOutput
from mde_compass.calls.review_structure.spec import SPEC
from mde_compass.calls.review_structure.validate import MAX_FINDINGS, structural
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.engine.ports import TurnContext
from mde_compass.engine.structure_wire import FINDING_CODES
from mde_core.errors import ProviderError

EXAMPLES_DIR = Path(__file__).parents[3] / "src/mde_compass/calls/review_structure/examples"
EXAMPLES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES_DIR.glob("*.json"))]
CTX = TurnContext(session_id="ses_1", tenant_id="t1")


def _input(raw: dict[str, object]) -> ReviewInput:
    return ReviewInput.model_validate(raw["input"])


def _output(raw: dict[str, object]) -> ReviewOutput:
    return ReviewOutput.model_validate(raw["output"])


def test_spec_is_a_findings_only_critic_with_no_tools() -> None:
    s = SPEC.spec
    assert s.id == "cmp.review_structure" and s.version == 1
    assert SPEC.lane == "critic" and s.route == "standard" and not s.tools and SPEC.timeout_s <= 60
    assert set(s.untrusted_inputs) == {"outline", "facts", "permissions"}
    assert s.fallback == "compass.review_structure" and s.mock_handler.endswith(
        "review_structure.mock:handle"
    )


def test_the_examples_pass_the_validator_and_use_the_known_finding_codes() -> None:
    assert {e["input"]["language"] for e in EXAMPLES} == {"en", "hi", "hi-Latn"}
    for raw in EXAMPLES:
        assert structural(_input(raw), _output(raw)) == [], raw["name"]
        assert {f.code for f in _output(raw).findings} <= set(FINDING_CODES)


def test_the_output_wire_is_strict_schema_safe() -> None:
    from mde_llm_gateway.strict_schema import to_strict_schema

    item = to_strict_schema(ReviewOutput)["properties"]["findings"]["items"]  # type: ignore[index]
    assert set(item["required"]) == set(FindingOut.model_fields)


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
async def test_templates_render_and_the_mock_runs_through_the_real_pipeline(
    raw: dict[str, object],
) -> None:
    res = await CallRunner(make_client([SPEC])).run(
        SPEC, _input(raw), session_id="ses_1", tenant_id="t1"
    )
    assert not res.used_fallback, res.reason
    assert res.output.findings == []  # offline, the code checks are the critic


def test_the_rendered_prompt_wraps_the_structure_as_data_and_asks_for_findings_only() -> None:
    from mde_llm_gateway.render import PromptRenderer

    rendered = PromptRenderer().render(SPEC.spec, _input(EXAMPLES[0]))
    assert rendered.user.count("untrusted_content") >= 3 and "SCR-008" in rendered.user
    for needle in (
        "REPORT problems only",
        "inconsistent_permission",
        "At most 8 findings",
        "empty list",
        "inside those blocks",
    ):
        assert needle in rendered.system, needle


def test_the_floor_and_the_mock_are_silent() -> None:
    inp = _input(EXAMPLES[0])
    assert wire_fallback(inp).findings == [] and handle(inp).findings == []


def test_the_validator_refuses_only_a_runaway_list() -> None:
    inp = _input(EXAMPLES[0])
    one = FindingOut(code="missing", ids=["SCR-001"], fact_id="", note="x")
    assert structural(inp, ReviewOutput(findings=[one] * MAX_FINDINGS)) == []
    assert structural(inp, ReviewOutput(findings=[one] * (MAX_FINDINGS + 1)))


async def test_the_port_returns_findings_and_a_failed_call_is_a_flagged_silence() -> None:
    inp = _input(EXAMPLES[0])
    ok = ReviewAdapter(CallRunner(ScriptedGateway(_output(EXAMPLES[0]))), load_catalog())
    res = await ok.review(CTX, inp)
    assert not res.used_fallback and res.output.findings[0].code == "missing"
    failing = build_structure_reviewer(
        CallRunner(ScriptedGateway(ProviderError("boom"))), load_catalog()
    )
    res = await failing.review(CTX, inp)
    assert res.used_fallback and res.output.findings == []
