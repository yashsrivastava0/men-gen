"""``cmp.draft_outline``: spec, examples, the real pipeline in mock mode, the deterministic floor, the validator, the port."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.draft_outline.adapter import OutlineAdapter, build_structure_outliner
from mde_compass.calls.draft_outline.fallback import wire_fallback
from mde_compass.calls.draft_outline.mock import TAG, handle
from mde_compass.calls.draft_outline.schema import OutlineInput, OutlineOutput
from mde_compass.calls.draft_outline.spec import SPEC
from mde_compass.calls.draft_outline.validate import structural
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.engine.ports import TurnContext
from mde_compass.engine.structure_wire import FeatureWire
from mde_core.errors import ProviderError

EXAMPLES_DIR = Path(__file__).parents[3] / "src/mde_compass/calls/draft_outline/examples"
EXAMPLES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES_DIR.glob("*.json"))]
CTX = TurnContext(session_id="ses_1", tenant_id="t1")


def _input(raw: dict[str, object]) -> OutlineInput:
    return OutlineInput.model_validate(raw["input"])


def _output(raw: dict[str, object]) -> OutlineOutput:
    return OutlineOutput.model_validate(raw["output"])


def test_spec_is_short_standard_route_and_has_no_tools() -> None:
    s = SPEC.spec
    assert s.id == "cmp.draft_outline" and s.version == 1
    assert s.reasoning_effort == "low" and s.route == "standard" and not s.tools
    assert SPEC.lane == "writer" and SPEC.timeout_s <= 60  # the gateway caps one attempt at 60 s
    assert set(s.untrusted_inputs) <= set(OutlineInput.model_fields)
    assert {
        "features",
        "roles",
        "fact_digest",
        "seeds",
        "pinned",
        "instruction",
        "project_card",
    } <= set(s.untrusted_inputs)
    assert s.fallback == "compass.draft_outline" and s.mock_handler.endswith(
        "draft_outline.mock:handle"
    )


def test_there_are_examples_in_three_languages_that_pass_the_validator() -> None:
    assert {e["input"]["language"] for e in EXAMPLES} == {"en", "hi", "hi-Latn"}
    for raw in EXAMPLES:
        assert structural(_input(raw), _output(raw)) == [], raw["name"]


def test_the_output_wire_is_strict_schema_safe() -> None:
    from mde_llm_gateway.strict_schema import to_strict_schema

    schema = to_strict_schema(OutlineOutput)
    assert set(schema["required"]) == {"modules", "entities", "journeys"}  # type: ignore[index]


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
async def test_templates_render_and_the_mock_runs_through_the_real_pipeline(
    raw: dict[str, object],
) -> None:
    runner = CallRunner(make_client([SPEC]))
    inp = _input(raw)
    res = await runner.run(SPEC, inp, session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert res.output.modules and all(m.purpose.startswith(TAG) for m in res.output.modules)


def test_the_rendered_prompt_wraps_the_facts_as_data_and_states_the_rules() -> None:
    from mde_llm_gateway.render import PromptRenderer

    rendered = PromptRenderer().render(SPEC.spec, _input(EXAMPLES[0]))
    assert rendered.user.count("untrusted_content") >= 8  # card, each feature and role, digest ...
    assert "FTR-001" in rendered.user and "Size band: medium" in rendered.user
    for needle in ("GUIDANCE", "not a quota", "never invent", "inside those blocks", "pinned"):
        assert needle in rendered.system, needle


# ---------------------------------------------------------------------------------------------------- floor


def test_the_floor_is_pure_and_every_feature_in_scope_lands_in_a_module() -> None:
    inp = _input(EXAMPLES[0])
    assert wire_fallback(inp) == wire_fallback(inp)
    out = wire_fallback(inp)
    delivered = {f for m in out.modules for f in m.features}
    assert delivered == {
        f.id for f in inp.features if f.scope_status == "in"
    }  # the later feature gets no module
    assert any(m.kind == "account" for m in out.modules)  # the app signs in with a phone code
    refs = [m.ref for m in out.modules]
    assert len(set(refs)) == len(refs) and all(
        j.legs and set(j.legs) <= set(refs) for j in out.journeys
    )
    assert all(e.owner_module in refs for e in out.entities)


def test_the_floor_groups_features_of_one_family_and_names_the_module_after_it() -> None:
    inp = _input(EXAMPLES[0])
    feats = [
        FeatureWire(
            id=f"FTR-00{i}",
            name=f"Thing {i}",
            intent="",
            priority="must",
            scope_status="in",
            family="booking_scheduling",
            apps=[],
        )
        for i in (1, 2, 3)
    ]
    out = wire_fallback(inp.model_copy(update={"features": feats, "auth_methods": []}))
    assert len(out.modules) >= 1 and out.modules[0].features == ["FTR-001", "FTR-002", "FTR-003"]
    assert out.modules[0].name == load_catalog().families["booking_scheduling"].label.get("en")


def test_the_floor_follows_the_band_and_adds_the_standard_modules_the_band_allows() -> None:
    inp = _input(EXAMPLES[0])
    kinds = {}
    for band in ("small", "medium", "large"):
        from mde_compass.catalog.size_bands import get_band
        from mde_compass.engine.structure_wire import band_wire

        out = wire_fallback(inp.model_copy(update={"band": band_wire(get_band(band))}))
        kinds[band] = {m.kind for m in out.modules}
    assert "support:notifications" not in kinds["small"]
    assert "support:notifications" in kinds["medium"] and "support:help" not in kinds["medium"]
    assert {"support:help", "support:administration"} <= kinds[
        "large"
    ]  # three roles: administration applies


def test_the_floor_in_hindi_uses_hindi_names_and_hinglish_reads_english_templates() -> None:
    hi = wire_fallback(_input(next(e for e in EXAMPLES if e["input"]["language"] == "hi")))
    assert any("ऀ" <= ch <= "ॿ" for ch in "".join(m.name for m in hi.modules))
    hl = wire_fallback(_input(next(e for e in EXAMPLES if e["input"]["language"] == "hi-Latn")))
    assert hl.modules[0].name == "Account"


# --------------------------------------------------------------------------------------------------- mock


def test_the_mock_is_the_floor_with_a_tag_on_every_purpose() -> None:
    inp = _input(EXAMPLES[0])
    out = handle(inp)
    floor = wire_fallback(inp)
    assert [m.ref for m in out.modules] == [m.ref for m in floor.modules]
    assert all(m.purpose.startswith(TAG) for m in out.modules) and all(
        e.purpose.startswith(TAG) for e in out.entities
    )


# ----------------------------------------------------------------------------------------------- validator


def test_the_validator_refuses_only_what_cannot_be_used() -> None:
    inp = _input(EXAMPLES[0])
    out = _output(EXAMPLES[0])
    assert structural(inp, out) == []
    assert structural(
        inp, OutlineOutput(modules=[], entities=[], journeys=[])
    )  # features but no module
    dup = out.model_copy(update={"modules": [out.modules[0], out.modules[0]]})
    assert any("share a ref" in p for p in structural(inp, dup))
    blank = out.model_copy(update={"modules": [out.modules[0].model_copy(update={"name": " "})]})
    assert any("no name" in p for p in structural(inp, blank))
    no_features = inp.model_copy(update={"features": []})
    assert (
        structural(no_features, OutlineOutput(modules=[], entities=[], journeys=[])) == []
    )  # nothing to plan


# ----------------------------------------------------------------------------------------------- the port


async def test_the_port_returns_the_call_output_and_a_failed_call_the_flagged_floor() -> None:
    inp = _input(EXAMPLES[0])
    ok = OutlineAdapter(CallRunner(ScriptedGateway(_output(EXAMPLES[0]))), load_catalog())
    res = await ok.outline(CTX, inp)
    assert not res.used_fallback and res.output == _output(EXAMPLES[0])
    failing = build_structure_outliner(
        CallRunner(ScriptedGateway(ProviderError("boom"))), load_catalog()
    )
    res = await failing.outline(CTX, inp)
    assert res.used_fallback and res.reason and res.output == wire_fallback(inp)


async def test_an_output_the_validator_refuses_is_replaced_by_the_floor() -> None:
    inp = _input(EXAMPLES[0])
    empty = OutlineOutput(modules=[], entities=[], journeys=[])
    res = await OutlineAdapter(CallRunner(ScriptedGateway(empty)), load_catalog()).outline(CTX, inp)
    assert res.used_fallback and res.output.modules
