"""Offline mock of C7.extract: sentences of the (already cleaned) search text become 1 to 3 facts.

Behaves like a careful model: copies only what the text states, uses the numbered source markers
(``[1]``, ``[2]``) the research engine put into the text, returns a claim once per source it carries,
never turns an instruction found in the content into a fact (it sets ``injection_suspected``
instead). Deterministic; rules and word lists are in ``fixtures/rules.yaml``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml

from mde_contracts.calls import C7Input, C7Output, ExtractedFact
from mde_contracts.research import Confidence, FactKind, Impact

RULES = Path(__file__).parent / "fixtures" / "rules.yaml"
_MARKER = re.compile(r"\[(\d+)\]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_UNTRUSTED_TAG = re.compile(r"</?untrusted_content[^<>]{0,500}+>", re.IGNORECASE)
_SPACE_BEFORE_PUNCT = re.compile(r"(?<!\s)\s++([.,;:!?])")  # starts once per whitespace run


@lru_cache(maxsize=1)
def _rules() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(RULES.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def _instruction_res() -> tuple[re.Pattern[str], ...]:
    patterns = cast("list[str]", _rules()["instruction_patterns"])
    return tuple(re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns)


def looks_like_instruction(text: str) -> bool:
    return any(p.search(text) for p in _instruction_res())


@dataclass(frozen=True)
class _Sentence:
    position: int
    text: str
    sources: tuple[int, ...]


def _sentences(text: str) -> list[_Sentence]:
    out: list[_Sentence] = []
    rules = _rules()
    min_words = int(rules["min_words"])
    for position, raw in enumerate(_SENTENCE_END.split(" ".join(text.split()))):
        markers = tuple(dict.fromkeys(int(m) for m in _MARKER.findall(raw)))
        body = " ".join(_MARKER.sub("", raw).split())
        body = _SPACE_BEFORE_PUNCT.sub(r"\1", body).strip(" ()")
        if len(body.split()) < min_words:
            continue
        out.append(_Sentence(position=position, text=body, sources=markers))
    return out


def _kind(job: str, sentence: str) -> FactKind:
    rules = _rules()
    kind = cast("dict[str, str]", rules["job_kind"]).get(job, "other")
    if job in {"R1", "R2"}:
        tokens = set(re.findall(r"[a-z]+", sentence.casefold()))
        words = cast("list[str]", rules["pattern_words"])
        if any(w in tokens or f"{w}s" in tokens for w in words):
            kind = "pattern"
    return cast("FactKind", kind)


def _impact(job: str, sentence: str) -> Impact:
    rules = _rules()
    if job in cast("list[str]", rules["high_impact_jobs"]):
        return "high"
    lowered = sentence.casefold()
    if any(w in lowered for w in cast("list[str]", rules["high_impact_words"])):
        return "high"
    return "low"


def handle(input: C7Input) -> C7Output:
    text = _UNTRUSTED_TAG.sub(" ", input.search_text)
    if looks_like_instruction(text):
        return C7Output(facts=[], injection_suspected=True)
    urls = [s.url for s in input.sources]
    if not urls:
        return C7Output(facts=[])
    rules = _rules()
    candidates = _sentences(text)
    ranked = sorted(candidates, key=lambda s: (-min(len(s.sources), 2), s.position))
    picked = sorted(ranked[: int(rules["max_facts"])], key=lambda s: s.position)
    limit = int(rules["max_statement_chars"])
    facts: list[ExtractedFact] = []
    for sentence in picked:
        indexes = [i for i in sentence.sources if 1 <= i <= len(urls)][:2] or [1]
        statement = sentence.text[:limit].rstrip()
        confidence: Confidence = "high" if len(indexes) >= 2 else "medium"
        for index in indexes:
            facts.append(
                ExtractedFact(
                    statement=statement,
                    source_url=urls[index - 1],
                    kind=_kind(input.job, sentence.text),
                    impact=_impact(input.job, sentence.text),
                    confidence=confidence,
                )
            )
    return C7Output(facts=facts)
