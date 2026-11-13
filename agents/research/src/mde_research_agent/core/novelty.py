"""RV-05 as code: does a text add a number, currency, date, name or URL that its sources do not contain?

Synthesis (``use_note`` sentences, digests) may select and annotate but never write new facts. ``novel_tokens``
returns what is NEW in ``text`` compared with the ``sources``:

* URLs and e-mail addresses unless a source contains them;
* figures (numbers, amounts, percentages, dates) compared as FIGURES, so ``Rs 5,000`` in a note is not new next to
  ``5000`` in the item, but ``2`` is new next to ``12`` (a digit anywhere in the sources no longer lets a number
  through);
* names: a capitalised word anywhere in the text, the FIRST word included, that no source contains (compared without
  regard to case). A capitalised first word passes when it is an ordinary stop word or one of the generic opener
  words (``allowed``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from mde_research_agent.core.numbers import figures, missing_figures
from mde_research_agent.core.text import STOP_WORDS, tokens

_URL = re.compile(r"(https?://\S+|www\.\S+|\S+@\S+\.\S+)", re.I)
_ID_LIKE = re.compile(r"^[A-Z]{1,4}-\d+$")
_CAPITALISED = re.compile(r"\b([A-Z][\w-]{2,})")


def novel_tokens(text: str, sources: Iterable[str], allowed: Iterable[str] = ()) -> list[str]:
    haystack = " ".join(sources)
    known = set(tokens(haystack))
    okay = {w.casefold() for w in allowed} | {w.casefold() for w in STOP_WORDS}
    found: list[str] = []
    for m in _URL.finditer(text):
        if m.group(0).casefold() not in haystack.casefold():
            found.append(m.group(0))
    bare = _URL.sub(" ", text)
    for f in missing_figures(figures(bare), figures(haystack)):
        found.append(f.raw)
    for m in _CAPITALISED.finditer(bare):
        word = m.group(1)
        if _ID_LIKE.match(word):
            continue  # an item id such as RI-061 is a reference, not a name
        low = word.casefold()
        if low in known or low in okay:
            continue
        found.append(word)
    return found
