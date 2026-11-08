from __future__ import annotations

import re

import pytest
import yaml

from mde_content.themes import (
    WCAG_AA_NORMAL,
    contrast_failures,
    contrast_ratio,
    load_themes,
    relative_luminance,
    theme,
    theme_ids,
)
from mde_contracts.content import Theme, ThemeLibrary

PRESETS = [
    "clean-bright",
    "bold-dark",
    "warm-friendly",
    "minimal-mono",
    "vibrant-playful",
    "corporate-calm",
]
REQUIRED_COLORS = {
    "background",
    "surface",
    "text",
    "text_muted",
    "primary",
    "text_on_primary",
    "accent",
    "border",
    "danger",
    "success",
}
REQUIRED_TYPE = {"display", "h1", "h2", "h3", "body", "body_small", "label", "caption", "button"}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def test_contrast_math_known_values() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    assert contrast_ratio("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.01)
    assert relative_luminance("#FFFFFF") == pytest.approx(1.0)
    assert relative_luminance("#000000") == pytest.approx(0.0)


def test_contrast_rejects_bad_color() -> None:
    with pytest.raises(ValueError):
        contrast_ratio("red", "#FFFFFF")
    with pytest.raises(ValueError):
        contrast_ratio("#FFF", "#FFFFFF")


def test_six_presets_in_order() -> None:
    assert theme_ids() == PRESETS
    assert isinstance(load_themes(), ThemeLibrary)
    assert theme("bold-dark").label


def test_unknown_theme() -> None:
    with pytest.raises(KeyError):
        theme("neon")


@pytest.mark.parametrize("item", load_themes().themes, ids=lambda t: t.id)
def test_theme_tokens_are_complete(item: Theme) -> None:
    tokens = item.tokens
    for colors in (tokens.colors_light, tokens.colors_dark):
        assert set(colors) >= REQUIRED_COLORS
        assert all(HEX.match(v) for v in colors.values())
    assert set(tokens.colors_light) == set(tokens.colors_dark)
    assert set(tokens.type_scale) >= REQUIRED_TYPE
    sizes = [tokens.type_scale[k].size for k in ("display", "h1", "h2", "h3", "body", "caption")]
    assert sizes == sorted(sizes, reverse=True), "type scale descends from display to caption"
    assert all(s.weight in range(100, 1000, 100) for s in tokens.type_scale.values())
    assert all(1.0 <= s.line_height <= 2.0 for s in tokens.type_scale.values())
    assert tokens.spacing == sorted(set(tokens.spacing)) and tokens.spacing[0] == 0
    assert {"none", "sm", "md", "lg", "full"} <= set(tokens.radius)
    assert {"none", "sm", "md", "lg"} <= set(tokens.elevation)
    assert {"duration_fast", "duration_base", "duration_slow", "easing_standard"} <= set(
        tokens.motion
    )


@pytest.mark.parametrize("item", load_themes().themes, ids=lambda t: t.id)
def test_every_declared_pair_passes_wcag_aa_in_light_and_dark(item: Theme) -> None:
    assert contrast_failures(item) == []
    # Spell the check out so a failure names the pair.
    for mode, colors in (("light", item.tokens.colors_light), ("dark", item.tokens.colors_dark)):
        for fg, bg in item.contrast_pairs:
            ratio = contrast_ratio(colors[fg], colors[bg])
            assert ratio >= WCAG_AA_NORMAL, f"{item.id} {mode} {fg}/{bg} = {ratio:.2f}"


@pytest.mark.parametrize("item", load_themes().themes, ids=lambda t: t.id)
def test_declared_pairs_cover_the_text_roles(item: Theme) -> None:
    pairs = {tuple(p) for p in item.contrast_pairs}
    must = {
        ("text", "background"),
        ("text", "surface"),
        ("text_muted", "background"),
        ("text_muted", "surface"),
        ("text_on_primary", "primary"),
        ("text_on_accent", "accent"),
        ("danger", "background"),
        ("success", "background"),
    }
    assert must <= pairs


def test_contrast_failures_detects_a_bad_pair_and_a_typo() -> None:
    base = theme("clean-bright")
    bad = base.model_copy(
        update={
            "tokens": base.tokens.model_copy(
                update={"colors_light": {**base.tokens.colors_light, "text": "#CCCCCC"}}
            )
        }
    )
    problems = contrast_failures(bad)
    assert any("light: text on background" in p for p in problems)
    assert not any("dark" in p for p in problems)
    typo = base.model_copy(update={"contrast_pairs": [("txt", "background")]})
    assert any("unknown token" in p for p in contrast_failures(typo))


def test_themes_are_visibly_different() -> None:
    primaries = {t.tokens.colors_light["primary"] for t in load_themes().themes}
    assert len(primaries) >= 5
    radii = {t.tokens.radius["md"] for t in load_themes().themes}
    assert len(radii) >= 4


def test_yaml_round_trips_through_the_contract_model() -> None:
    lib = load_themes()
    again = ThemeLibrary.model_validate(yaml.safe_load(yaml.safe_dump(lib.model_dump())))
    assert again == lib
