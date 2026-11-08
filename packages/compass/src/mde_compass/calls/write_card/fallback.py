"""Deterministic card writer: the mock handler and the fallback of ``cmp.write_card``.

Built from the catalog (the frame's slots and their bilingual ``fallback_card``) and the typical items
(``hints``) the caller passes in; it satisfies every rule of ``validate.validate_card`` for every frame in
English and Hindi (``hi-Latn`` uses English wording: the catalog has en and hi only). Nothing is invented:
labels are the catalog's or the hints', every option says honestly where it comes from.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.write_card.schema import OptionWire, WriteCardInput, WriteCardOutput
from mde_compass.calls.write_card.validate import script_ok
from mde_compass.catalog.loader import Catalog, SlotDef, load_catalog

_TEXTS = Path(__file__).with_name("texts.yaml")
IDS = "ABCDEFGHIJKL"
MAX_CHECKLIST_HINTS = 8


@lru_cache(maxsize=1)
def _texts() -> dict[str, dict[str, str]]:
    return yaml.safe_load(_TEXTS.read_text(encoding="utf-8"))


def _t(key: str, lang: str) -> str:
    table = _texts()[key]
    return table.get("hi" if lang == "hi" else "en", table["en"])


def _lang(language: str) -> str:
    return "hi" if language == "hi" else "en"


def value_for(slot: SlotDef, label: str) -> Any | None:
    """The slot value that picking ``label`` sets, or None when the label does not fit the slot."""
    kind, options, fields = (
        slot.value_schema.kind,
        slot.value_schema.options,
        slot.value_schema.fields,
    )
    if kind == "text":
        return label
    if kind == "enum":
        return label if label in options else None
    if kind == "enum_list":
        return [label] if label in options else None
    if kind == "text_list":
        return [label]
    if kind == "object_list":
        return [{("name" if "name" in fields else fields[0]): label}] if fields else None
    return None


def _trim_words(text: str, limit: int) -> str:
    parts = text.split()
    return text if len(parts) <= limit else " ".join(parts[:limit])


def _primary(inp: WriteCardInput, catalog: Catalog) -> SlotDef | None:
    """The first slot of the frame whose fallback card has options (else the first slot)."""
    defs = [catalog.slots[s.slot_id] for s in inp.slots if s.slot_id in catalog.slots]
    for d in defs:
        if d.fallback_card.options:
            return d
    return defs[0] if defs else None


def _hint_options(inp: WriteCardInput, slot: SlotDef, want: int | None) -> list[OptionWire]:
    ev = lx.normalize(" ".join(inp.evidence))
    options: list[OptionWire] = []
    seen: set[str] = set()
    for hint in inp.hints:
        label = _trim_words(hint.strip(), inp.frame.max_option_words)
        key = lx.normalize(label)
        value = value_for(slot, label)
        if not key or key in seen or value is None or not script_ok(label, inp.language):
            continue
        if key in {lx.normalize(a) for a in inp.avoid_labels}:
            continue
        seen.add(key)
        said = bool(key) and key in ev
        options.append(
            OptionWire(
                id=IDS[len(options)],
                label=label,
                basis="your_words" if said else "typical",
                recommended=False,
                reason="",
                sets_json=json.dumps({slot.id: value}, ensure_ascii=False),
                ticked=said and want is None,
            )
        )
        if len(options) >= (want or MAX_CHECKLIST_HINTS):
            break
    return options


def _card_options(inp: WriteCardInput, slot: SlotDef, lang: str) -> list[OptionWire]:
    out: list[OptionWire] = []
    avoid = {lx.normalize(a) for a in inp.avoid_labels}
    for o in slot.fallback_card.options:
        label = o.label.get(lang)
        if lx.normalize(label) in avoid:
            continue
        sets: dict[str, Any] = {slot.id: o.value} if o.value is not None else {}
        if o.value is not None and slot.value_schema.kind in ("enum_list", "text_list"):
            sets = {slot.id: [o.value]}
        out.append(
            OptionWire(
                id=o.id if o.id and o.id not in {x.id for x in out} else IDS[len(out)],
                label=_trim_words(label, inp.frame.max_option_words),
                basis="suggestion",
                recommended=False,
                reason="",
                sets_json=json.dumps(sets, ensure_ascii=False),
                ticked=False,
            )
        )
    return out


def _finish(options: list[OptionWire], lang: str, *, recommend: bool) -> list[OptionWire]:
    """Exactly one recommended option with a reason (the first), when there are options."""
    if not options or not recommend:
        return options
    first = options[0].model_copy(
        update={"recommended": True, "reason": _t("recommended_reason", lang)}
    )
    return [first, *options[1:]]


def fallback(inp: WriteCardInput, catalog: Catalog | None = None) -> WriteCardOutput:
    """The deterministic card for ``inp.frame`` in the user's language (see the module docstring)."""
    cat = catalog or load_catalog()
    lang = _lang(inp.language)
    slot = _primary(inp, cat)
    ctype = inp.frame.card_type
    if slot is None:
        return WriteCardOutput(
            question=_t("checklist_question", lang),
            why_we_ask=_t("checklist_why", lang),
            options=[],
            placeholder=_t("open_placeholder", lang),
        )
    card = slot.fallback_card
    question = _trim_words(card.question.get(lang), inp.frame.max_question_words)
    why = card.why_we_ask.get(lang)
    placeholder = card.placeholder.get(lang) if card.placeholder else _t("open_placeholder", lang)
    if ctype in ("open_prompt", "quick_form", "upload_or_link"):
        return WriteCardOutput(
            question=question, why_we_ask=why, options=[], placeholder=placeholder
        )
    want = inp.frame.option_count if ctype in ("choose_one", "look_signals") else None
    hinted = _hint_options(inp, slot, want) if inp.hints else []
    options = hinted if (hinted and (want is None or len(hinted) >= want)) else []
    if not options:
        options = _card_options(inp, slot, lang)
    if want is not None:
        options = options[:want]
    elif ctype == "pick_many" and len(options) < 2 and inp.hints:
        options = _card_options(inp, slot, lang)
    return WriteCardOutput(
        question=question,
        why_we_ask=why,
        options=_finish(options, lang, recommend=bool(options)),
        placeholder=placeholder if ctype != "choose_one" else "",
    )
