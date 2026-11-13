"""Structural checks on the rephrased question. The call may only change the wording: the number and order of
the options is fixed, the question must still be a short question and it must actually differ from the one the
person did not understand. Anything else is refused so the gateway's one repair, or the flagged fallback
(the original question, unchanged), takes over."""

from __future__ import annotations

import re

from mde_compass.calls.rephrase_question.schema import RephraseInput, RephraseOutput

MAX_QUESTION_WORDS = 30  # the prompt asks for about 20; this is the hard line
MAX_LABEL_WORDS = 12  # the prompt asks for at most 8
_WORD = re.compile(r"[\wऀ-ॿ]+")  # word characters plus the Devanagari block (marks included)


def plain(text: str) -> str:
    """Case-folded words only, so punctuation and spacing do not make a question look new."""
    return " ".join(_WORD.findall(text.casefold()))


def structural(inp: RephraseInput, out: RephraseOutput) -> list[str]:
    errors: list[str] = []
    words = len(out.question.split())
    if not out.question.strip():
        errors.append("empty question")
    elif words > MAX_QUESTION_WORDS:
        errors.append(f"question has {words} words (at most {MAX_QUESTION_WORDS})")
    elif plain(out.question) == plain(inp.question):
        errors.append(f"the question is unchanged (mode {inp.mode})")
    if len(out.options) != len(inp.options):
        errors.append(f"option count changed ({len(out.options)} instead of {len(inp.options)})")
    seen: set[str] = set()
    for i, o in enumerate(out.options, start=1):
        if not o.label.strip():
            errors.append(f"option {i}: empty label")
            continue
        if len(o.label.split()) > MAX_LABEL_WORDS:
            errors.append(f"option {i}: label has more than {MAX_LABEL_WORDS} words")
        key = plain(o.label)
        if key in seen:
            errors.append(f"option {i}: same label as another option")
        seen.add(key)
    return errors
