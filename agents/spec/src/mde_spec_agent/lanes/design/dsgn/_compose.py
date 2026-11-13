"""One deterministic way to write a valid DsgnOut from a DsgnIn: used by the mock handler and by the fallback.

Axes and seed hues come from `tokens.pick_directions` (which honours the minimum axis differences and the hue gap);
the words come from the lane's data file by mood family. Nothing here names a company or an app type.
"""

from __future__ import annotations

import random

from mde_spec_agent.lanes.design._data import design_data
from mde_spec_agent.lanes.design.dsgn.schema import AssetOut, DirectionOut, DsgnIn, DsgnOut
from mde_spec_core import tokens

_AMBITIONS = ("refined", "expressive", "immersive")
_DELIGHT = {"refined": 1, "expressive": 2, "immersive": 3}


def _ambition_for(i: int, hint: str) -> str:
    order = (
        [hint, *[a for a in _AMBITIONS if a != hint]] if hint in _AMBITIONS else list(_AMBITIONS)
    )
    return order[i % len(order)]


def _entity_names(inp: DsgnIn) -> list[str]:
    return [e.split("|")[0].strip() for e in inp.entities.splitlines() if e.strip()]


def write_assets(inp: DsgnIn) -> list[AssetOut]:
    d = design_data()
    out: list[AssetOut] = []
    ents = _entity_names(inp)
    cap = d.empty_state_entities_max.get(inp.profile, 1)
    for key in d.asset_template_order.get(inp.profile, []):
        t = d.asset_templates[key]
        if key == "empty_state":
            for name in ents[:cap] or ["items"]:
                out.append(_asset(t, inp, t.subject.format(entity=name.lower())))
        else:
            out.append(_asset(t, inp, t.subject))
    return out[: inp.max_assets]


def _asset(t, inp: DsgnIn, subject: str) -> AssetOut:  # type: ignore[no-untyped-def]
    return AssetOut(
        kind=t.kind,
        purpose=t.purpose,
        subject=subject,
        count=1,
        avoid=list(t.avoid),
        app="",
    )


def compose(inp: DsgnIn) -> DsgnOut:
    d = design_data()
    rnd = random.Random(inp.seed)  # noqa: S311 - deterministic choice, not security
    picks = tokens.pick_directions(inp.count, brand_locked=inp.brand_locked, rnd=rnd)
    deep = inp.profile == "deep"
    used: dict[str, int] = {}
    outs: list[DirectionOut] = []
    audience = "; ".join([a.strip() for a in inp.audience.splitlines() if a.strip()][:3])
    for i, p in enumerate(picks):
        mood = str(p["mood_family"])
        copy = d.moods[mood]
        n = used.get(mood, 0)
        used[mood] = n + 1
        name = copy.name if n == 0 else f"{copy.name} {d.ordinals[min(n, len(d.ordinals) - 1)]}"
        ambition = _ambition_for(i, inp.ambition_hint)
        hue = p.get("seed_hue")
        dark = d.deep_default_dark_stance if deep else d.default_dark_stance
        outs.append(
            DirectionOut(
                name=name,
                mood=mood,
                personality=copy.personality,
                audience_context=audience or "The people the Brief describes",
                emotional_goals=list(copy.goals),
                motion_personality=p["motion_personality"],
                depth=p["depth"],
                shape=p["shape"],
                density=p["density"],
                imagery=p["imagery"],
                type_personality=p["type_personality"],
                dark_stance=dark,
                ambition=ambition,
                delight_budget=_DELIGHT[ambition],
                voice_line=copy.voice_line,
                avoid=list(copy.avoid),
                seed_colour=None
                if (inp.brand_locked or hue is None)
                else tokens.seed_from_hue(float(hue)),
                admin_density=d.admin_variant.default_density if deep else None,  # type: ignore[arg-type]
                admin_motion=d.admin_variant.default_motion if deep else None,  # type: ignore[arg-type]
                admin_ambition=d.admin_variant.default_ambition if deep else None,  # type: ignore[arg-type]
                admin_note=d.admin_variant.note if deep else "",
            )
        )
    return DsgnOut(directions=outs, assets=write_assets(inp))
