"""What the design lane reads: the baseline graph and the SpecInput, nothing the plan step is still deciding.

Design runs in parallel with the Product lane right after the plan, so roles, apps, features and journeys may still be
renamed: only ids and the Brief's own words are used here, and the words go into untrusted fields."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from mde_spec_agent.lanes.design._data import design_data
from mde_spec_agent.lanes.design.dsgn.schema import DsgnIn
from mde_spec_core import tokens, vocab
from mde_spec_core.graph import SpecGraph
from mde_spec_core.mock import script_for_language


@dataclass(frozen=True)
class BrandFacts:
    colour: str | None  # the user's Locked brand colour as `#rrggbb`, or None
    has_logo: bool
    ref: str  # the Brief id of the quote that supports it


def brand_facts(spec_input: Any) -> BrandFacts:
    """A brand colour counts only when the Brief's brand setting is Locked and carries a hex colour (FN-27, FN-28)."""
    brand = getattr(spec_input, "brand", None)
    if brand is None:
        return BrandFacts(None, False, "")
    pat = re.compile(design_data().brand_colour_pattern)
    colour: str | None = None
    if getattr(brand.trust, "grade", "") == "locked":
        for v in brand.values:
            m = pat.search(v)
            if m:
                try:
                    colour = tokens.to_hex(m.group(0))
                except tokens.TokenError:
                    colour = None
                break
    logo = any(re.search(r"\blogo\b", v, re.I) for v in brand.values)
    quotes = getattr(brand.trust, "quote_ids", ())
    return BrandFacts(colour, logo, quotes[0] if quotes else "")


def _brand_words(spec_input: Any) -> list[str]:
    pat = re.compile(design_data().brand_colour_pattern)
    return [pat.sub("", v).strip() for v in spec_input.brand.values if pat.sub("", v).strip()]


def brief_text(spec_input: Any, graph: SpecGraph) -> str:
    if spec_input is None:
        return "No Brief text is available; use only the facts above."
    si = spec_input
    idea = si.idea
    geo = ", ".join(x for x in (si.geography.city, si.geography.region, si.geography.country) if x)
    lines = [
        f"Idea: {idea.summary}",
        f"Category: {idea.category} / {idea.subcategory}; business model: {idea.business_model}",
        f"Brand: {'; '.join(_brand_words(si))}",
        f"Tone words: {', '.join(si.tone.values)}",
        f"Density asked for: {', '.join(si.density.values)}",
        f"Devices: {', '.join(si.devices.values)}",
        f"Place: {geo}",
        f"App languages: {', '.join(graph.meta.languages)}",
    ]
    return "\n".join(lines)


def audience_class(surface: str) -> str:
    return design_data().audience_class_by_surface.get(surface, "consumer")


def ambition_hint(graph: SpecGraph) -> str:
    """The default ambition: the majority audience class of the in-scope apps, through the vocabulary's defaults."""
    apps = [a for a in graph.apps.values() if a.status == "in"]
    classes = [audience_class(a.surface) for a in apps]
    cls = "enterprise" if classes and classes.count("enterprise") * 2 > len(classes) else "consumer"
    return str(vocab.direction_axes_file().ambition_defaults.get(cls, "expressive"))


def vocabulary_lines() -> list[str]:
    d = design_data()
    lines = [
        f"{name}: {' | '.join(axis.value_names)}" for name, axis in vocab.direction_axes().items()
    ]
    lines.append(f"asset kind: {' | '.join(d.model_asset_kinds)}")
    lines.append(f"asset purpose: {' | '.join(d.model_asset_purposes)}")
    return lines


def stable_seed(program_id: str, input_hash: str, brief: str) -> int:
    """Per program AND per Brief: two different apps from one generator get different candidates."""
    return int(hashlib.sha256(f"{program_id}|{input_hash}|{brief}".encode()).hexdigest()[:12], 16)


def build_dsgn_input(
    graph: SpecGraph, spec_input: Any, config: Any, program_id: str, input_hash: str
) -> tuple[DsgnIn, BrandFacts]:
    profile = graph.meta.profile
    brand = brand_facts(spec_input) if spec_input is not None else BrandFacts(None, False, "")
    apps = [a for _, a in sorted(graph.apps.items()) if a.status == "in"]
    ents = [
        e
        for _, e in sorted(graph.entities.items())
        if e.status == "in" and not e.later and e.klass in ("primary", "secondary")
    ][:12]
    langs = list(graph.meta.languages) or [graph.meta.source_language]
    scripts = sorted({script_for_language(lang) for lang in langs})
    audience = list(getattr(spec_input, "audience", ()) or ())
    brief = brief_text(spec_input, graph)
    inp = DsgnIn(
        language=graph.meta.source_language,
        profile=profile,
        count=int(config.profiles.directions[profile]),
        brand_locked=brand.colour is not None,
        brand_colour=brand.colour or "",
        has_logo=brand.has_logo,
        languages=langs,
        scripts=scripts,
        app_ids=[a.id for a in apps],
        app_classes=[f"{a.id} {audience_class(a.surface)}" for a in apps],
        ambition_hint=ambition_hint(graph),
        min_axis_differences=int(config.checks.min_direction_axis_differences),
        min_hue_gap=float(config.checks.min_seed_hue_gap_degrees),
        max_assets=int(design_data().max_model_assets.get(profile, 4)),
        seed=stable_seed(program_id, input_hash, brief),
        vocabularies=vocabulary_lines(),
        brief=brief,
        audience="\n".join(audience),
        apps="\n".join(f"{a.id} | {a.name} | {a.surface}" for a in apps),
        entities="\n".join(f"{e.name} | {e.klass}" for e in ents),
    )
    return inp, brand
