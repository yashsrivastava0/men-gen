"""Local checks of the G2 paragraph: one paragraph, not long, and no number that the call did not give (never invented)."""

from __future__ import annotations

import re

from mde_spec_agent.lanes.screens.g2_summary.schema import G2In, G2Out

_NUM = re.compile(r"\d+")
MAX_WORDS = 110


def validate(inp: G2In, out: G2Out) -> list[str]:
    errors: list[str] = []
    text = out.paragraph.strip()
    if not text:
        return ["the paragraph is empty"]
    if "\n" in text:
        errors.append("write ONE paragraph: no line breaks, lists or headings")
    if len(text.split()) > MAX_WORDS:
        errors.append(f"the paragraph is longer than {MAX_WORDS} words")
    given = {str(x.value) for x in inp.numbers} | set(_NUM.findall("\n".join(inp.lines)))
    for n in _NUM.findall(text):
        if n not in given:
            errors.append(f"the number {n} is not in the data of this call")
            break
    return errors
