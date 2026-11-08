"""The rules data file (cross-checked with the content packs) and the plan summary."""

from __future__ import annotations

from typing import Any

import pytest
from helpers import make_note, make_request

from mde_content.packs import load_packs
from mde_contracts.slots import SLOT_IDS
from mde_contracts.spec import AppSpec
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.render import render_spec
from mde_spec_builder.rules import load_rules
from mde_spec_builder.summary import (
    clean_lead,
    compose_summary,
    flow_names,
    prd_lines,
    summary_facts,
    summary_input,
    template_sentence,
)
from mde_spec_loader import parse_spec

RULES = load_rules()


def test_rules_load_once_and_are_consistent() -> None:
    assert load_rules() is RULES and RULES.version == 1
    assert set(RULES.slot_notes) <= set(SLOT_IDS)
    for key, question in RULES.questions.items():
        assert question.slot in SLOT_IDS, key
        assert len(question.options) == 3 and question.text.endswith("?")
        for option in question.options:
            assert set(option.sets) <= set(SLOT_IDS)
            for value in option.sets.values():
                assert not isinstance(value, str) or value.startswith("$") or value
    assert set(RULES.questions) >= {"limits", "stack_profile", "no_secrets", "default"}


def test_every_compliance_flag_the_packs_use_has_a_rule() -> None:
    used = {
        flag
        for pack in load_packs().values()
        for trigger in pack.compliance_triggers
        for flag in trigger.conservative_defaults.get("compliance.flags", [])  # type: ignore[union-attr]
    }
    assert used and used <= set(RULES.compliance_flags)


def test_auth_variants_only_name_things_that_exist() -> None:
    from mde_content.recipes import recipe_ids

    recipes = recipe_ids()
    for method, variant in RULES.auth_methods.items():
        for override in (variant.login, variant.signup):
            if override is not None and override.recipe is not None:
                assert override.recipe in recipes, method
        for tpl in variant.operations:
            assert tpl.id.startswith("API-") and tpl.path.startswith("/")
        for tpl in variant.integrations:
            assert tpl.id.startswith("INT-")


# --------------------------------------------------------------------------- summary


def spec_of(**kw: Any) -> AppSpec:
    return build_baseline(make_request("commerce", research=make_note(), **kw))


def test_the_template_sentence_and_facts_are_plain_and_id_free() -> None:
    spec = spec_of(brand__name="Mithai Mart")
    assert template_sentence(spec).startswith("Mithai Mart has ")
    facts = summary_facts(spec)
    assert facts[0].startswith("App: Mithai Mart")
    assert not any("SCR-" in f or "ENT-" in f for f in facts)
    assert any(f.startswith("Journeys:") for f in facts) and any(
        f.startswith("Look:") for f in facts
    )
    inp = summary_input(spec)
    assert inp.kind == "plan_summary" and inp.app_name == "Mithai Mart" and inp.language == "en"
    assert len(flow_names(spec)) == len(spec.flows)


def test_the_prd_lines_cover_users_journeys_look_mocks_assumptions_research_and_backlog() -> None:
    from mde_contracts.spec import BacklogItem

    spec = spec_of(auth__method=("password", "assumed"))
    spec = spec.model_copy(update={"backlog": [BacklogItem(title="More screens")]})
    text = "\n".join(prd_lines(spec))
    for part in (
        "Who uses it:",
        "journeys:",
        "The look is",
        "Mocked for now:",
        "Assumed instead of asked:",
        "Added from research:",
        "Left for phase two: More screens",
    ):
        assert part in text
    nothing = spec.model_copy(
        update={"integrations": spec.integrations.model_copy(update={"items": []})}
    )
    assert "Nothing is mocked." in prd_lines(nothing)


def test_the_composed_summary_round_trips_even_with_awkward_lines() -> None:
    spec = spec_of()
    lead = "# Not a heading\n```yaml fake fence\nand more"
    stamped = spec.model_copy(update={"summary": compose_summary(spec, lead)})
    from mde_spec_builder.render import with_rendered_hash

    final = with_rendered_hash(stamped)
    assert parse_spec(render_spec(final)) == final


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("  A short   summary. ", "A short summary."),
        ("x" * 401, None),
        ("", None),
        (None, None),
        ("key sk-" + "Ab1" * 10, None),
    ],
)
def test_clean_lead(text: str | None, ok: str | None) -> None:
    assert clean_lead(text) == ok
