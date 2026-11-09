"""Deterministic scanner for ``cmp.scan_ambiguity``: vague-word table, undefined acronyms (not in the glossary)
and contradictions between lines (reusing the merger's rules). Pure and linear; never changes a value (the
output type has no value field). v2 also compares the details (``facts``) with the Brief values and with each other,
and reports a share above 100 percent as unrealistic; the plausibility, dependency and timeline judgements need the
model, so the floor reports none of them rather than guess."""

from __future__ import annotations

import re

from mde_compass.calls.merge_claims.conflicts import find_conflicts
from mde_compass.calls.read_chunk.textutil import has_phrase, normalize, tokens
from mde_compass.calls.scan_ambiguity.rules import ScanRules, load_rules
from mde_compass.calls.scan_ambiguity.schema import DigestLine, ScanInput, ScanOutput, WireFinding


def _verbatim(text: str, term: str) -> str:
    """The term as written in ``text`` (case kept) when it can be located, else the normalised term."""
    low = text.casefold()
    at = low.find(term)
    if at >= 0 and len(low) == len(text):
        return text[at : at + len(term)]
    return term


_FACT_PREFIX = re.compile(r"^FCT-\d+ \[[^\]]*\] ")


def _body(text: str) -> str:
    """A digest line without its ``FCT-004 [rule]`` prefix (the id is a number, it must not count as a value)."""
    return _FACT_PREFIX.sub("", text, count=1)


def vague_terms(text: str, rules: ScanRules) -> list[str]:
    """Vague words and phrases in one line; none if the line carries a number (it is quantified)."""
    toks = tokens(text)
    if any(t[0].isdigit() for t in toks):
        return []
    found: list[str] = []
    for t in toks:
        if t in rules.vague_words and t not in found:
            found.append(t)
    norm = " ".join(toks)
    phrases = [p for p in rules.vague_phrases if has_phrase(norm, p)]
    covered = {w for p in phrases for w in p.split()}
    return [t for t in found if t not in covered] + phrases


def undefined_terms(text: str, defined: frozenset[str], rules: ScanRules) -> list[str]:
    """Short ALL-CAPS terms that are neither common nor defined in the glossary."""
    out: list[str] = []
    for raw in text.split():
        word = raw.strip(".,;:!?()[]{}\"'")
        if (
            rules.min_acronym_len <= len(word) <= rules.max_acronym_len
            and word.isascii()
            and word.isalpha()
            and word.isupper()
            and word.casefold() not in rules.common_terms
            and word.casefold() not in defined
            and word not in out
        ):
            out.append(word)
    return out


_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:%|percent|prati?shat|प्रतिशत)", re.IGNORECASE)


def impossible_shares(text: str, rules: ScanRules) -> list[str]:
    """Shares above the possible maximum ("150% of customers"), as written in the line."""
    out: list[str] = []
    for match in _PERCENT.finditer(text):
        try:
            value = float(match.group(1).replace(",", "."))
        except ValueError:  # pragma: no cover - the pattern only matches digits
            continue
        if value > rules.max_percent and match.group(0).strip() not in out:
            out.append(match.group(0).strip())
    return out


def defined_terms(glossary: list[str]) -> frozenset[str]:
    return frozenset(normalize(g) for g in glossary if normalize(g))


def fallback(inp: ScanInput) -> ScanOutput:
    """The deterministic ``cmp.scan_ambiguity``: conflicts first, then undefined terms, then vague words."""
    rules = load_rules()
    items: list[DigestLine] = inp.items[: rules.max_items]
    details: list[DigestLine] = inp.facts[: rules.max_fact_lines]
    defined = defined_terms(inp.glossary_terms)
    conflicts: list[WireFinding] = []
    both = [*items, *details]  # a detail can clash with a Brief value or with another detail
    for i, j, _ in find_conflicts([_body(it.text) for it in both]):
        a, b = both[i], both[j]
        text = f"{a.text[:80].strip()} / {b.text[:80].strip()}"
        conflicts.append(
            WireFinding(
                slot_id=a.slot_id if a.slot_id is not None else b.slot_id,
                text=text[: rules.max_finding_chars],
                kind="conflicting",
            )
        )
    unrealistic: list[WireFinding] = []
    for it in both:
        for share in impossible_shares(_body(it.text), rules):
            unrealistic.append(
                WireFinding(
                    slot_id=it.slot_id,
                    text=f"{share} in: {it.text[:120].strip()}"[: rules.max_finding_chars],
                    kind="unrealistic",
                )
            )
    undefined: list[WireFinding] = []
    vague: list[WireFinding] = []
    for it in items:
        for term in undefined_terms(it.text, defined, rules):
            undefined.append(WireFinding(slot_id=it.slot_id, text=term, kind="undefined"))
        for term in vague_terms(it.text, rules):
            vague.append(
                WireFinding(slot_id=it.slot_id, text=_verbatim(it.text, term), kind="vague")
            )
    return ScanOutput(findings=(conflicts + unrealistic + undefined + vague)[: rules.max_findings])
