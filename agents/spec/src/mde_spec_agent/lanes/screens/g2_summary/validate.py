"""Local checks of the G2 paragraph: one paragraph and no number that the call did not give (never invented). The length is
not a re-ask: `trim_paragraph` cuts it at the last whole sentence within the limit (R-code)."""

from __future__ import annotations

import re

from mde_spec_agent.lanes.screens.g2_summary.schema import G2In, G2Out

_NUM = re.compile(r"\d+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def trim_paragraph(text: str, max_words: int) -> str:
    """At most `max_words` words, ending at a sentence boundary. If the first sentence alone is too long it is cut at the
    word limit and closed with a full stop."""
    text = " ".join(text.split())
    if len(text.split()) <= max_words:
        return text
    kept: list[str] = []
    used = 0
    for s in _SENTENCE.split(text):
        n = len(s.split())
        if used + n > max_words:
            break
        kept.append(s)
        used += n
    if kept:
        return " ".join(kept)
    return " ".join(text.split()[:max_words]).rstrip(",;:") + "."


def validate(inp: G2In, out: G2Out) -> list[str]:
    errors: list[str] = []
    text = out.paragraph.strip()
    if not text:
        return ["the paragraph is empty"]
    if "\n" in text:
        errors.append("write ONE paragraph: no line breaks, lists or headings")
    given = {str(x.value) for x in inp.numbers} | set(_NUM.findall("\n".join(inp.lines)))
    for n in _NUM.findall(text):
        if n not in given:
            errors.append(f"the number {n} is not in the data of this call")
            break
    return errors
