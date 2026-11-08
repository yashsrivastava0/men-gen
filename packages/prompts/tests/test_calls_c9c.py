"""C9c.design_tokens: spec, templates, strict schema and the offline handler."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import builder_call_checks as chk

from mde_contracts.calls import BriefContext, C9cInput, DesignBaseline, KeyValue
from mde_prompts.calls.c9c_design_tokens.mock import handle

CALL_ID = "C9c.design_tokens"


def make(tone: list[str], colors: list[str], radius: int = 12) -> C9cInput:
    return C9cInput(
        brief=BriefContext(app_summary="An app", archetype="commerce"),
        baseline=DesignBaseline(
            direction="clean-bright",
            color_tokens_light=[
                KeyValue(key="primary", value="#1B5FD9"),
                KeyValue(key="accent", value="#0F766E"),
            ],
            radius=radius,
        ),
        brand_colors=colors,
        brand_tone=tone,
    )


def test_spec_shape() -> None:
    chk.check_spec_shape(
        CALL_ID, effort="medium", min_tokens=600, max_tokens=2000, fallback="builder.no_proposals"
    )


def test_output_converts_to_the_strict_schema() -> None:
    chk.check_strict_schema(CALL_ID)


def test_mock_output_is_deterministic_schema_valid_and_matches_the_golden_fixture() -> None:
    out = chk.check_mock_matches_golden(CALL_ID)
    assert len(out.proposals) == 2  # type: ignore[attr-defined]


def test_templates_render_with_the_fixture_and_stay_small() -> None:
    system, user = chk.render_fixture(CALL_ID, max_chars=6000)
    assert "never lower contrast" in system
    assert (
        "Theme: warm-friendly" in user and "background #" in user and "Brand tone: playful" in user
    )


def test_invalid_fixture_is_schema_valid() -> None:
    chk.check_invalid_fixture_parses(CALL_ID)


def test_soft_tones_round_the_corners_and_sharp_tones_square_them() -> None:
    (soft,) = handle(make(["Playful"], [])).proposals
    assert soft.pairs == [KeyValue(key="radius.md", value="16")]
    (sharp,) = handle(make(["minimal"], [], radius=12)).proposals
    assert sharp.pairs == [KeyValue(key="radius.md", value="8")]


def test_radius_is_clamped_and_unchanged_values_are_not_proposed() -> None:
    assert handle(make(["playful"], [], radius=24)).proposals == []
    assert handle(make(["serious"], [], radius=2)).proposals == []
    assert handle(make(["quirky"], [])).proposals == []


def test_a_new_brand_colour_becomes_a_readable_accent_and_a_known_one_does_not() -> None:
    from mde_content.themes import contrast_ratio

    (p,) = handle(make([], ["#e4572e", "#112233"])).proposals
    assert p.pairs is not None and p.pairs[0].key == "colors_light.accent"
    value = p.pairs[0].value
    assert (
        value != "#E4572E" and contrast_ratio("#FFFFFF", value) >= 4.5
    )  # darkened to stay readable
    (dark,) = handle(make([], ["#0b6e4f"])).proposals
    assert (dark.pairs or [])[0].value == "#0B6E4F"  # already readable: unchanged
    assert handle(make([], ["#1b5fd9"])).proposals == []  # already the primary
    assert handle(make([], ["red", "#12"])).proposals == []  # not colours


def test_nothing_without_brand_signals() -> None:
    assert handle(make([], [])).proposals == []
