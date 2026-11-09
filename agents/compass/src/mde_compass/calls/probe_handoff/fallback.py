"""Deterministic fallback planners of ``cmp.probe_handoff``: templates over the digest, gaps instead of guesses.

They plan only from what the digest says. When the digest lacks something the plan needs (a geography for
a query's region, roles for a journey, tone words for a direction) they leave that part empty and report the
gap in ``blocking_questions``; the code verdict then fails the probe honestly.
"""

from __future__ import annotations

import re
from typing import Any

from mde_compass.calls.compile_text.text_utils import clip
from mde_compass.calls.probe_handoff.rules import (
    LOOK_KINDS,
    details_of,
    digest_gaps,
    live_features,
    table_for,
)
from mde_compass.calls.probe_handoff.schema import (
    Digest,
    Journey,
    PlanQuery,
    ProbeHandoffInput,
    ProbeHandoffOutput,
    Screen,
)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
PART_CHARS = 60
MAX_QUERIES = 12
MAX_REFERENCE_QUERIES = 4
MAX_STEPS = 5


class _Safe(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _fill(template: str, values: dict[str, str]) -> str:
    return " ".join(template.format_map(_Safe(values)).split())


def _missing(template: str, values: dict[str, str]) -> bool:
    return any(not values.get(name, "").strip() for name in _PLACEHOLDER.findall(template))


def _join(items: list[str]) -> str:
    return clip(", ".join(i.strip() for i in items if i.strip()), PART_CHARS)


def _values(d: Digest) -> dict[str, str]:
    look = details_of(d, *LOOK_KINDS)
    return {
        "category": clip(d.category or d.idea, PART_CHARS),
        "geo": clip(d.geography, PART_CHARS),
        "audience": _join(d.audience),
        "business_model": clip(d.business_model, PART_CHARS),
        "locales": _join(d.locales),
        "tone": _join(d.tone_words) or (clip(look[0], PART_CHARS) if look else ""),
    }


def _query(spec: dict[str, Any], values: dict[str, str]) -> PlanQuery | None:
    needs = [*spec.get("requires", [])]
    if any(not values.get(n, "").strip() for n in needs):
        return None
    if _missing(spec["query"], values) or _missing(spec["entity"], values):
        return None
    terms = [_fill(t, values) for t in spec["terms"] if not _missing(t, values)]
    return PlanQuery(
        query=_fill(spec["query"], values),
        region=values["geo"],
        entity=_fill(spec["entity"], values),
        terms=terms,
    )


def research_plan(inp: ProbeHandoffInput) -> list[PlanQuery]:
    table, values = table_for(inp.language), _values(inp.digest)
    if not values["geo"].strip() or not values["category"].strip():
        return []  # a query without a region or an entity is useless: report the gap instead
    queries = [q for spec in table["research_queries"] if (q := _query(spec, values))]
    refs = [r.strip() for r in inp.digest.references if r.strip()][:MAX_REFERENCE_QUERIES]
    for ref in refs:
        q = _query(table["reference_query"], {**values, "reference": clip(ref, PART_CHARS)})
        if q:
            queries.append(q)
    return queries[:MAX_QUERIES]


def blueprint_plan(inp: ProbeHandoffInput) -> tuple[list[Journey], list[Screen]]:
    table = table_for(inp.language)
    d = inp.digest
    steps = [clip(f.name, PART_CHARS) for f in live_features(d)][:MAX_STEPS]
    journeys: list[Journey] = []
    for role in d.roles:
        name = role.name.strip()
        if not name or not steps:
            continue
        key = "journey_goal" if role.goal.strip() else "journey"
        title = _fill(
            table[key], {"role": clip(name, PART_CHARS), "goal": clip(role.goal, PART_CHARS)}
        )
        journeys.append(Journey(name=title, role=name, steps=steps))
    first = journeys[0].name if journeys else ""
    screens = [
        Screen(
            name=_fill(table["home_screen"], {"app": clip(a.name, PART_CHARS)}),
            journey=first,
            first_release=True,
        )
        for a in d.apps
        if a.name.strip()
    ]
    # each role the user confirmed gets its own first screen (a data-derived screen, not a guess)
    screens += [
        Screen(
            name=_fill(table["role_screen"], {"role": clip(r.name.strip(), PART_CHARS)}),
            journey=first,
            first_release=True,
        )
        for r in d.roles
        if r.name.strip()
    ]
    screens += [
        Screen(name=clip(f.name, PART_CHARS), journey=first, first_release=True)
        for f in live_features(d)
    ]
    # a screen the person described is a first-release screen; a typical one we proposed is not (yet)
    screens += [
        Screen(name=clip(text, PART_CHARS), journey=first, first_release=True)
        for text in details_of(d, "screen")
    ]
    screens += [
        Screen(name=clip(x.text, PART_CHARS), journey=first, first_release=False)
        for x in d.details
        if x.kind == "typical" and x.text.strip()
    ][:4]
    return journeys, screens


def design_plan(inp: ProbeHandoffInput) -> tuple[str, str]:
    table, values = table_for(inp.language), _values(inp.digest)
    tone = values["tone"]
    if not tone or not values["locales"]:
        return tone, ""
    key = "direction" if values["audience"] else "direction_no_audience"
    return tone, _fill(table[key], values)


def fallback(inp: ProbeHandoffInput) -> ProbeHandoffOutput:
    """Pure: the same input always gives the same plan and the same questions."""
    queries: list[PlanQuery] = []
    journeys: list[Journey] = []
    screens: list[Screen] = []
    tone = direction = ""
    if inp.kind == "research":
        queries = research_plan(inp)
    elif inp.kind == "blueprint":
        journeys, screens = blueprint_plan(inp)
    else:
        tone, direction = design_plan(inp)
    questions = digest_gaps(inp)
    return ProbeHandoffOutput(
        can_start=not questions,
        plan_queries=queries,
        journeys=journeys,
        screens=screens,
        tone=tone,
        direction=direction,
        blocking_questions=questions,
    )
