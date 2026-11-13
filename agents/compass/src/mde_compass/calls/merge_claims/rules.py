"""Loader for ``rules.yaml`` of the merger (cached, typed)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_compass.calls.read_chunk.textutil import normalize

RULES_PATH = Path(__file__).with_name("rules.yaml")
LANGS = ("en", "hi", "hinglish")


@dataclass(frozen=True)
class MergeRules:
    max_statements: int
    candidate_df_cutoff: int
    min_shared_tokens: int
    min_similarity: float
    low_confidence_below: float
    stopwords: frozenset[str]
    modals: frozenset[str]
    negations: frozenset[str]
    antonyms: dict[str, frozenset[str]]
    tbd_markers: tuple[str, ...]
    slot_hints: dict[str, str | None]


def _flat(block: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for lang in LANGS:
        out.extend(str(x) for x in block.get(lang) or [])
    return out


@lru_cache(maxsize=1)
def load_rules() -> MergeRules:
    raw = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    lim = raw["limits"]
    ants: dict[str, set[str]] = {}
    for a, b in raw["antonyms"]:
        na, nb = normalize(str(a)), normalize(str(b))
        ants.setdefault(na, set()).add(nb)
        ants.setdefault(nb, set()).add(na)
    return MergeRules(
        max_statements=int(lim["max_statements"]),
        candidate_df_cutoff=int(lim["candidate_df_cutoff"]),
        min_shared_tokens=int(lim["min_shared_tokens"]),
        min_similarity=float(lim["min_similarity"]),
        low_confidence_below=float(lim["low_confidence_below"]),
        stopwords=frozenset(normalize(w) for w in _flat(raw["stopwords"])),
        modals=frozenset(normalize(w) for w in _flat(raw["modals"])),
        negations=frozenset(normalize(w) for w in _flat(raw["negations"])),
        antonyms={k: frozenset(v) for k, v in ants.items()},
        tbd_markers=tuple(normalize(m) for m in _flat(raw["tbd_markers"])),
        slot_hints=dict(raw["slot_hints"]),
    )
