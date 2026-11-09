"""Deterministic situate: the mock handler and the fallback of ``cmp.situate``.

The profile is inferred from the text (length, cues) and the hypothesis from keyword overlap with the catalog
families. Everything is labelled ``hypothesis``; user facts are never invented (the idea line is the user's own
first sentence). Linear: the text is clipped, patterns are literal alternations or bounded.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_compass.calls.situate.schema import (
    FeatureWire,
    HypothesisWire,
    ProfileWire,
    SituateInput,
    SituateOutput,
    TermWire,
)
from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.understand_turn.fallback import extract_references
from mde_compass.catalog.loader import Catalog, Family, load_catalog

_LEX = Path(__file__).with_name("lexicon.yaml")
MAX_ONE_LINE = 160
MAX_FAMILIES = 3
MAX_FEATURES = 6
MAX_ROLES = 6
MAX_BUSINESS = 3
MAX_TERMS = 5
ONE_SENTENCE_WORDS = 25
DOCUMENT_CHARS = 3_000
KNOWS_EVERYTHING_WORDS = 120
_SENTENCE_END = re.compile(r"[.!?।]\s{1,4}")


@lru_cache(maxsize=1)
def _lex() -> dict[str, list[str]]:
    raw: dict[str, Any] = yaml.safe_load(_LEX.read_text(encoding="utf-8"))
    return {k: [str(x) for x in v] for k, v in raw.items()}


def _has(text: str, key: str) -> bool:
    return lx.has_phrase(text, _lex()[key])


def _scan_text(inp: SituateInput) -> str:
    parts = [inp.user_text, *(s.text for s in inp.statements[:40])]
    return lx.clip(" ".join(parts))


def _family_hits(text: str, fam: Family) -> list[str]:
    """Keywords of the family as the user wrote them (distinct, in keyword order)."""
    found: list[str] = []
    for kw in fam.keywords:
        span = lx.find_first(text, [kw])
        if span is not None and text[span[0] : span[1]].casefold() not in {
            f.casefold() for f in found
        }:
            found.append(text[span[0] : span[1]])
    return found


def _richness(inp: SituateInput) -> str:
    text = inp.user_text.strip()
    if not text and not inp.statements:
        return "nothing"
    if len(text) > DOCUMENT_CHARS or len(inp.statements) > 8 or _has(text, "documents"):
        return "documents"
    if len(text.split()) <= ONE_SENTENCE_WORDS and len(_SENTENCE_END.findall(text)) < 2:
        return "one_sentence"
    return "rich"


def _clarity(text: str, hits_total: int, feature_mentions: int, words: int) -> str:
    if words >= KNOWS_EVERYTHING_WORDS and hits_total >= 3:
        return "knows_everything"
    if feature_mentions >= 2 or (hits_total >= 2 and words >= 30):
        return "knows_features"
    return "vague"


def _first_sentence(text: str) -> str:
    flat = lx.squash_spaces(text).strip()
    m = _SENTENCE_END.search(flat)
    first = flat[: m.start() + 1] if m else flat
    return first[:MAX_ONE_LINE].strip()


def fallback(inp: SituateInput, catalog: Catalog | None = None) -> SituateOutput:
    """Profile and a labelled hypothesis from the text alone (see the module docstring)."""
    cat = catalog or load_catalog()
    text = _scan_text(inp)
    words = len(inp.user_text.split())
    families = [cat.families[f.id] for f in inp.families if f.id in cat.families] or list(
        cat.families.values()
    )
    scored = [(fam, _family_hits(text, fam)) for fam in families]
    scored = [(f, h) for f, h in scored if h]
    scored.sort(key=lambda x: -len(x[1]))  # stable: ties keep catalog order
    top = scored[:MAX_FAMILIES]
    hits_total = sum(len(h) for _, h in scored)

    feature_names = {lx.normalize(ft.name.en): ft for fam, _ in top for ft in fam.typical_features}
    mentions = sum(1 for n in feature_names if n and lx.has_phrase(text, [n]))

    refs = extract_references(inp.user_text)
    profile = ProfileWire(
        input_richness=_richness(inp),  # type: ignore[arg-type]
        clarity=_clarity(text, hits_total, mentions, words),  # type: ignore[arg-type]
        expertise="technical"
        if _has(text, "technical")
        else "product"
        if _has(text, "product")
        else "non_technical",
        size_stakes="enterprise"
        if _has(text, "enterprise") or inp.mode == "enterprise"
        else "tiny"
        if _has(text, "tiny")
        else "startup",
        reference_driven=bool(refs),
        collaboration="several" if _has(text, "several") or inp.mode == "enterprise" else "one",
        language=lx.detect_language(inp.user_text, inp.language),
        input_mode=inp.input_mode,
        urgency="just_build"
        if lx.has_phrase(text, tuple(lx.load_lexicon().intents["finish_with_defaults"]))
        or _has(text, "urgency")
        else "thorough",
        formality="professional"
        if _has(text, "professional") and not _has(text, "casual")
        else "casual",
    )

    best_id = top[0][0].id if top else ""
    best_hit = top[0][1][0] if top else ""
    business: list[str] = []
    roles: list[str] = []
    features: list[FeatureWire] = []
    terms: list[TermWire] = []
    for fam, hits in top:
        business += [b for b in fam.typical_business_models if b not in business]
        roles += [
            r.replace("_", " ") for r in fam.typical_roles if r.replace("_", " ") not in roles
        ]
        for ft in fam.typical_features[: max(1, MAX_FEATURES // max(1, len(top)))]:
            features.append(
                FeatureWire(family=fam.id, name=ft.name.en, intent=ft.intent, custom=False)
            )
        term = hits[0]
        terms.append(
            TermWire(
                term=term,
                synonyms=[k for k in fam.keywords if lx.normalize(k) != lx.normalize(term)][:3],
            )
        )
    hard = []
    for cap in (*cat.tier_b(), *cat.declined()):
        if lx.find_first(text, cap.keywords) is not None:
            hard.append(cap.label.en)
    confidence = 0.0 if not top else min(0.6, 0.2 + 0.1 * hits_total)
    hypothesis = HypothesisWire(
        label="hypothesis",
        category=best_id,
        subcategory=best_hit,
        business_model_candidates=business[:MAX_BUSINESS],
        likely_families=[f.id for f, _ in top],
        likely_roles=roles[:MAX_ROLES],
        likely_features=features[:MAX_FEATURES],
        hard_parts=hard[:MAX_ROLES],
        glossary_candidates=terms[:MAX_TERMS],
        confidence=round(confidence, 2),
    )
    return SituateOutput(
        profile=profile, hypothesis=hypothesis, one_line_idea=_first_sentence(inp.user_text)
    )
