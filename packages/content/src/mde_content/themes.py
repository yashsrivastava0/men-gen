"""Theme presets loader and WCAG contrast math (brief 8.6).

Six presets live in ``data/themes.yaml`` (validated by ``ThemeLibrary``). Each theme declares
``contrast_pairs`` (foreground token, background token); every declared pair must reach WCAG AA
for normal text (4.5:1) in both the light and the dark palette. ``contrast_failures`` is the one
place that checks it, used by the tests and available to any validator.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

import yaml

from mde_contracts.content import Theme, ThemeLibrary

WCAG_AA_NORMAL = 4.5
WCAG_AA_LARGE = 3.0

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{6})$")


def _channel(value: int) -> float:
    s = value / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def _rgb(hex_color: str) -> tuple[int, int, int]:
    if not _HEX.match(hex_color):
        raise ValueError(f"expected a #RRGGBB color, got {hex_color!r}")
    return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of a ``#RRGGBB`` color (0 black .. 1 white)."""
    r, g, b = _rgb(hex_color)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG contrast ratio between two ``#RRGGBB`` colors (1.0 .. 21.0), order independent."""
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    hi, lo = (la, lb) if la >= lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


@lru_cache(maxsize=1)
def load_themes() -> ThemeLibrary:
    """Load and validate the six theme presets (cached; the result is immutable)."""
    text = resources.files("mde_content").joinpath("data/themes.yaml").read_text(encoding="utf-8")
    return ThemeLibrary.model_validate(yaml.safe_load(text))


def theme_ids() -> list[str]:
    """Theme ids in file order."""
    return [t.id for t in load_themes().themes]


def theme(theme_id: str) -> Theme:
    """Return one theme by id; ``KeyError`` for an unknown id."""
    for item in load_themes().themes:
        if item.id == theme_id:
            return item
    raise KeyError(f"unknown theme: {theme_id!r}")


def contrast_failures(item: Theme, minimum: float = WCAG_AA_NORMAL) -> list[str]:
    """Describe every declared contrast pair below ``minimum`` (light and dark). Empty means pass.

    A pair that names a missing token is also reported, so a typo cannot hide a failure.
    """
    problems: list[str] = []
    for mode, colors in (("light", item.tokens.colors_light), ("dark", item.tokens.colors_dark)):
        for fg, bg in item.contrast_pairs:
            if fg not in colors or bg not in colors:
                problems.append(f"{item.id} {mode}: unknown token in pair ({fg}, {bg})")
                continue
            ratio = contrast_ratio(colors[fg], colors[bg])
            if ratio < minimum:
                problems.append(f"{item.id} {mode}: {fg} on {bg} is {ratio:.2f}:1 (< {minimum})")
    return problems
