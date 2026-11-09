"""Code validators of ``cmp.answer``: no invented price, time or capability; honest grounding; language.

The regexes are linear (bounded gaps, no nested quantifiers) and run on clipped text.
"""

from __future__ import annotations

import re

from mde_compass.calls.answer import text as tx
from mde_compass.calls.answer.fallback import rules
from mde_compass.calls.answer.schema import MAX_MODEL_CHARS, AnswerInput, AnswerOutput

_MONEY = re.compile(
    r"[$€£₹]|"
    r"\b\d[\d,.]{0,15}\s{0,2}(?:usd|inr|eur|gbp|rs|rupees?|dollars?|euros?|lakhs?|crores?|k)\b|"
    r"\b(?:rs|inr|usd)\.?\s{0,2}\d|"
    r"रुपय[ेा]|रुपए|\b(?:rupaye|rupay|paise)\b",
    re.IGNORECASE,
)
_DURATION = re.compile(
    r"\b(?:minutes?|hours?|days?|weeks?|months?|years?|quarters?|tomorrow|overnight|asap)\b|"
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|"
    r"\b(?:din|hafte|hafta|mahine|mahina|saal|ghante)\b|"
    r"दिन|हफ़्त|हफ्त|सप्ताह|महीन|घंटे|साल",
    re.IGNORECASE,
)
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_FACT_KEYS = ("fallback.", "pending_card")


def risky_tokens(text: str) -> set[str]:
    """Money, duration and date tokens in ``text`` (lower-cased)."""
    body = tx.squash_spaces(tx.clip(text))
    found = {m.group(0).casefold() for m in _MONEY.finditer(body)}
    found |= {m.group(0).casefold() for m in _DURATION.finditer(body)}
    return found


def allowed_text(inp: AnswerInput) -> str:
    """The input text that may legitimise a number or a promise: slots, facts, the pending card."""
    parts = [s.value for s in inp.slots] + inp.facts + inp.pending_options
    parts += [p for p in (inp.pending_question, inp.pending_why) if p]
    return " ".join(parts)


def check_answer(inp: AnswerInput, out: AnswerOutput) -> list[str]:
    """Errors that make the model output unusable; an empty list means it may be shown."""
    errors: list[str] = []
    text = out.text.strip()
    if not text:
        return ["empty text"]
    if len(text) > MAX_MODEL_CHARS:
        errors.append(f"text longer than {MAX_MODEL_CHARS} characters")
    allowed = allowed_text(inp)
    extra = risky_tokens(text) - risky_tokens(allowed)
    if extra:
        errors.append(f"invents a price, time or date: {sorted(extra)[:3]}")
    promises = [p for p in tx.hits(text, rules()["promise"]) if not tx.has_phrase(allowed, (p,))]
    if promises:
        errors.append(f"promises a capability: {promises[:3]}")
    deva = len(_DEVANAGARI.findall(text))
    if inp.language == "hi" and deva == 0:
        errors.append("language mismatch: expected Hindi script")
    if inp.language != "hi" and deva * 2 > sum(ch.isalpha() for ch in text):
        errors.append("language mismatch: unexpected Devanagari")
    known = {s.slot_id for s in inp.slots}
    unknown = [g for g in out.grounded_in if g not in known and not g.startswith(_FACT_KEYS)]
    if unknown:
        errors.append(f"grounded_in names unknown ids: {unknown[:3]}")
    if inp.topic in ("price_or_time", "tech_choice", "unsafe") and not out.declined:
        errors.append(f"must decline a {inp.topic} message")
    return errors
