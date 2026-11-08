"""Deterministic fallback of ``cmp.compile_text``: template sentences filled only from slot values.

It never invents: every word that is not a template word comes from a slot value, label or item name.
"""

from __future__ import annotations

from mde_compass.calls.compile_text.schema import (
    MAX_DESCRIPTION_CHARS,
    MAX_PITCH_CHARS,
    MAX_SUMMARY_CHARS,
    CompileTextInput,
    CompileTextOutput,
    ItemText,
)
from mde_compass.calls.compile_text.text_utils import clip, load_templates, template_lang

PART_CHARS = 60
MAX_LISTED_FEATURES = 5


def _slot(inp: CompileTextInput, slot_id: str) -> str:
    for s in inp.slots:
        if s.slot_id == slot_id and s.state != "empty" and s.value.strip():
            return " ".join(s.value.split())
    return ""


def _join(parts: list[str], table: dict[str, object]) -> str:
    sep, last = str(table["list_sep"]), str(table["list_last"])
    if len(parts) <= 1:
        return "".join(parts)
    return sep.join(parts[:-1]) + last + parts[-1]


def _summary(inp: CompileTextInput, table: dict[str, object]) -> str:
    idea = _slot(inp, "idea.summary")
    if idea:
        return clip(idea, MAX_SUMMARY_CHARS)
    category = _slot(inp, "idea.subcategory") or _slot(inp, "idea.category")
    if not category:
        return str(table["summary_empty"])
    text = str(table["summary_category"]).format(category=clip(category, PART_CHARS))
    audience = _slot(inp, "audience.segments")
    if audience:
        text += str(table["summary_audience"]).format(audience=clip(audience, PART_CHARS))
    geo = _slot(inp, "context.geo")
    if geo:
        text += str(table["summary_geo"]).format(geo=clip(geo, PART_CHARS))
    return clip(text, MAX_SUMMARY_CHARS)


def _feature_names(inp: CompileTextInput) -> list[str]:
    names = [clip(i.name, PART_CHARS) for i in inp.items if i.path.startswith("scope.") and i.name]
    if names:
        return names[:MAX_LISTED_FEATURES]
    listed = _slot(inp, "scope.features")
    return [clip(listed, 2 * PART_CHARS)] if listed else []


def _pitch(inp: CompileTextInput, summary: str, table: dict[str, object]) -> str:
    sentences: list[str] = []
    audience, geo = _slot(inp, "audience.segments"), _slot(inp, "context.geo")
    model = _slot(inp, "idea.business_model")
    features = _feature_names(inp)
    if audience:
        sentences.append(str(table["pitch_audience"]).format(audience=clip(audience, PART_CHARS)))
    if geo:
        sentences.append(str(table["pitch_geo"]).format(geo=clip(geo, PART_CHARS)))
    if features:
        sentences.append(str(table["pitch_features"]).format(features=_join(features, table)))
    if model:
        sentences.append(str(table["pitch_model"]).format(business_model=clip(model, PART_CHARS)))
    first = summary if summary.endswith((".", "!", "?", "।")) else summary + "."
    pitch = first
    for sentence in sentences:
        if len(pitch) + 1 + len(sentence) > MAX_PITCH_CHARS:
            break
        pitch += " " + sentence
    return clip(pitch, MAX_PITCH_CHARS)


def _description(name: str, intent: str, table: dict[str, object]) -> str:
    key = "description_with_intent" if intent.strip() else "description_plain"
    text = str(table[key]).format(name=clip(name, PART_CHARS), intent=clip(intent, 160))
    return clip(text, MAX_DESCRIPTION_CHARS)


def fallback(inp: CompileTextInput) -> CompileTextOutput:
    """Pure: the same input always gives the same texts."""
    table = load_templates()[template_lang(inp.language)]
    summary = _summary(inp, table)
    descriptions = [
        ItemText(item_id=i.path, text=_description(i.name, i.intent, table))
        for i in inp.items
        if i.name.strip()
    ]
    return CompileTextOutput(
        summary=summary, pitch=_pitch(inp, summary, table), descriptions=descriptions
    )
