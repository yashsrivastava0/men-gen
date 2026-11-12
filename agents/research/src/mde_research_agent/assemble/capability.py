"""Which integration need (a key of ``data/need_keywords.yaml``) a provider fact is about. Pure code.

The model writes a provider fact's ``need`` as free text ("online payments", "SMS"). Code maps it to the need key the Brief
index and ``by_integration`` use, by the words of ``need_keywords.yaml`` (stems, so plural and singular are one word). The
best overlap wins, a tie goes to the need listed first; no overlap is ``None`` (the fact keeps its own text and gets no
integration key).
"""

from __future__ import annotations

from mde_research_agent.catalog.models import NeedsConfig
from mde_research_agent.core.text import stem_set


def need_key_of(text: str, needs: NeedsConfig) -> str | None:
    mine = stem_set(text)
    if not mine:
        return None
    best: str | None = None
    best_hits = 0
    for key, need in needs.needs.items():
        words = {w for word in need.words for w in stem_set(word)} | stem_set(key.replace("_", " "))
        hits = len(mine & words)
        if hits > best_hits:
            best, best_hits = key, hits
    return best
