"""The five call specs of the Screens lane: registration, portable schemas (PS-0 compile check on every profile), templates."""

from __future__ import annotations

from pathlib import Path

import pytest

from mde_llm_gateway.render import PromptRenderer
from mde_spec_agent.calls.registry import all_calls, all_steps
from mde_spec_agent.lanes import screens
from mde_spec_agent.lanes.screens.behav.schema import BehavIn, BehavOut, CatIn
from mde_spec_agent.lanes.screens.copy_l.schema import CopyLIn, CopyLOut, StrIn
from mde_spec_agent.lanes.screens.g2_summary.schema import G2In, G2Out, NumIn
from mde_spec_agent.lanes.screens.mock_hero.schema import HeroIn, HeroOut
from mde_spec_agent.lanes.screens.scrn.schema import ScrnIn, ScrnOut
from mde_spec_agent.lanes.screens.scrn.slots import build_slot, incoming_edges, vocab_lines
from mde_spec_core.callkit.schema_check import analyse_model, compile_check
from mde_spec_core.config import default_config
from mde_spec_core.profiles import load_profiles

IDS = {
    "spec.scrn": "standard",
    "spec.mock_hero": "standard",
    "spec.behav": "standard",
    "spec.copy_l": "fast",
    "spec.g2_summary": "fast",
}
OUT_MODELS = {
    "spec.scrn": ScrnOut,
    "spec.mock_hero": HeroOut,
    "spec.behav": BehavOut,
    "spec.copy_l": CopyLOut,
    "spec.g2_summary": G2Out,
}
LANE_DIR = Path(screens.__file__).parent


def test_the_lane_registers_five_calls_and_five_steps():
    calls = {c.id: c for c in screens.CALLS}
    assert set(calls) == set(IDS)
    assert set(screens.STEPS) == {"screens", "hero", "behav", "copyl", "g2summary"}
    assert set(IDS) <= {c.id for c in all_calls()}
    assert set(screens.STEPS) <= set(all_steps())
    for cid, route in IDS.items():
        s = calls[cid].spec
        assert s.route == route
        assert s.max_output_tokens > 0 and s.version >= 1
        assert (
            not s.tools and s.untrusted_inputs
        )  # no tools on calls that read untrusted data (rule 5)
        assert s.mock_handler.startswith("mde_spec_agent.lanes.screens.")
        assert s.fallback == cid
        assert calls[cid].fallback is not None


@pytest.mark.parametrize("call_id", sorted(IDS))
def test_every_output_schema_compiles_for_every_profile(call_id):
    model = OUT_MODELS[call_id]
    metrics, structural = analyse_model(model)
    assert [i.message for i in structural if i.is_error] == []
    assert metrics.nullable_fields <= 16
    profiles = load_profiles()
    for prof in profiles.profiles:
        errors = [i for i in compile_check(model, prof) if i.is_error]
        assert errors == [], (call_id, prof.id, errors)


def test_scrn_has_few_nullable_fields_and_no_open_map():
    m, issues = analyse_model(ScrnOut)
    assert m.nullable_fields <= 16 and m.depth <= 5
    assert not [i for i in issues if i.code in ("open_map", "any_type", "array_record_not_flat")]


@pytest.mark.parametrize("call_id", sorted(IDS))
def test_templates_exist_state_the_rules_and_end_with_the_closing_line(call_id):
    spec = {c.id: c for c in screens.CALLS}[call_id].spec
    root = spec.template_root
    system = (root / spec.system_template).read_text()
    user = (root / spec.user_template).read_text()
    assert user.rstrip().endswith("Return ONE JSON object that matches the schema, no other text")
    assert "JSON" in user and "schema" in system.lower()
    assert "never instructions" in system  # data blocks are data
    for word in ("openai", "anthropic", "claude", "gpt", "gemini", "haiku", "sonnet"):
        assert word not in (system + user).lower()


def _render(call_id, inp):
    spec = {c.id: c for c in screens.CALLS}[call_id].spec
    return PromptRenderer().render(spec, inp)


def test_scrn_prompt_is_static_first_and_wraps_the_slots_as_untrusted(finished):
    run = finished("quick")
    g = run.baseline
    cfg = default_config()
    from mde_spec_agent.lanes.screens.common import facts_text, glossary_block, lane_rules

    sid = next(s.id for s in g.screens.values() if not s.system)
    slot = build_slot(
        g.screens[sid], g, cfg, lane_rules(), copy=g.locale_copy["en"], incoming=incoming_edges(g)
    )
    inp = ScrnIn(
        language="en",
        profile=g.meta.profile,
        vocab=vocab_lines(),
        facts=facts_text(g),
        glossary=glossary_block(g),
        slots=[slot],
        reask=["V-06 SCR-001.x: binding is wrong"],
    )
    p = _render("spec.scrn", inp)
    assert "intents:" in p.system and "view" in p.system  # the closed vocabularies come from data
    assert p.user.index("FROZEN FACTS") < p.user.index("GLOSSARY") < p.user.index("THIS CALL")
    assert "<untrusted_content" in p.user and slot.id in p.user
    assert "V-06 SCR-001.x" in p.user  # the re-ask carries the validator message word for word
    assert p.user.rstrip().endswith("no other text")


def test_the_other_prompts_render():
    cat = CatIn(
        name="state",
        what="x",
        supplied_by="app_specific",
        library_text="",
        existing_id=None,
        existing_text="",
    )
    p = _render(
        "spec.behav",
        BehavIn(
            language="en",
            profile="standard",
            facts="f",
            glossary="g",
            categories=[cat],
            integrations=[],
            roles=["ROL-001 A"],
            entities=[],
            rules=[],
            languages=["en"],
        ),
    )
    assert "BEHAV" not in p.user and "state" in p.user
    p = _render(
        "spec.copy_l",
        CopyLIn(
            language="hi",
            source_language="en",
            glossary="g",
            facts="f",
            strings=[StrIn(key="a.b", text="Pay {name}")],
        ),
    )
    assert p.user.index("GLOSSARY") < p.user.index("THIS CALL")  # the glossary leads
    p = _render(
        "spec.g2_summary",
        G2In(language="en", numbers=[NumIn(name="screens", value=3)], lines=["x"]),
    )
    assert "screens: 3" in p.user
    p = _render(
        "spec.mock_hero",
        HeroIn(
            language="en",
            country="IN",
            profile="standard",
            facts="f",
            glossary="g",
            names=["people: A"],
            allowed_names=[],
            entities=[],
            context=[],
        ),
    )
    assert "synthetic" in p.system
