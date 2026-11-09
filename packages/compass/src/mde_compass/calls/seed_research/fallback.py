"""Deterministic fallback of ``cmp.seed_research``: requests from ``templates.yaml`` over the Brief.

At least three requests (comparable apps, local players, rules; payments when money flows), at most eight
(each named reference app adds one while there is room). Every request has a geography, an entity and
terms; with no geography at all the word ``global`` is used and the priority is low. Nothing is invented.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

from mde_compass.calls.compile_text.text_utils import template_lang
from mde_compass.calls.seed_research.rules import (
    GLOBAL_WORD,
    _clean,
    geography_text,
    named_references,
)
from mde_compass.calls.seed_research.schema import (
    FALLBACK_MAX_REQUESTS,
    SeedResearchInput,
    SeedResearchOutput,
    WireHints,
    WireRequest,
)

TEMPLATES_PATH = Path(__file__).parent / "templates.yaml"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
PART_CHARS = 60
MAX_GLOSSARY_TERMS = 4


@functools.lru_cache(maxsize=1)
def load_templates() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {str(k): dict(v) for k, v in raw.items()}


class _Safe(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _fill(template: str, values: dict[str, str]) -> str:
    return " ".join(template.format_map(_Safe(values)).split())


def _short(text: str) -> str:
    text = " ".join(text.split())
    return (
        text
        if len(text) <= PART_CHARS
        else text[:PART_CHARS].rsplit(" ", 1)[0] or text[:PART_CHARS]
    )


def _values(inp: SeedResearchInput, table: dict[str, Any]) -> dict[str, str]:
    d = table["defaults"]
    subcategory = _short(inp.subcategory)
    category = _short(inp.category) or subcategory or d["category"]
    geo = geography_text(inp)
    first_feature = next((f.name for f in inp.features if f.name.strip()), "")
    return {
        "category": category,
        "subcategory": subcategory if subcategory.casefold() != category.casefold() else "",
        "audience": _short(", ".join(_clean(inp.audience))) or d["audience"],
        "geo": _short(geo) if geo != GLOBAL_WORD else d["global"],
        "business_model": _short(inp.business_model),
        "flow": _short(first_feature) or d["flow"],
    }


def _terms(
    templates: list[str], values: dict[str, str], inp: SeedResearchInput, table: Any
) -> list[str]:
    terms: list[str] = []
    for tpl in templates:
        wanted = _PLACEHOLDER.findall(tpl)
        if any(not values.get(name, "").strip() for name in wanted):
            continue
        terms.append(_fill(tpl, values))
    glossary: list[str] = []
    for g in inp.glossary:
        glossary += [g.term, *g.synonyms[:2]]
    terms += _clean(glossary)[:MAX_GLOSSARY_TERMS]
    return _clean(terms) or list(table["defaults"]["terms"])


def _request(
    spec: dict[str, Any],
    values: dict[str, str],
    inp: SeedResearchInput,
    table: dict[str, Any],
    *,
    references: list[str],
) -> WireRequest:
    global_geo = geography_text(inp) == GLOBAL_WORD
    avoid = _clean(inp.exclusions)
    return WireRequest(
        topic=_fill(spec["topic"], values),
        question=_fill(spec["question"], values),
        decision_unlocked=_fill(spec["decision_unlocked"], values),
        entity=_fill(spec["entity"], values),
        geography=geography_text(inp),
        source_languages=_clean(inp.languages) or [inp.language],
        terms=_terms(spec["terms"], values, inp, table),
        references=references,
        prefer_sources=list(spec["prefer_sources"]),
        avoid=avoid,
        depth=spec["depth"],
        priority="low" if global_geo else spec["priority"],
        shown_to_user_as=_fill(spec["shown_to_user_as"], values),
    )


def fallback(inp: SeedResearchInput) -> SeedResearchOutput:
    """Pure: three to eight specific requests and the hints, from the Brief and the templates only."""
    table = load_templates()[template_lang(inp.language)]
    values = _values(inp, table)
    named = list(named_references(inp).values())
    requests: list[WireRequest] = []
    for spec in table["requests"]:
        needs = spec.get("requires")
        if needs and not values.get(needs, "").strip():
            continue
        refs = named if spec.get("with_references") else []
        requests.append(_request(spec, values, inp, table, references=refs))
    for ref in named:
        if len(requests) >= FALLBACK_MAX_REQUESTS:
            break
        rvalues = {**values, "reference": _short(ref)}
        requests.append(_request(table["reference_request"], rvalues, inp, table, references=[ref]))
    synonyms: list[str] = []
    for g in inp.glossary:
        synonyms += g.synonyms
    hints = WireHints(
        disambiguated_names=named,
        synonyms=_clean(synonyms),
        local_competitors=named,
        languages=_clean(inp.languages) or [inp.language],
    )
    return SeedResearchOutput(requests=requests[:FALLBACK_MAX_REQUESTS], hints=hints)
