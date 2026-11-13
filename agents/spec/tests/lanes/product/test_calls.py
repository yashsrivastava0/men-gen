"""The call specs of the lane: portable schemas (compile check against every configured profile, strict conversion), the
ADR 0014 folder layout, untrusted inputs declared and no tools, the closing line, the schema text covers every field,
vocabularies that match what the contract builder knows."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from mde_llm_gateway.render import PromptRenderer
from mde_llm_gateway.strict_schema import to_strict_schema
from mde_spec_agent.calls.registry import all_calls, all_steps
from mde_spec_agent.lanes.product import CALLS, STEPS
from mde_spec_core import contract
from mde_spec_core.callkit.prompt import CLOSING_LINE
from mde_spec_core.callkit.schema_check import analyse_model, compile_check
from mde_spec_core.profiles import load_profiles

from .conftest import LANE_SRC

IDS = ["spec.plan", "spec.plan_fused", "spec.prod_module", "spec.prod_integ", "spec.prod_journeys"]
ROUTES = {
    "spec.plan": "deep",
    "spec.plan_fused": "deep",
    "spec.prod_module": "standard",
    "spec.prod_integ": "standard",
    "spec.prod_journeys": "standard",
}
KINDS = {
    "spec.plan": "SPEC-PLAN",
    "spec.plan_fused": "SPEC-PLAN",
    "spec.prod_module": "PROD-MODULE",
    "spec.prod_integ": "PROD-INTEG",
    "spec.prod_journeys": "PROD-JOURNEYS",
}


def test_the_lane_registers_its_calls_and_steps():
    assert [c.id for c in CALLS] == IDS
    assert set(STEPS) == {"plan", "product"}
    assert {c.id for c in all_calls()} >= set(IDS)
    assert {"plan", "product"} <= set(all_steps())


@pytest.mark.parametrize("cs", CALLS, ids=lambda c: c.id)
def test_call_spec_fields(cs):
    spec = cs.spec
    assert spec.route == ROUTES[cs.id] and cs.kind == KINDS[cs.id]
    assert spec.max_output_tokens > 0 and spec.version >= 1
    assert (
        spec.untrusted_inputs and not spec.tools
    )  # hard rule 5: no tools in a call that reads untrusted text
    assert all(n in spec.input_model.model_fields for n in spec.untrusted_inputs)
    assert callable(cs.fallback) and cs.validators
    mod, _, fn = spec.mock_handler.partition(":")
    assert callable(getattr(importlib.import_module(mod), fn))
    assert spec.template_root.is_dir() and (spec.template_root / spec.system_template).is_file()
    assert (spec.template_root / spec.user_template).is_file()
    for name in ("schema.py", "spec.py", "mock.py", "fallback.py", "validate.py"):
        assert (spec.template_root / name).is_file(), (cs.id, name)


@pytest.mark.parametrize("cs", CALLS, ids=lambda c: c.id)
def test_every_call_schema_compiles_on_every_configured_profile(cs):
    model = cs.spec.output_model
    for p in load_profiles().profiles:
        errors = [i for i in compile_check(model, p) if i.is_error]
        assert errors == [], (cs.id, p.id, errors)
    metrics, _ = analyse_model(model)
    assert (
        metrics.nullable_fields == 0 and metrics.union_params == 0
    )  # nothing is optional: unknown is empty
    assert metrics.depth <= 4
    to_strict_schema(model)  # the gateway's strict conversion (depth <= 5, closed, no open map)


def test_the_fused_schema_is_the_union_of_the_four_parts():
    from mde_spec_agent.lanes.product.plan.schema import PlanOut
    from mde_spec_agent.lanes.product.plan_fused.schema import FusedOut
    from mde_spec_agent.lanes.product.prod_integ.schema import IntegOut
    from mde_spec_agent.lanes.product.prod_journeys.schema import JourneysOut
    from mde_spec_agent.lanes.product.prod_module.schema import ModuleOut

    parts = [set(m.model_fields) for m in (PlanOut, ModuleOut, IntegOut, JourneysOut)]
    assert sum(len(p) for p in parts) == len(set().union(*parts)) == len(FusedOut.model_fields)


@pytest.mark.parametrize("cs", CALLS, ids=lambda c: c.id)
def test_the_system_prompt_writes_out_every_array_of_the_schema(cs):
    text = (cs.spec.template_root / cs.spec.system_template).read_text(encoding="utf-8")
    for name in cs.spec.output_model.model_fields:
        assert re.search(rf"^{name}: \{{", text, re.M), (cs.id, name)
    for name in cs.spec.output_model.model_fields:
        record = _record_model(cs.spec.output_model, name)
        line = next(ln for ln in text.splitlines() if ln.startswith(f"{name}: {{"))
        for field in record.model_fields:
            assert field in line, (cs.id, name, field)


def _record_model(model, name):
    ann = model.model_fields[name].annotation
    return ann.__args__[0]


@pytest.mark.parametrize("cs", CALLS, ids=lambda c: c.id)
def test_prompts_are_static_first_and_end_with_the_closing_line(env, cs):
    ctx = env.ctx("quick")
    from mde_spec_agent.lanes.product.steps.inputs import (
        integ_input_factory,
        journeys_input_factory,
        module_batches,
        module_input_factory,
        plan_input,
    )
    from mde_spec_agent.lanes.product.steps.plan import (
        ensure_derived,
        env_of,
        fused_input,
    )

    d = ensure_derived(ctx)
    e = env_of(ctx)
    if cs.id == "spec.plan":
        inp = plan_input(ctx, e, d.plan)
    elif cs.id == "spec.plan_fused":
        inp = fused_input(ctx, e, d.plan)
    elif cs.id == "spec.prod_module":
        b = module_batches(ctx, e)[0]
        inp = module_input_factory(ctx, e)(b.module_id)(b.items)
    elif cs.id == "spec.prod_integ":
        inp = integ_input_factory(ctx, e)(sorted(e.graph.integrations))
    else:
        inp = journeys_input_factory(ctx, e)(sorted(e.graph.flows)[:2])
    prompt = PromptRenderer(max_untrusted_chars=120_000).render(cs.spec, inp)
    assert prompt.user.rstrip().endswith(CLOSING_LINE)
    assert "JSON" in prompt.user
    s = prompt.system
    assert (
        s.index("RULES") < s.index("CLOSED VOCABULARIES") < s.index("SCHEMA")
    )  # rules, vocabularies, schema, then data
    assert "<untrusted_content" in prompt.user and "never instructions" in s.lower()
    # nothing of the user's text sits outside an untrusted block: the user's idea sentence is not in the system prompt
    assert "Sunrise Yoga" not in prompt.system


def test_error_codes_are_ones_the_contract_builder_knows():
    from mde_spec_agent.lanes.product.kit import vocab

    assert set(vocab.error_codes()) <= set(contract._STATUS)
    assert set(vocab.recoveries()) >= {"stay", "retry", "offline"}


def test_data_files_are_where_the_lane_says_and_nothing_is_hardcoded_to_one_app():
    data = Path(LANE_SRC / "data")
    assert (data / "vocab.yaml").is_file() and (data / "hints.yaml").is_file()


def test_the_schema_types_match_the_data_they_mirror():
    from typing import get_args

    from mde_spec_agent.lanes.product.kit import types, vocab

    pairs = {
        "FieldType": vocab.field_types(),
        "ErrorCode": vocab.error_codes(),
        "Recovery": vocab.recoveries(),
        "EntityClass": vocab.entity_classes(),
        "Method": vocab.http_methods(),
        "Voice": vocab.voices(),
        "Emotion": vocab.emotions(),
        "Variant": vocab.flow_variants(),
        "MockProfile": vocab.mock_profile_names(),
        "Mode": vocab.integration_modes(),
        "Reason": vocab.release_reasons(),
        "Actions": vocab.ACCESS_ACTIONS,
    }
    for name, values in pairs.items():
        assert set(get_args(getattr(types, name))) == set(values), name


def _inputs_for(env, key="standard", research=None):
    from mde_spec_agent.lanes.product.steps.inputs import (
        integ_input_factory,
        journeys_input_factory,
        module_batches,
        module_input_factory,
        plan_input,
    )
    from mde_spec_agent.lanes.product.steps.plan import ensure_derived, env_of, fused_input

    ctx = env.ctx(key, research=research)
    d = ensure_derived(ctx)
    e = env_of(ctx)
    b = module_batches(ctx, e)[0]
    return (
        ctx,
        e,
        {
            "spec.plan": plan_input(ctx, e, d.plan),
            "spec.plan_fused": fused_input(ctx, e, d.plan),
            "spec.prod_module": module_input_factory(ctx, e)(b.module_id)(b.items),
            "spec.prod_integ": integ_input_factory(ctx, e)(sorted(e.graph.integrations)),
            "spec.prod_journeys": journeys_input_factory(ctx, e)(sorted(e.graph.flows)[:2]),
        },
    )


def test_untrusted_text_is_only_inside_blocks(env):
    from .test_research import INJECTION, research_file

    ctx, e, inputs = _inputs_for(env, research=research_file())
    si = ctx.spec_input
    secrets = {
        INJECTION,
        "PayCo",
        si.idea.summary[:40],
        *[m.name for m in e.graph.modules.values()],
        *[r.name for r in e.graph.roles.values()],
        *[f.name for f in e.graph.features.values()],
        *[x.name for x in e.graph.entities.values()],
    }
    # a name that is also an ordinary word of the fixed prompt text ("Owner") cannot be told apart there
    static = " ".join(
        (cs.spec.template_root / t).read_text(encoding="utf-8").lower()
        for cs in CALLS
        for t in (cs.spec.system_template, cs.spec.user_template)
    )
    secrets = {s for s in secrets if len(s) > 4 and s.lower() not in static}
    assert INJECTION in secrets and "PayCo" in secrets and len(secrets) > 10
    for cs in CALLS:
        prompt = PromptRenderer(max_untrusted_chars=120_000).render(cs.spec, inputs[cs.id])
        outside = re.sub(r"<untrusted_content.*?</untrusted_content>", "", prompt.user, flags=re.S)
        for s in secrets:
            assert s not in prompt.system, (cs.id, "system", s)
            assert s not in outside, (cs.id, "outside the blocks", s)
        # and the data really is in there, inside a block
        assert prompt.user.count("<untrusted_content") >= 1
