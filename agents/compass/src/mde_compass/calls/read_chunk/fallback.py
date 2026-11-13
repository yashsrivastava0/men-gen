"""Deterministic reader for ``cmp.read_chunk``: sentence, bullet and table-row splitter with keyword typing
from ``rules.yaml`` (en, hi, hinglish). Pure, linear, never invents: every quote is a slice of the chunk.
Also the mock handler's engine and the adapter's fallback."""

from __future__ import annotations

from mde_compass.calls.read_chunk.rules import Rules, TypeRule, load_rules
from mde_compass.calls.read_chunk.schema import ReadChunkInput, ReadChunkOutput, WireStatement
from mde_compass.calls.read_chunk.textutil import (
    has_phrase,
    is_number,
    normalize,
    split_units,
    tokens,
)
from mde_compass.domain.claims import StatementType


def looks_like_injection(text: str, rules: Rules | None = None) -> bool:
    """True if ``text`` contains an instruction-to-the-reader marker (never turned into a statement)."""
    r = rules or load_rules()
    norm = normalize(text)
    return any(has_phrase(norm, marker) for marker in r.injection_markers)


def _has_number_with_unit(toks: list[str], rules: Rules) -> bool:
    for i, tok in enumerate(toks):
        if rules.unit_symbols & {tok}:
            if i + 1 < len(toks) and is_number(toks[i + 1]):
                return True
            if i > 0 and is_number(toks[i - 1]):
                return True
            continue
        if not is_number(tok):
            continue
        if i + 1 < len(toks) and (toks[i + 1] in rules.units or toks[i + 1] in rules.unit_symbols):
            return True
        if i > 0 and toks[i - 1] in rules.unit_symbols:
            return True
        head = tok.rstrip("abcdefghijklmnopqrstuvwxyz")
        if head != tok and tok[len(head) :] in rules.units:
            return True
    return False


def _matches(rule: TypeRule, norm: str, toks: list[str], rules: Rules) -> bool:
    if rule.type == "rule_or_number" and _has_number_with_unit(toks, rules):
        return True
    if rule.words & set(toks):
        return True
    return any(has_phrase(norm, p) for p in rule.phrases)


def classify(text: str, rules: Rules | None = None) -> StatementType | None:
    """The statement type of one sentence, or ``None`` if it is not worth a statement."""
    r = rules or load_rules()
    toks = tokens(text)
    if not toks:
        return None
    norm = " ".join(toks)
    for rule in r.types:
        if rule.type == "glossary_term" and not _glossary_shape(rule, norm, toks):
            continue
        if _matches(rule, norm, toks, r):
            return rule.type  # type: ignore[return-value]
    if len(toks) >= r.min_words_business_context:
        return "business_context"
    return None


def _glossary_shape(rule: TypeRule, norm: str, toks: list[str]) -> bool:
    """A glossary hit needs the defining word after at least one term word ("X means Y")."""
    for i, tok in enumerate(toks):
        if i >= 1 and i + 1 < len(toks) and tok in rule.words:
            return True
    return any(has_phrase(norm, p) and not norm.startswith(p + " ") for p in rule.phrases)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip()


def read_units(chunk_text: str, rules: Rules | None = None) -> list[WireStatement]:
    """All statements found by rules, in document order."""
    r = rules or load_rules()
    out: list[WireStatement] = []
    seen: set[str] = set()
    for start, end in split_units(chunk_text, r.abbreviations):
        unit = chunk_text[start:end]
        if len(unit) < r.min_quote_chars or looks_like_injection(unit, r):
            continue
        kind = classify(unit, r)
        if kind is None:
            continue
        key = f"{kind}|{normalize(unit)}"
        if key in seen:
            continue
        seen.add(key)
        conf = r.confidence["business_context" if kind == "business_context" else "keyword"]
        out.append(
            WireStatement(
                type=kind,
                text=_clip(unit, r.max_text_chars),
                quote=_clip(unit, r.max_quote_chars),
                confidence=conf,  # type: ignore[arg-type]
            )
        )
        if len(out) >= r.max_statements:
            break
    return out


def fallback(inp: ReadChunkInput) -> ReadChunkOutput:
    """The deterministic ``cmp.read_chunk``: statements by keyword rules; flags injection-looking text."""
    rules = load_rules()
    suspected = looks_like_injection(inp.chunk_text, rules)
    return ReadChunkOutput(
        statements=read_units(inp.chunk_text, rules), injection_suspected=suspected
    )
