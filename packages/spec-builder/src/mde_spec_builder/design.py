"""Design tokens: theme lookup, accent / brand application and the contrast guard.

Used by the baseline (B2) to turn a theme plus ``design.accent`` / brand findings into tokens, and
by the proposal validator to refuse a token edit that would break text contrast (WCAG AA, 4.5:1).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from mde_content.themes import contrast_ratio, load_themes
from mde_content.themes import theme as load_theme
from mde_contracts.content import Theme
from mde_contracts.spec import DesignTokens

HEX6 = re.compile(r"^#[0-9A-Fa-f]{6}$")
_HEX_ANY = re.compile(r"^#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")
AA_NORMAL = 4.5
_STEPS = 24


def theme_ids() -> list[str]:
    return [t.id for t in load_themes().themes]


def get_theme(theme_id: str) -> Theme:
    return load_theme(theme_id)


def normalize_hex(value: str) -> str | None:
    """``#abc`` / ``aabbcc`` / ``#AABBCC`` -> ``#AABBCC``; None when it is not a colour code."""
    match = _HEX_ANY.match(value.strip())
    if match is None:
        return None
    digits = match.group(1).upper()
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return f"#{digits}"


def _rgb(value: str) -> tuple[int, int, int]:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _mix(color: str, target: str, amount: float) -> str:
    a, b = _rgb(color), _rgb(target)
    mixed = [round(x + (y - x) * amount) for x, y in zip(a, b, strict=True)]
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def _fit(color: str, backgrounds: Sequence[str], toward: str) -> str:
    """The first colour on the line from ``color`` to ``toward`` that reaches AA on every
    background (``color`` itself when it already does)."""
    for step in range(_STEPS + 1):
        candidate = _mix(color, toward, step / _STEPS)
        if all(contrast_ratio(candidate, bg) >= AA_NORMAL for bg in backgrounds):
            return candidate
    return toward


def _on(color: str) -> str:
    """The text colour (white or black) that reads best on ``color``."""
    return (
        "#FFFFFF"
        if contrast_ratio("#FFFFFF", color) >= contrast_ratio("#000000", color)
        else "#000000"
    )


def apply_primary(tokens: DesignTokens, accent: str) -> DesignTokens:
    """Make ``accent`` the primary colour in light and dark, nudged only as far as needed to keep
    AA contrast on the backgrounds, with a matching ``text_on_primary``."""
    light = dict(tokens.colors_light)
    dark = dict(tokens.colors_dark)
    light_bgs = [light[k] for k in ("background", "surface") if k in light and HEX6.match(light[k])]
    dark_bgs = [dark[k] for k in ("background", "surface") if k in dark and HEX6.match(dark[k])]
    primary_light = _fit(accent, light_bgs, "#000000")
    primary_dark = _fit(accent, dark_bgs, "#FFFFFF")
    light["primary"] = primary_light
    dark["primary"] = primary_dark
    light["text_on_primary"] = _on(primary_light)
    dark["text_on_primary"] = _on(primary_dark)
    return tokens.model_copy(update={"colors_light": light, "colors_dark": dark})


def contrast_failures(
    tokens: DesignTokens, pairs: Sequence[tuple[str, str]], minimum: float = AA_NORMAL
) -> set[tuple[str, str, str]]:
    """``(mode, foreground, background)`` of every declared pair below ``minimum``."""
    failing: set[tuple[str, str, str]] = set()
    for mode, colors in (("light", tokens.colors_light), ("dark", tokens.colors_dark)):
        for fg, bg in pairs:
            a, b = colors.get(fg), colors.get(bg)
            if a is None or b is None or not HEX6.match(a) or not HEX6.match(b):
                continue
            if contrast_ratio(a, b) < minimum:
                failing.add((mode, fg, bg))
    return failing


def pairs_for(direction: str, tokens: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """The contrast pairs of a theme (``text`` on ``background`` ... ); a sane default for an
    unknown direction."""
    try:
        return [(a, b) for a, b in get_theme(direction).contrast_pairs]
    except KeyError:
        return [("text", "background"), ("text_on_primary", "primary")]
