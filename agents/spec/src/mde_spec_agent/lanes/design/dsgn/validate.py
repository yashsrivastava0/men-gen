"""Local checks of DSGN's output (code says no; the runner re-asks once, then the deterministic fallback).

Everything here is a pure function of (input, output): the count, the closed axes (the schema already enforces the sets),
the rule that the candidates differ (`tokens.check_direction_set`), that every seed colour yields accessible tokens
(`tokens.build_tokens` asserts contrast in light and dark), no form words in free text (V-25), and complete unbound needs.
"""

from __future__ import annotations

import re

from mde_spec_agent.lanes.design._tokens import tokens_for
from mde_spec_agent.lanes.design.dsgn._node import direction_id, seed_hue_of, to_direction
from mde_spec_agent.lanes.design.dsgn.schema import DsgnIn, DsgnOut
from mde_spec_core import assets, libraries, tokens, vocab
from mde_spec_core.config import SpecConfig, default_config

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
_RAW_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_URL = re.compile(r"https?://|www\.", re.I)


def config_for(inp: DsgnIn) -> SpecConfig:
    """The default config with this call's own thresholds (they travel in the input so the check is pure)."""
    cfg = default_config()
    checks = cfg.checks.model_copy(
        update={
            "min_direction_axis_differences": inp.min_axis_differences,
            "min_seed_hue_gap_degrees": inp.min_hue_gap,
        }
    )
    return cfg.model_copy(update={"checks": checks})


def picks_of(out: DsgnOut, inp: DsgnIn) -> list[dict[str, object]]:
    return [
        {
            "mood_family": d.mood,
            "shape": d.shape,
            "density": d.density,
            "depth": d.depth,
            "motion_personality": d.motion_personality,
            "imagery": d.imagery,
            "type_personality": d.type_personality,
            "seed_hue": None if inp.brand_locked else seed_hue_of(d.seed_colour),
        }
        for d in out.directions
    ]


def lint(where: str, text: str) -> list[str]:
    hits = libraries.form_word_findings(text)
    if not hits:
        return []
    words = ", ".join(sorted({h.word.lower() for h in hits}))
    return [f"{where}: uses form words ({words}); say what it communicates, not how it looks"]


def lint_avoid(where: str, text: str) -> list[str]:
    """The `avoid` list says what the brand must not feel like or do, so it may name widgets and layouts it rejects
    (a banner, a carousel). Only a hex colour is refused: a colour is the seed colour's alone."""
    if _RAW_HEX.search(text):
        return [
            f"{where}: a raw colour value is not allowed here; the seed colour is the only colour"
        ]
    return []


def validate(inp: DsgnIn, out: DsgnOut) -> list[str]:
    errors: list[str] = []
    if len(out.directions) != inp.count:
        errors.append(f"write exactly {inp.count} direction(s), got {len(out.directions)}")
        return errors
    names = [x.name.strip().lower() for x in out.directions]
    if len(set(names)) != len(names):
        errors.append("direction names must be different")
    for i, x in enumerate(out.directions):
        w = f"directions[{i}]"
        for field in ("name", "personality", "audience_context", "voice_line"):
            if not getattr(x, field).strip():
                errors.append(f"{w}.{field} must not be empty")
            errors += lint(f"{w}.{field}", getattr(x, field))
        if len(x.emotional_goals) < 3:
            errors.append(f"{w}.emotional_goals: write one line per journey stage (at least 3)")
        for j, g in enumerate(x.emotional_goals):
            errors += lint(f"{w}.emotional_goals[{j}]", g)
        if not x.avoid:
            errors.append(f"{w}.avoid: list at least one thing to avoid")
        for j, a in enumerate(
            x.avoid
        ):  # what to avoid may name a banner or a carousel (doc 17 5.2)
            errors += lint_avoid(f"{w}.avoid[{j}]", a)
        if x.admin_note.strip():
            errors += lint(f"{w}.admin_note", x.admin_note)
        if not inp.brand_locked and not (x.seed_colour and _HEX.match(x.seed_colour.strip())):
            errors.append(
                f"{w}.seed_colour must be a hex colour such as #3a6ea5 when no brand colour is Locked"
            )
    if errors:
        return errors
    # the candidates must differ (axes, and hue when no brand colour is Locked)
    cfg = config_for(inp)
    for msg in tokens.check_direction_set(picks_of(out, inp), inp.brand_locked, config=cfg):
        axes = ", ".join(vocab.direction_axes_file().diff_axes)
        errors.append(
            f"{msg}; make each pair differ on at least {inp.min_axis_differences} of: {axes}"
            + (
                ""
                if inp.brand_locked
                else f", and by at least {inp.min_hue_gap:g} degrees of seed hue"
            )
        )
    # every seed colour must give accessible tokens in light and dark (code asserts it)
    scripts = tuple(inp.scripts)
    brand = inp.brand_colour if inp.brand_locked else None
    for i, x in enumerate(out.directions):
        node = to_direction(inp, x, direction_id(i), brand_colour=brand)
        try:
            tokens_for(node, brand, scripts)
        except tokens.TokenError as exc:
            errors.append(
                f"directions[{i}].seed_colour {x.seed_colour}: no accessible tokens can be built from it ({exc}); pick a colour with some colour in it"
            )
    # unbound asset needs (counts, unknown apps, repeats and the maximum are repaired by code, see repair.py)
    for i, a in enumerate(out.assets):
        w = f"assets[{i}]"
        if not a.subject.strip():
            errors.append(f"{w}.subject must not be empty")
        if _URL.search(a.subject) or any(_URL.search(x) for x in a.avoid):
            errors.append(f"{w}: write what is needed, never a URL")
        bad = assets.lint_brief(a.subject, *a.avoid)
        if bad:
            errors.append(
                f"{w}: remove {', '.join(bad)} (lighting, placement, size and crop belong to the front end)"
            )
        errors += lint(f"{w}.subject", a.subject)
    return errors
