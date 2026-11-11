"""The words a use note may open a sentence with (data file ``use_note_words.yaml``); shared by assembly and RV-05."""

from __future__ import annotations

import re
from functools import lru_cache

import yaml

from mde_research_agent.catalog.loader import DATA_DIR


@lru_cache(maxsize=1)
def openers() -> tuple[str, ...]:
    data = yaml.safe_load((DATA_DIR / "use_note_words.yaml").read_text(encoding="utf-8")) or {}
    words = [str(w) for w in data.get("openers", [])]
    # the words of the default notes (data/use_notes.yaml) are code-written and always allowed
    defaults = yaml.safe_load((DATA_DIR / "use_notes.yaml").read_text(encoding="utf-8")) or {}
    for note in defaults.values():
        words += re.findall(r"[A-Za-z]+", str(note))
    return tuple(dict.fromkeys(words))
