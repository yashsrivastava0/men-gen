"""Deterministic half of ``cmp.interpret_change``: rule-based interpretation with phrase tables.

It only maps what the text states onto the targeted items. It never guesses a value: anything it cannot map
becomes ``understood=False`` with a clarification (an either/or question when there are candidates).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from mde_compass.calls.answer import text as tx
from mde_compass.calls.interpret_change.schema import (
    USE_VARIANTS,
    ChangeIn,
    ChangeOut,
    DigestItem,
    OpOut,
)
from mde_compass.calls.interpret_change.validate import NAME_FIELD, value_ok
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.changes import OpName

MIN_LABEL_CHARS = 3
MAX_OPTIONS = 3
MAX_NAME_TOKENS = 6
SCOPE = "scope"


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    return tx.load_rules("mde_compass.calls.interpret_change")


def _s(key: str, lang: str, **values: str) -> str:
    text = tx.pick(rules()["strings"][key], lang)
    return text.format(**values) if values else text


def _no(inp: ChangeIn, *, clarification: str = "", options: list[str] | None = None) -> ChangeOut:
    """Not understood: a clarification built from the catalog (or the given one)."""
    lang = "hi" if inp.language == "hi" else "en"
    text = clarification or load_catalog().t("fallback.not_sure_rephrase", lang)
    return ChangeOut(
        understood=False, ops=[], clarification=text, options=options or [], confidence=0.0
    )


def _op(op: OpName, path: str, value: object = None, old: str = "null") -> OpOut:
    return OpOut(
        op=op, path=path, value_json=json.dumps(value, ensure_ascii=False), old_value_json=old
    )


def _fields(item: DigestItem) -> dict[str, Any]:
    try:
        value = json.loads(item.value_json)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _candidates(text: str, items: list[DigestItem]) -> list[DigestItem]:
    """Items whose label occurs in the text as whole words; a label inside a longer matching label is dropped."""
    hay = tx.normalize(text)
    found = [
        (i, n)
        for i in items
        if len(n := tx.normalize(i.label)) >= MIN_LABEL_CHARS and tx.occurs(hay, n)
    ]
    return [i for i, n in found if not any(n != m and n in m for _, m in found)]


def _ambiguous(inp: ChangeIn, labels: list[str]) -> ChangeOut:
    lang = inp.language
    options = labels[:MAX_OPTIONS]
    if len(options) == 2:
        question = _s("either_or", lang, a=options[0], b=options[1])
    else:
        question = _s("which_one", lang)
    return _no(inp, clarification=question, options=options)


def _use_variants(inp: ChangeIn) -> ChangeOut:
    return ChangeOut(
        understood=False,
        ops=[],
        clarification=_s("use_variants_note", inp.language),
        options=[USE_VARIANTS],
        confidence=0.7,
    )


def _level_ops(item: DigestItem, level: str) -> list[OpOut] | None:
    fields = _fields(item)
    if item.value_kind != "record" or "priority" not in fields:
        return None
    ops = [_op("set", f"{item.path}.priority", level, json.dumps(fields["priority"]))]
    status = fields.get("scope_status")
    if level == "later" and status == "in":
        ops.append(_op("set", f"{item.path}.scope_status", "later", json.dumps(status)))
    if level in ("must", "nice") and status in ("later", "out"):
        ops.append(_op("set", f"{item.path}.scope_status", "in", json.dumps(status)))
    return ops


def _locale_first(inp: ChangeIn, text: str) -> ChangeOut | None:
    r = rules()
    item = next((i for i in inp.items if i.path == "experience.locales"), None)
    if item is None or not tx.has_phrase(text, r["first"]):
        return None
    names = {tx.normalize(k): v for k, v in r["locale_names"].items()}
    codes = list(dict.fromkeys(names[w] for w in tx.words(text) if w in names))
    if len(codes) != 1:
        return None
    code = codes[0]
    try:
        current = json.loads(item.value_json)
    except ValueError:
        return None
    if isinstance(current, list) and all(isinstance(c, str) for c in current):
        new: object = [code, *[c for c in current if c != code]]
    elif isinstance(current, dict) and isinstance(current.get("app"), list):
        new = {**current, "app": [code, *[c for c in current["app"] if c != code]]}
    else:
        return None
    if value_ok("locales", [], new) is not None:
        return None
    return ChangeOut(
        understood=True,
        ops=[_op("set", item.path, new, item.value_json)],
        clarification="",
        options=[],
        confidence=0.75,
    )


def _add_name(text: str) -> str:
    """The user's own words for a new item: the tokens after (or, in Hindi order, before) the add cue."""
    r = rules()
    tokens = text.split()
    norm = [tx.normalize(t) for t in tokens]
    cues = sorted(r["add"], key=lambda c: -len(c.split()))
    for i in range(len(tokens)):
        for cue in cues:
            n = len(cue.split())
            if " ".join(norm[i : i + n]) == cue:
                after = _name_tokens(tokens[i + n :], norm[i + n :], r)
                return after or _name_tokens(tokens[:i][::-1], norm[:i][::-1], r, before=True)
    return ""


