"""Offline mock of C8.synthesize_research: a canned, schema-valid draft per archetype.

Deterministic. Suggestions come from ``fixtures/drafts.yaml`` and are emitted only when a fact of the
call's input supports them (so the draft always cites real fact ids). Compliance pointers, the brand
finding and integration notes are derived from the matching facts. Features the Brief already has
are skipped. On Deep depth the first round reports one gap so the gap-fill round runs offline too.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml

from mde_contracts.calls import (
    BrandDraft,
    C8Input,
    C8Output,
    FactForSynthesis,
    IntegrationDraft,
    PointerDraft,
    SuggestionDraft,
)

DRAFTS = Path(__file__).parent / "fixtures" / "drafts.yaml"
_HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
_FONTS = re.compile(r"fonts? observed:\s*([^;.]+)", re.IGNORECASE)
_TONE = re.compile(r"tone of voice is\s+([a-z]+(?:\s+and\s+[a-z]+)?)", re.IGNORECASE)


@lru_cache(maxsize=1)
def _drafts() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(DRAFTS.read_text(encoding="utf-8")))


def _matches(facts: list[FactForSynthesis], words: list[str]) -> list[FactForSynthesis]:
    folded = [w.casefold() for w in words]
    return [f for f in facts if any(w in f.statement.casefold() for w in folded)]


def _suggestions(inp: C8Input) -> list[SuggestionDraft]:
    drafts = _drafts()
    block = cast("dict[str, Any]", drafts["archetypes"]).get(inp.brief.archetype)
    if block is None:
        block = drafts["archetypes"]["custom"]
    have = {f.id for f in inp.brief.features}
    out: list[SuggestionDraft] = []
    seen: set[str] = set()
    for item in [*cast("list[dict[str, Any]]", block["suggestions"]), *drafts["common"]]:
        feature_id = cast("str | None", item.get("feature_id"))
        if feature_id and feature_id in have:
            continue
        hits = _matches(inp.facts, cast("list[str]", item["match"]))
        if not hits or item["title"] in seen:
            continue
        seen.add(item["title"])
        out.append(
            SuggestionDraft(
                kind=item["kind"],
                title=item["title"],
                rationale=item["rationale"],
                fact_ids=[f.id for f in hits][:3],
                default_included=bool(item["default_included"]),
                applies_to=item.get("applies_to"),
            )
        )
        if len(out) >= inp.max_suggestions:
            break
    return out


def _pointers(inp: C8Input) -> list[PointerDraft]:
    pointers = cast("dict[str, dict[str, Any]]", _drafts()["pointers"])
    compliance = [f for f in inp.facts if f.kind == "compliance" and f.source_urls]
    out: list[PointerDraft] = []
    for flag in inp.brief.compliance_flags:
        info = pointers.get(flag)
        if info is None or not compliance:
            continue
        hits = _matches(compliance, cast("list[str]", info["words"])) or compliance
        out.append(PointerDraft(flag=flag, title=info["title"], url=hits[0].source_urls[0]))
    return out


def _brand(inp: C8Input) -> BrandDraft | None:
    facts = [f for f in inp.facts if f.kind == "brand" and f.source_urls]
    if not facts:
        return None
    text = ". ".join(f.statement for f in facts)
    colors = list(dict.fromkeys(c.upper() for c in _HEX.findall(text)))
    fonts: list[str] = []
    for found in _FONTS.findall(text):
        fonts += [p.strip() for p in re.split(r",|\band\b", found) if p.strip()]
    tone: list[str] = []
    for found in _TONE.findall(text):
        tone += [p.strip().lower() for p in re.split(r"\band\b|,", found) if p.strip()]
    directions = cast("dict[str, str]", _drafts()["tone_directions"])
    nearest = next((directions[t] for t in tone if t in directions), None)
    return BrandDraft(
        source_url=facts[0].source_urls[0],
        colors=colors[:6],
        fonts=list(dict.fromkeys(fonts))[:3],
        tone_words=list(dict.fromkeys(tone))[:5],
        logo_available="logo file is available" in text.casefold(),
        nearest_direction=nearest,
    )


def _integrations(inp: C8Input) -> list[IntegrationDraft]:
    topics = cast("list[dict[str, Any]]", _drafts()["integration_topics"])
    facts = [f for f in inp.facts if f.kind == "integration" and f.source_urls]
    by_provider: dict[str, list[FactForSynthesis]] = {}
    for fact in facts:
        folded = fact.statement.casefold()
        for topic in topics:
            if any(w in folded for w in cast("list[str]", topic["words"])):
                by_provider.setdefault(topic["provider"], []).append(fact)
                break
    out: list[IntegrationDraft] = []
    for provider, group in by_provider.items():
        text = " ".join(f.statement for f in group).casefold()
        caveats = [
            f.statement[:160]
            for f in group
            if any(w in f.statement.casefold() for w in ("development build", "cannot", "api key"))
        ][:3]
        out.append(
            IntegrationDraft(
                provider=provider,
                sdk_available=True if "sdk" in text else None,
                expo_compatible=True
                if ("development build" in text or "web view" in text)
                else None,
                caveats=caveats,
                source_url=group[0].source_urls[0],
            )
        )
    return out[:6]


def _gaps(inp: C8Input) -> list[str]:
    if inp.gap_round or inp.depth != "deep":
        return []
    block = cast("dict[str, Any]", _drafts()["archetypes"]).get(inp.brief.archetype)
    phrase = cast("str | None", (block or _drafts()["archetypes"]["custom"]).get("gap_phrase"))
    return [phrase] if phrase else []


def handle(input: C8Input) -> C8Output:
    return C8Output(
        suggestions=_suggestions(input),
        compliance=_pointers(input),
        brand=_brand(input),
        integrations=_integrations(input),
        gaps=_gaps(input),
    )
