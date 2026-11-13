"""DirectionOut -> Direction node (one place, used by the validator, the gate and the step)."""

from __future__ import annotations

from mde_spec_agent.lanes.design.dsgn.schema import DirectionOut, DsgnIn
from mde_spec_core import tokens
from mde_spec_core.graph import Direction, Prov

CALL_ID = "spec.dsgn"


def direction_id(i: int) -> str:
    return f"DIR-S{i + 1:03d}"


def seed_hue_of(colour: str | None) -> float | None:
    """The hue the token builder will use for a seed colour (None when there is no usable colour)."""
    if not colour:
        return None
    try:
        return round(tokens.hex_to_hct(colour)[0], 1)
    except tokens.TokenError:
        return None


def admin_sentence(d: DirectionOut, inp: DsgnIn) -> str:
    """The deep per-role variant, as text carried by the direction (the Direction node has no variant field)."""
    if inp.profile != "deep" or not (d.admin_density or d.admin_motion or d.admin_ambition):
        return ""
    parts = [
        f"density {d.admin_density}" if d.admin_density else "",
        f"motion {d.admin_motion}" if d.admin_motion else "",
        f"ambition {d.admin_ambition}" if d.admin_ambition else "",
    ]
    axes = ", ".join(p for p in parts if p)
    note = d.admin_note.strip()
    return f"For admin and operations apps: {axes}." + (f" {note}" if note else "")


def to_direction(
    inp: DsgnIn,
    d: DirectionOut,
    did: str,
    *,
    brand_colour: str | None,
    user_ref: str = "",
    from_fallback: bool = False,
) -> Direction:
    colour = brand_colour if inp.brand_locked and brand_colour else d.seed_colour
    context = " ".join(
        s for s in (d.personality.strip(), d.audience_context.strip(), admin_sentence(d, inp)) if s
    )
    prov = [
        Prov(
            kind="code" if from_fallback else "model",
            ref="design.fallback" if from_fallback else CALL_ID,
        )
    ]
    if inp.brand_locked:
        prov.append(Prov(kind="user", ref=user_ref or "brand"))
    return Direction(
        id=did,
        tier="open",
        assumed=from_fallback,
        provenance=prov,
        name=d.name.strip(),
        mood=d.mood,
        audience_context=context,
        voice_line=d.voice_line.strip(),
        ambition=d.ambition,
        motion_personality=d.motion_personality,
        depth=d.depth,
        shape=d.shape,
        density=d.density,
        imagery=d.imagery,
        type_personality=d.type_personality,
        seed_hue=None if inp.brand_locked else seed_hue_of(colour),
        seed_colour=colour,
        brand_locked=bool(inp.brand_locked),
        delight_budget=d.delight_budget,
        dark_stance=d.dark_stance,
        avoid=[a.strip() for a in d.avoid],
        emotional_goals=[g.strip() for g in d.emotional_goals],
    )
