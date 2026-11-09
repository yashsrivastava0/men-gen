"""Code validators of ``cmp.make_variants``: exactly three, distinct, valid for the slot, honest basis.

Regexes are anchored or bounded (linear); texts are clipped before matching.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from pydantic import JsonValue

from mde_compass.calls.answer import text as tx
from mde_compass.calls.interpret_change.validate import (
    FIELD_KINDS,
    ParsedPath,
    parse_json,
    parse_path,
    value_ok,
)
from mde_compass.calls.make_variants.schema import (
    MAX_LABEL_WORDS,
    MAX_REASON_CHARS,
    VARIANT_COUNT,
    VariantOut,
    VariantsIn,
    VariantsOut,
)

NEAR_DUPLICATE_AT = 0.8
_PERCENT = re.compile(
    r"\d\s{0,2}(?:%|percent|per cent)|\b\d{1,3}\s{0,2}(?:x|times)\b", re.IGNORECASE
)


@lru_cache(maxsize=1)
def stats_words() -> tuple[str, ...]:
    words: tuple[str, ...] = tx.load_rules("mde_compass.calls.make_variants")["stats_words"]
    return words


def canon(value: JsonValue, name_field: str = "") -> str:
    """A comparable plain text for a value (record: its name; list: sorted items; text: itself)."""
    if value is None:
        return ""
    if isinstance(value, dict):
        named = value.get(name_field) if name_field else None
        if isinstance(named, str):
            return tx.normalize(named)
        return tx.normalize(" ".join(f"{k} {canon(v)}" for k, v in sorted(value.items())))
    if isinstance(value, list):
        return " ".join(sorted(canon(v) for v in value))
    return tx.normalize(str(value))


def _context_text(inp: VariantsIn) -> str:
    parts = [inp.reason, inp.current_value_json, *(c.value for c in inp.context)]
    return " ".join(parts)


def _family_items(inp: VariantsIn) -> set[str]:
    return {tx.normalize(i) for f in inp.families for i in f.items}


def _allowed_words(inp: VariantsIn) -> set[str]:
    parts = [_context_text(inp), *inp.options, *(c.label for c in inp.choices)]
    parts += [i for f in inp.families for i in f.items]
    return set(tx.words(" ".join(parts)))


def _value_errors(inp: VariantsIn, value: JsonValue, pp: ParsedPath | None) -> str | None:
    kind = inp.value_kind
    if kind == "record":
        if not isinstance(value, dict) or not inp.name_field:
            return "expected a record object"
        name = value.get(inp.name_field)
        if not isinstance(name, str) or not name.strip():
            return f"record needs a {inp.name_field}"
        allowed = FIELD_KINDS.get(pp.section, {}) if pp else {}
        extra = [k for k in value if allowed and k not in allowed]
        return f"unknown record fields {extra[:2]}" if extra else None
    if kind:
        return value_ok(kind, inp.options, value)
    return None if value not in (None, "", [], {}) else "empty value"


def _brand_errors(inp: VariantsIn, label: str) -> bool:
    """A capitalised word inside the label that the person, the options or the family data never used."""
    allowed = _allowed_words(inp)
    for tok in label.split()[1:]:
        word = tok.strip(".,;:!?\"'()")
        if word[:1].isupper() and not word.isupper() and tx.normalize(word) not in allowed:
            return True
    return False


def _variant_errors(inp: VariantsIn, v: VariantOut, current: str) -> list[str]:
    errors: list[str] = []
    label = v.label.strip()
    if not label or len(tx.words(label)) > MAX_LABEL_WORDS:
        errors.append(f"label must be 1 to {MAX_LABEL_WORDS} words: {label[:40]!r}")
    if not v.reason.strip() or len(v.reason) > MAX_REASON_CHARS:
        errors.append("reason must be one short sentence")
    ok, value = parse_json(v.value_json)
    if not ok:
        return [*errors, f"{label[:30]!r}: value_json is not JSON"]
    err = _value_errors(inp, value, parse_path(inp.path))
    if err:
        errors.append(f"{label[:30]!r}: {err}")
    text = tx.clip(f"{label} {v.reason}")
    if _PERCENT.search(text) or tx.has_phrase(text, stats_words()):
        errors.append(f"{label[:30]!r}: invented statistic")
    n_value, n_label = canon(value, inp.name_field), tx.normalize(label)
    if current and (n_value == current or n_label == current):
        errors.append(f"{label[:30]!r}: equals the current value")
    if _brand_errors(inp, label):
        errors.append(f"{label[:30]!r}: possible brand name")
    user_text = tx.normalize(_context_text(inp))
    if v.basis == "typical" and not ({n_value, n_label} & _family_items(inp)):
        errors.append(f"{label[:30]!r}: basis typical but not in the family data")
    if v.basis == "your_words" and not any(n and n in user_text for n in (n_value, n_label)):
        errors.append(f"{label[:30]!r}: basis your_words but the person did not say it")
    if v.basis == "your_document":
        errors.append(f"{label[:30]!r}: no document to base it on")
    return errors


def near_duplicates(a: VariantOut, b: VariantOut, name_field: str = "") -> bool:
    """Same after normalisation, or token-Jaccard of labels or values at least 0.8."""
    _, va = parse_json(a.value_json)
    _, vb = parse_json(b.value_json)
    ca, cb = canon(va, name_field), canon(vb, name_field)
    la, lb = tx.normalize(a.label), tx.normalize(b.label)
    if la == lb or (ca and ca == cb):
        return True
    return (
        tx.jaccard(a.label, b.label) >= NEAR_DUPLICATE_AT or tx.jaccard(ca, cb) >= NEAR_DUPLICATE_AT
    )


def current_text(inp: VariantsIn) -> str:
    _, cur = parse_json(inp.current_value_json)
    return canon(cur, inp.name_field)


def expected_count(inp: VariantsIn) -> int:
    """Three, unless an enum slot has fewer options different from the current value."""
    if inp.value_kind == "enum" and inp.options:
        _, cur = parse_json(inp.current_value_json)
        others = [o for o in inp.options if o != cur]
        return min(VARIANT_COUNT, len(others))
    return VARIANT_COUNT


def check_variants(inp: VariantsIn, out: VariantsOut) -> list[str]:
    """Errors that make the model output unusable; an empty list means it may be shown as a card."""
    expected = expected_count(inp)
    if len(out.variants) != expected:
        return [f"expected exactly {expected} variants, got {len(out.variants)}"]
    current = current_text(inp)
    errors: list[str] = []
    for v in out.variants:
        errors.extend(_variant_errors(inp, v, current))
    for i, a in enumerate(out.variants):
        for b in out.variants[i + 1 :]:
            if near_duplicates(a, b, inp.name_field):
                errors.append(f"near-duplicates: {a.label[:25]!r} and {b.label[:25]!r}")
    return errors


def parse_value(text: str) -> Any:
    return json.loads(text) if text.strip() else None
