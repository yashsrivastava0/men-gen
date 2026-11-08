"""Template card writer: the offline brain of C5 (mock handler and engine fallback).

Builds a generic card from the slot's own options (``options_hint``): one plain question, at most
three options for a choice card with the first one recommended, every option for a multi card, no
options for a text card. No LLM, no invention: it only rephrases what it is given.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from mde_contracts.calls import C5Input, C5Output, OptionDraft
from mde_prompts.calls.c1_understand import lexicon as lx


@lru_cache(maxsize=1)
def _templates() -> dict[str, dict[str, str]]:
    text = (
        resources.files("mde_prompts.calls.c5_write_card")
        .joinpath("templates.yaml")
        .read_text(encoding="utf-8")
    )
    data: dict[str, dict[str, str]] = yaml.safe_load(text)
    return data


def _t(key: str, lang: str | None) -> str:
    return str(lx.localized(_templates()[key], lang))


def humanize(value: str) -> str:
    """``email_phone_code`` -> ``Email phone code``; text that is already a phrase is kept."""
    if " " in value:
        return value
    spaced = value.replace("_", " ").replace("-", " ").strip()
    return spaced[:1].upper() + spaced[1:]


def option_value(hint: str, slot_kind: str | None = None) -> dict[str, Any]:
    low = hint.strip().casefold()
    if slot_kind == "bool" or low in ("true", "false"):
        return {"value_bool": low == "true"}
    return {"value_text": hint}


def write_card(inp: C5Input) -> C5Output:
    """A card for ``inp.slot`` built only from ``inp.options_hint``."""
    lang = inp.language
    topic = lx.slot_label(inp.slot, lang)
    hints = [h for h in inp.options_hint if h.strip()]
    help_text = inp.slot_hint or _t("help", lang)
    if inp.card_type in ("text", "upload") or not hints:
        return C5Output(
            text=_t("text", lang).format(topic=topic),
            options=[],
            other_placeholder=_t("text_placeholder", lang),
            help=help_text,
        )
    multi = inp.card_type == "multi"
    chosen = hints if multi else hints[:3]
    options: list[OptionDraft] = []
    for i, hint in enumerate(chosen):
        if multi:
            options.append(
                OptionDraft(label=humanize(hint), value_list=[hint], recommended=(i == 0))
            )
        else:
            options.append(
                OptionDraft(label=humanize(hint), recommended=(i == 0), **option_value(hint))
            )
    key = "multi" if multi else "choice"
    return C5Output(
        text=_t(key, lang).format(topic=topic),
        options=options,
        other_placeholder=_t("other_placeholder", lang),
        help=help_text,
    )
