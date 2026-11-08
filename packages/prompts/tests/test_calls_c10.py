"""C10.screen_briefs: spec, templates, strict schema and the offline handler."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import builder_call_checks as chk

from mde_contracts.calls import BriefContext, C10Input, ScreenDraftLite
from mde_prompts.calls.c10_screen_briefs.mock import handle

CALL_ID = "C10.screen_briefs"


def screen(**kw: object) -> ScreenDraftLite:
    base: dict[str, object] = {
        "id": "SCR-010",
        "route": "/cart",
        "recipe": "list",
        "purpose": "Review the items. Change quantities and continue to checkout.",
    }
    return ScreenDraftLite.model_validate({**base, **kw})


def inp(*screens: ScreenDraftLite) -> C10Input:
    return C10Input(
        brief=BriefContext(app_summary="An app", archetype="commerce"), screens=list(screens)
    )


def test_spec_shape() -> None:
    chk.check_spec_shape(
        CALL_ID, effort="medium", min_tokens=2000, max_tokens=4000, fallback="builder.no_proposals"
    )


def test_output_converts_to_the_strict_schema() -> None:
    chk.check_strict_schema(CALL_ID)


def test_mock_output_is_deterministic_schema_valid_and_matches_the_golden_fixture() -> None:
    out = chk.check_mock_matches_golden(CALL_ID)
    assert len(out.proposals) == 9  # type: ignore[attr-defined]  # three proposals for each of three screens


def test_templates_render_with_the_fixture_and_stay_small() -> None:
    system, user = chk.render_fixture(CALL_ID, max_chars=9000)
    assert chk.PROPOSAL_RULE in system and "At most 4 proposals per screen" in system
    assert "SCR-006" in user and "Recipes used" in user and "Neighbouring screens" in user


def test_invalid_fixture_is_schema_valid() -> None:
    chk.check_invalid_fixture_parses(CALL_ID)


def test_each_screen_gets_copy_a_section_and_a_check() -> None:
    out = handle(inp(screen(), screen(id="SCR-011", route="/checkout"))).proposals
    kinds = [(p.kind, p.target) for p in out]
    assert kinds == [
        ("set_screen_copy", "SCR-010"),
        ("add_screen_section", "SCR-010"),
        ("add_screen_check", "SCR-010"),
        ("set_screen_copy", "SCR-011"),
        ("add_screen_section", "SCR-011"),
        ("add_screen_check", "SCR-011"),
    ]
    copy = out[0].pairs
    assert copy is not None and copy[0].key == "intro"
    assert copy[0].value == "Review the items"  # the first sentence, no full stop
    assert (out[2].items or [""])[0].startswith(
        "Everything this screen promises works: review the items"
    )


def test_it_only_adds_what_is_missing() -> None:
    done = screen(copy_keys=["intro", "title"], sections=["items", "help_hint"])
    out = handle(inp(done)).proposals
    assert [p.kind for p in out] == ["add_screen_check"]


def test_long_purposes_are_cut_at_a_word_boundary() -> None:
    long = screen(purpose="Words " * 60)
    out = handle(inp(long)).proposals
    assert len((out[0].pairs or [])[0].value) <= 100
    assert len((out[2].items or [""])[0]) <= 160
    assert not (out[0].pairs or [])[0].value.endswith(" ")


def test_an_empty_purpose_still_gives_valid_proposals() -> None:
    out = handle(inp(screen(purpose=""))).proposals
    assert (out[0].pairs or [])[0].value == "This screen"
    assert len(out) == 3


# --------------------------------------------------------------------------- a detail from the person


def test_a_detail_rides_in_the_purpose_and_comes_back_out() -> None:
    from mde_prompts.calls.c10_screen_briefs.detail import (
        USER_DETAIL_MARK,
        split_detail,
        with_detail,
    )

    line = with_detail("Review the items.", "a gift message field")
    assert line == f"Review the items. {USER_DETAIL_MARK} a gift message field"
    assert split_detail(line) == ("Review the items.", "a gift message field")
    assert with_detail("Review the items.", None) == "Review the items."
    assert split_detail("Review the items.") == ("Review the items.", None)
    assert split_detail(f"Only this. {USER_DETAIL_MARK}   ") == ("Only this.", None)


def test_the_handler_turns_a_detail_into_a_check_and_keeps_the_rest_as_before() -> None:
    from mde_prompts.calls.c10_screen_briefs.detail import with_detail

    plain = handle(inp(screen())).proposals
    asked = handle(
        inp(screen(purpose=with_detail(screen().purpose, "a gift message field")))
    ).proposals
    assert [p.kind for p in asked] == ["add_screen_check", *[p.kind for p in plain]]
    detail = asked[0]
    assert detail.items == ["The screen has what you asked for: a gift message field"]
    assert "asked" in detail.rationale
    # the intro line and the generic check read the purpose without the detail
    assert asked[1:] == plain


def test_the_detail_check_is_cut_to_the_check_limit() -> None:
    from mde_prompts.calls.c10_screen_briefs.detail import with_detail

    out = handle(inp(screen(purpose=with_detail("A screen.", "word " * 100)))).proposals
    assert len((out[0].items or [""])[0]) <= 160


def test_the_system_prompt_says_what_a_user_detail_is() -> None:
    system, _ = chk.render_fixture(CALL_ID, max_chars=9000)
    assert 'User detail: ..."' in system and "never drop it" in system
