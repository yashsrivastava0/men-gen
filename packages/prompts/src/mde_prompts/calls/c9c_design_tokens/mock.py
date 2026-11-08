"""Offline handler for C9c: nudge at most a corner radius and the accent colour.

Deterministic and schema-valid. Playful or friendly brand tones round the corners a little; formal
or minimal tones square them off. A brand colour that differs from the baseline becomes the accent.
"""

from __future__ import annotations

import re

from mde_contracts.calls import C9cInput, C9cOutput, KeyValue, Proposal, SpecProposals

_SOFT = {"playful", "friendly", "warm", "soft", "rounded", "fun"}
_SHARP = {"minimal", "formal", "serious", "corporate", "professional", "sharp", "precise"}
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
RADIUS_STEP = 4
RADIUS_MAX = 24
RADIUS_MIN = 2
MIN_CONTRAST = 4.6


def _luminance(color: str) -> float:
    def channel(value: int) -> float:
        s = value / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _readable_with_white(color: str) -> str:
    """The brand colour, darkened only as far as white text on it reaches 4.5:1 (WCAG AA)."""
    for step in range(21):
        mixed = [round(int(color[i : i + 2], 16) * (1 - step / 20)) for i in (1, 3, 5)]
        candidate = "#{:02X}{:02X}{:02X}".format(*mixed)
        if 1.05 / (_luminance(candidate) + 0.05) >= MIN_CONTRAST:
            return candidate
    return "#000000"


def _token(baseline: C9cInput, name: str) -> str | None:
    return next((c.value for c in baseline.baseline.color_tokens_light if c.key == name), None)


def handle(input: C9cInput) -> C9cOutput:
    proposals: list[Proposal] = []
    tone = {w.lower() for w in input.brand_tone}
    radius = input.baseline.radius
    target: int | None = None
    if tone & _SOFT:
        target = min(radius + RADIUS_STEP, RADIUS_MAX)
    elif tone & _SHARP:
        target = max(radius - RADIUS_STEP, RADIUS_MIN)
    if target is not None and target != radius:
        proposals.append(
            Proposal(
                kind="set_design_token",
                rationale=f"The brand tone ({', '.join(input.brand_tone[:3])}) suits {'rounder' if target > radius else 'squarer'} corners.",
                pairs=[KeyValue(key="radius.md", value=str(target))],
            )
        )
    known = {_token(input, "primary"), _token(input, "accent")}
    for color in input.brand_colors:
        if _HEX.match(color) and color.upper() not in {k.upper() for k in known if k}:
            proposals.append(
                Proposal(
                    kind="set_design_token",
                    rationale="The brand colour becomes the accent (kept readable with white text).",
                    pairs=[KeyValue(key="colors_light.accent", value=_readable_with_white(color))],
                )
            )
            break
    return SpecProposals(proposals=proposals, notes="mock: radius and accent nudges")
