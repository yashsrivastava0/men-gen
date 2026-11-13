"""``cmp.draft_module``: spec, examples, the real pipeline in mock mode, the deterministic floor (valid by construction),
the validator, the port."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.draft_module.adapter import ModuleAdapter, build_structure_drafter
from mde_compass.calls.draft_module.fallback import wire_fallback
from mde_compass.calls.draft_module.mock import TAG, handle
from mde_compass.calls.draft_module.schema import ModuleInput, ModuleOutput
from mde_compass.calls.draft_module.spec import SPEC
from mde_compass.calls.draft_module.validate import structural
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.catalog.structure_patterns import load_structure_patterns
from mde_compass.engine.ports import TurnContext
from mde_core.errors import ProviderError

EXAMPLES_DIR = Path(__file__).parents[3] / "src/mde_compass/calls/draft_module/examples"
EXAMPLES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES_DIR.glob("*.json"))]
CTX = TurnContext(session_id="ses_1", tenant_id="t1")
PATTERNS = set(load_structure_patterns().patterns)


def _input(raw: dict[str, object]) -> ModuleInput:
    return ModuleInput.model_validate(raw["input"])


def _output(raw: dict[str, object]) -> ModuleOutput:
    return ModuleOutput.model_validate(raw["output"])


def test_spec_is_standard_route_low_effort_and_has_no_tools() -> None:
    s = SPEC.spec
    assert s.id == "cmp.draft_module" and s.version == 1
    assert s.reasoning_effort == "low" and s.route == "standard" and not s.tools
    assert SPEC.lane == "writer" and SPEC.timeout_s <= 60
    assert set(s.untrusted_inputs) <= set(ModuleInput.model_fields)
    assert {"modules", "features", "journeys", "facts", "instruction", "issues", "existing"} <= set(
        s.untrusted_inputs
    )
    assert s.fallback == "compass.draft_module" and s.mock_handler.endswith(
        "draft_module.mock:handle"
    )


def test_the_examples_pass_the_validator_and_use_only_allowed_patterns_and_types() -> None:
    assert {e["input"]["language"] for e in EXAMPLES} == {"en", "hi"}
    for raw in EXAMPLES:
        inp, out = _input(raw), _output(raw)
        assert structural(inp, out) == [], raw["name"]
        assert {s.pattern for s in out.screens} <= PATTERNS
        assert {f.type for f in out.fields} <= set(load_structure_patterns().field_types)
        assert {u.use for u in out.uses} <= {"view", "create", "edit"}


def test_the_output_wire_is_flat_and_strict_schema_safe() -> None:
    from mde_llm_gateway.strict_schema import to_strict_schema

    schema = to_strict_schema(
        ModuleOutput
    )  # a strict schema may nest at most five levels: lists point at each other
    assert set(schema["required"]) == {  # type: ignore[index]
        "screens",
        "uses",
        "entries",
        "segments",
        "entities",
        "fields",
        "access",
        "journeys",
    }


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
async def test_templates_render_and_the_mock_runs_through_the_real_pipeline(
    raw: dict[str, object],
) -> None:
    runner = CallRunner(make_client([SPEC]))
    inp = _input(raw)
    res = await runner.run(SPEC, inp, session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert {s.module for s in res.output.screens} == {m.ref for m in inp.modules}
    assert all(s.purpose.startswith(TAG) for s in res.output.screens)


def test_the_rendered_prompt_wraps_the_group_as_data_and_states_the_consistency_rules() -> None:
    from mde_llm_gateway.render import PromptRenderer

    rendered = PromptRenderer().render(SPEC.spec, _input(EXAMPLES[0]))
    assert rendered.user.count("untrusted_content") >= 10
    assert "Allowed screen patterns:" in rendered.user and "calendar" in rendered.user
    for needle in (
        "flat lists",
        "at least that access",
        "somewhere to start",
        "never invent an id",
        "issues",
        "inside those blocks",
    ):
        assert needle in rendered.system, needle


# ---------------------------------------------------------------------------------------------------- floor


def test_the_floor_is_pure_and_drafts_every_requested_module_with_screens() -> None:
    inp = _input(EXAMPLES[0])
    assert wire_fallback(inp) == wire_fallback(inp)
    out = wire_fallback(inp)
    assert structural(inp, out) == []
    assert {s.module for s in out.screens} == {m.ref for m in inp.modules}
    assert {e.module for e in out.entries} == {m.ref for m in inp.modules}
    refs = {s.ref for s in out.screens}
    assert (
        all(e.screen in refs for e in out.entries) and {s.pattern for s in out.screens} <= PATTERNS
    )
    assert len(refs) == len(out.screens)


def test_the_floor_gives_every_feature_of_a_module_the_screens_its_band_asks_for() -> None:
    inp = _input(EXAMPLES[0])  # medium: three screens per feature
    out = wire_fallback(inp)
    serving = [s for s in out.screens if s.module == "m2" and "FTR-001" in s.serves]
    assert [s.pattern for s in serving] == ["list", "detail", "form"]
    assert all(s.name.startswith("Book appointments") for s in serving)  # the person's own words


def test_the_floors_access_is_consistent_by_construction() -> None:
    inp = _input(EXAMPLES[0])
    out = wire_fallback(inp)
    level = {(a.entity, a.role): a.level for a in out.access}
    screens = {s.ref: s for s in out.screens}
    assert out.uses and out.access
    for use in out.uses:
        for role in screens[use.screen].roles:
            assert level[(use.entity, role)] in ("edit", "view")
            assert use.use == "view" or level[(use.entity, role)] == "edit", (use, role)
    assert {a.entity for a in out.access} == {e.ref for e in inp.entities if e.mine}
    assert {f.entity for f in out.fields} == {e.ref for e in out.entities}


def test_the_floor_writes_segments_for_the_legs_it_owns_and_acceptance_for_the_journeys_it_owns() -> (
    None
):
    inp = _input(EXAMPLES[0])
    out = wire_fallback(inp)
    owned = {j.ref for j in inp.journeys if j.owner}
    assert {j.ref for j in out.journeys} == owned and all(j.acceptance for j in out.journeys)
    by_ref = {s.ref: s for s in out.screens}
    for seg in out.segments:
        assert seg.steps and all(by_ref[st.screen].module == seg.module for st in seg.steps)
    here = {(j.ref, leg.index) for j in inp.journeys for leg in j.legs if leg.here}
    assert {(seg.journey, seg.leg) for seg in out.segments} == here


def test_the_floor_in_hindi_and_the_mock_tags() -> None:
    hi = next(e for e in EXAMPLES if e["input"]["language"] == "hi")
    out = wire_fallback(_input(hi))
    assert any("\u0900" <= ch <= "\u097f" for ch in "".join(s.name for s in out.screens))
    tagged = handle(_input(EXAMPLES[0]))
    assert all(s.purpose.startswith(TAG) for s in tagged.screens)


# ----------------------------------------------------------------------------------------------- validator


def test_the_validator_refuses_only_what_cannot_be_used() -> None:
    inp = _input(EXAMPLES[0])
    out = _output(EXAMPLES[0])
    assert structural(inp, out) == []
    missing = out.model_copy(update={"screens": [s for s in out.screens if s.module != "m3"]})
    assert any("has no screens" in p for p in structural(inp, missing))
    dup = out.model_copy(update={"screens": [*out.screens, out.screens[0]]})
    assert any("share a ref" in p for p in structural(inp, dup))
    blank = out.model_copy(
        update={"screens": [out.screens[0].model_copy(update={"name": " "}), *out.screens[1:]]}
    )
    assert any("no name" in p for p in structural(inp, blank))


# ----------------------------------------------------------------------------------------------- the port


async def test_the_port_returns_the_call_output_and_a_failed_call_the_flagged_floor() -> None:
    inp = _input(EXAMPLES[0])
    ok = ModuleAdapter(CallRunner(ScriptedGateway(_output(EXAMPLES[0]))), load_catalog())
    res = await ok.draft(CTX, inp)
    assert not res.used_fallback and res.output == _output(EXAMPLES[0])
    failing = build_structure_drafter(
        CallRunner(ScriptedGateway(ProviderError("boom"))), load_catalog()
    )
    res = await failing.draft(CTX, inp)
    assert res.used_fallback and res.output == wire_fallback(inp)


async def test_a_draft_for_no_module_is_a_programming_error_not_a_call() -> None:
    inp = _input(EXAMPLES[0]).model_copy(update={"modules": []})
    adapter = ModuleAdapter(CallRunner(ScriptedGateway()), load_catalog())
    with pytest.raises(ValueError):
        await adapter.draft(CTX, inp)
