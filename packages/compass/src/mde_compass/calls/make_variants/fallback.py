"""Deterministic half of ``cmp.make_variants``: alternatives from the slot's catalog choices, then from the
capability families' typical items. Returns what is valid, possibly fewer than three (the adapter then adds
a "Something else" escape). Never invents a value, a statistic or a brand."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import JsonValue

from mde_compass.calls.answer import text as tx
from mde_compass.calls.make_variants.schema import (
    VARIANT_COUNT,
    Basis,
    VariantOut,
    VariantsIn,
    VariantsOut,
)
from mde_compass.calls.make_variants.validate import (
    canon,
    current_text,
    near_duplicates,
    parse_value,
)


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    return tx.load_rules("mde_compass.calls.make_variants")


def _reason(key: str, language: str) -> str:
    return tx.pick(rules()["strings"][key], language)


def _shape(inp: VariantsIn, item: str) -> JsonValue:
    """A family item as a value for this path: a record object for list records, else the text."""
    return {inp.name_field: item} if inp.name_field else item


def _candidates(inp: VariantsIn) -> list[tuple[str, JsonValue, Basis, str]]:
    out: list[tuple[str, JsonValue, Basis, str]] = []
    reason_choice = _reason("reason_choice", inp.language)
    for c in inp.choices:
        out.append((c.label, parse_value(c.value_json), "suggestion", reason_choice))
    reason_typical = _reason("reason_typical", inp.language)
    context = tx.normalize(" ".join(c.value for c in inp.context))
    if not inp.options:  # an enum slot only accepts its own options, never family text
        for fam in inp.families:
            for item in fam.items:
                if tx.normalize(item) and not tx.occurs(context, tx.normalize(item)):
                    out.append((item, _shape(inp, item), "typical", reason_typical))
    return out


def fallback(inp: VariantsIn) -> VariantsOut:
    """Pure ``input -> output``: up to three alternatives, each different from the current value and from
    each other; fewer when the data offers fewer."""
    current = current_text(inp)
    picked: list[VariantOut] = []
    for label, value, basis, reason in _candidates(inp):
        if len(tx.words(label)) > 8 or not label.strip():
            continue
        n_value = canon(value, inp.name_field)
        if current and (n_value == current or tx.normalize(label) == current):
            continue
        variant = VariantOut(
            label=label.strip(),
            value_json=json.dumps(value, ensure_ascii=False),
            reason=reason,
            basis=basis,
        )
        if any(near_duplicates(variant, v, inp.name_field) for v in picked):
            continue
        picked.append(variant)
        if len(picked) == VARIANT_COUNT:
            break
    return VariantsOut(variants=picked)