def _name_tokens(
    tokens: list[str], norm: list[str], r: dict[str, Any], *, before: bool = False
) -> str:
    stop, end = set(r["name_stop"]), set(r["name_end"])
    picked: list[str] = []
    for tok, n in zip(tokens, norm, strict=False):
        if not n:
            continue
        if n in end and picked:
            break
        if n in stop and not picked:
            continue
        if n in end:
            continue
        picked.append(tok.strip(".,;:!?\"'()"))
        if len(picked) >= MAX_NAME_TOKENS:
            break
    if before:
        picked.reverse()
    name = " ".join(p for p in picked if p)
    return name[:1].upper() + name[1:] if name else ""


def fallback(inp: ChangeIn) -> ChangeOut:
    """Pure ``input -> output``: rule-based patch operations, or a clarification; never guesses a value."""
    r = rules()
    text = tx.squash_spaces(tx.clip(inp.text))
    has = lambda key: tx.has_phrase(text, r[key])  # noqa: E731
    removing, adding = has("remove"), has("add")
    if inp.reason == "dont_want_assumption" or (has("assumption") and removing):
        return _use_variants(inp)
    if (out := _locale_first(inp, text)) is not None:
        return out
    cands = _candidates(text, inp.items)
    if not cands and (has("too_much") or inp.reason == "too_much") and inp.target_kind == "section":
        labels = [i.label for i in inp.items][:MAX_OPTIONS]
        if labels:
            return _no(inp, clarification=_s("which_to_remove", inp.language), options=labels)
    if (
        not cands
        and inp.target_kind in ("row", "slot", "feature", "assumption")
        and len(inp.items) == 1
    ):
        cands = list(inp.items)
    if len(cands) > 1:
        return _ambiguous(inp, [c.label for c in cands])
    levels = [lv for lv in ("later", "must", "nice") if has(f"level_{lv}")]
    if cands:
        item = cands[0]
        if removing and not levels:
            if item.value_kind == "record":
                return _single(_op("remove", item.path, None, item.value_json))
            return _single(_op("clear", item.path, None, item.value_json))
        if len(levels) == 1 and has("move") and not removing:
            ops = _level_ops(item, levels[0])
            if ops:
                return ChangeOut(
                    understood=True, ops=ops, clarification="", options=[], confidence=0.8
                )
        return _no(inp)
    if adding and not removing and SCOPE in inp.add_sections:
        name = _add_name(text)
        if name:
            record = {NAME_FIELD[SCOPE]: name}
            return ChangeOut(
                understood=True,
                ops=[_op("add", SCOPE, record)],
                clarification="",
                options=[],
                confidence=0.6,
            )
    return _no(inp)


def _single(op: OpOut) -> ChangeOut:
    return ChangeOut(understood=True, ops=[op], clarification="", options=[], confidence=0.8)
