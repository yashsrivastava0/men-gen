"""Which language is a page in? A best effort from its script plus the languages we searched in (never a guess dressed as fact).

``source_language`` is recorded per page (RS-32): when the dominant script of the text belongs to one of the languages
searched, that language's code; Latin script is ``en`` unless a searched Latin-script language fits; otherwise the script
name. Used for evidence and for the English-fallback report (RS-15).
"""

from __future__ import annotations

import unicodedata
from collections import Counter

from mde_research_agent.catalog.models import LanguageInfo


def dominant_script(text: str, sample: int = 4000) -> str:
    counts: Counter[str] = Counter()
    for ch in text[:sample]:
        if ch.isalpha():
            try:
                counts[unicodedata.name(ch).split()[0]] += 1
            except ValueError:
                continue
    return counts.most_common(1)[0][0].title() if counts else "Unknown"


def guess_language(text: str, searched: list[str], languages: dict[str, LanguageInfo]) -> str:
    script = dominant_script(text)
    for code in searched:
        info = languages.get(code.split("-")[0])
        if info and info.script.casefold() == script.casefold():
            return code.split("-")[0]
    if script.casefold() == "latin":
        return "en"
    return script.lower()
