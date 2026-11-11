"""RV-05 as code: does a text add a number, currency, date, name or URL that its sources do not contain?

Synthesis (``use_note`` sentences, digests) may select and annotate but never write new facts. ``novel_tokens``
returns what is NEW in ``text`` compared with the ``sources``: digit groups, URLs and e-mail addresses always
count as new unless the sources contain them; capitalised words in the middle of a sentence (names) count when no
source contains them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from mde_research_agent.core.text import tokens

_URL = re.compile(r"(https?://\S+|www\.\S+|\S+@\S+\.\S+)", re.I)
_NUMBER = re.compile(r"\d[\d,.]*")
_ID_LIKE = re.compile(r"^[A-Z]{1,4}-\d+$")
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][\w-]{2,})")


def novel_tokens(text: str, sources: Iterable[str]) -> list[str]:
    haystack = " ".join(sources)
    known = set(tokens(haystack))
    found: list[str] = []
    for m in _URL.finditer(text):
        if m.group(0).casefold() not in haystack.casefold():
            found.append(m.group(0))
    for m in _NUMBER.finditer(_URL.sub(" ", text)):
        num = m.group(0).rstrip(".,")
        if num and num not in haystack:
            found.append(num)
    for m in _NAME.finditer(_URL.sub(" ", text)):
        word = m.group(1)
        if _ID_LIKE.match(word):
            continue  # an item id such as RI-061 is a reference, not a name
        if word.casefold() not in known:
            found.append(word)
    return found
