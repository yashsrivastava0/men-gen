"""C9a.screens_nav: spec, templates, strict schema and the offline handler."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import builder_call_checks as chk

from mde_contracts.calls import (
    BriefContext,
    C9aInput,
    OutlineScreen,
    RecipeLite,
    ResearchAdditionLite,
    SpecOutline,
)
from mde_prompts.calls.c9a_screens_nav.mock import handle

CALL_ID = "C9a.screens_nav"
BRIEF = BriefContext(app_summary="An app", archetype="commerce")
OUTLINE = SpecOutline(
    screens=[
        OutlineScreen(id="SCR-001", route="/login", recipe="login-otp"),
        OutlineScreen(id="SCR-002", route="/home", recipe="dashboard"),
    ]
)
RECIPES = [RecipeLite(id="login-otp", kind="auth"), RecipeLite(id="list", kind="list")]


def addition(ref: str, title: str, kind: str = "feature") -> ResearchAdditionLite:
    return ResearchAdditionLite(
        id=ref,
        kind=kind,  # type: ignore[arg-type]
        title=title,
        rationale="Seen in similar apps",
    )


def test_spec_shape() -> None:
    chk.check_spec_shape(
        CALL_ID, effort="medium", min_tokens=1000, max_tokens=3000, fallback="builder.no_proposals"
    )


def test_output_converts_to_the_strict_schema() -> None:
    chk.check_strict_schema(CALL_ID)


def test_mock_output_is_deterministic_schema_valid_and_matches_the_golden_fixture() -> None:
    out = chk.check_mock_matches_golden(CALL_ID)
    assert out.proposals and all(p.kind == "add_screen" for p in out.proposals)  # type: ignore[attr-defined]


def test_templates_render_with_the_fixture_and_stay_small() -> None:
    system, user = chk.render_fixture(CALL_ID, max_chars=9000)
    assert chk.PROPOSAL_RULE in system and "At most 8 new screens" in system
    assert "RA-001" in user and "SCR-004 /home" in user and "(primary)" in user
    assert system.count("\n") < 30  # a short system prompt


def test_invalid_fixture_is_schema_valid() -> None:
    chk.check_invalid_fixture_parses(CALL_ID)


def test_it_proposes_nothing_without_research() -> None:
    assert handle(C9aInput(brief=BRIEF, outline=OUTLINE, recipes=RECIPES)).proposals == []


def test_feature_additions_become_research_screens_opened_from_home() -> None:
    inp = C9aInput(
        brief=BRIEF, outline=OUTLINE, recipes=RECIPES, research=[addition("RA-007", "Gift cards")]
    )
    (p,) = handle(inp).proposals
    assert (p.kind, p.target, p.source, p.research_ref) == (
        "add_screen",
        "SCR-002",
        "research",
        "RA-007",
    )
    assert (p.route, p.recipe, p.name) == ("/gift-cards", "list", "Gift cards")


def test_it_proposes_at_most_two_and_never_more_than_the_room() -> None:
    titles = ["Gift cards", "Loyalty points", "Wishlist items", "Size guide"]
    adds = [addition(f"RA-00{i}", t) for i, t in enumerate(titles, 1)]
    inp = C9aInput(brief=BRIEF, outline=OUTLINE, recipes=RECIPES, research=adds)
    assert len(handle(inp).proposals) == 2
    assert len(handle(inp.model_copy(update={"max_new_screens": 1})).proposals) == 1
    assert handle(inp.model_copy(update={"max_new_screens": 0})).proposals == []


def test_other_kinds_and_features_that_already_exist_are_skipped() -> None:
    inp = C9aInput(
        brief=BRIEF,
        outline=OUTLINE,
        recipes=RECIPES,
        research=[addition("RA-001", "Status timeline", "pattern")],
    )
    assert handle(inp).proposals == []
    has_favorites = OUTLINE.model_copy(
        update={
            "screens": [
                *OUTLINE.screens,
                OutlineScreen(id="SCR-003", route="/favorites", recipe="list"),
            ]
        }
    )
    again = C9aInput(
        brief=BRIEF,
        outline=has_favorites,
        recipes=RECIPES,
        research=[addition("RA-001", "Favorites list")],
    )
    assert handle(again).proposals == []


def test_without_a_list_recipe_or_a_home_screen_it_degrades_gracefully() -> None:
    only_auth = C9aInput(
        brief=BRIEF,
        outline=OUTLINE,
        recipes=[RecipeLite(id="login-otp", kind="auth")],
        research=[addition("RA-001", "Gift cards")],
    )
    assert handle(only_auth).proposals == []
    no_home = SpecOutline(screens=[OutlineScreen(id="SCR-001", route="/tasks", recipe="list")])
    inp = C9aInput(
        brief=BRIEF, outline=no_home, recipes=RECIPES, research=[addition("RA-001", "Gift cards")]
    )
    assert handle(inp).proposals[0].target == "SCR-001"
    empty = C9aInput(
        brief=BRIEF,
        outline=SpecOutline(),
        recipes=RECIPES,
        research=[addition("RA-001", "Gift cards")],
    )
    assert handle(empty).proposals[0].target is None
