"""Loader for ``flows.yaml`` (cue phrases and policy numbers of the targeted flows) and phrase matching.

Matching is linear: the text is clipped, normalised once, and each phrase is a whole-word literal (no
nested quantifiers anywhere), the same approach as the router.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_compass.engine.language import contains_phrase, normalize

DATA_PATH = Path(__file__).with_name("flows.yaml")
SCAN_LIMIT = 20_000  # characters of a message the cue rules look at


@lru_cache(maxsize=1)
def load_flows() -> dict[str, Any]:
    return yaml.safe_load(DATA_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def phrases(*path: str) -> tuple[str, ...]:
    """The normalised phrases at ``path`` in flows.yaml (a list, or a dict of language lists flattened)."""
    node: Any = load_flows()
    for key in path:
        node = node[key]
    raw: list[str] = (
        [str(p) for v in node.values() for p in v]
        if isinstance(node, dict)
        else [str(p) for p in node]
    )
    return tuple(dict.fromkeys(normalize(p) for p in raw if normalize(p)))


def find_phrase(text: str, options: tuple[str, ...]) -> str | None:
    """The first (longest first) phrase of ``options`` that occurs in ``text`` as whole words."""
    norm = normalize(text[:SCAN_LIMIT])
    for phrase in sorted(options, key=len, reverse=True):
        if contains_phrase(norm, phrase):
            return phrase
    return None


@dataclass(frozen=True)
class Policy:
    """Numbers of the flows, from flows.yaml."""

    first_release_max: int
    min_features: int
    max_options: int
    max_named_references: int
    readback_min: int
    split_min_words: int
    max_statements: int
    max_statement_words: int
    apps_phase_at: int
    apps_main_now: int
    apps_max_listed_later: int
    locked_max_consumers: int
    backedge_max_variants: int


@lru_cache(maxsize=1)
def policy() -> Policy:
    raw = load_flows()
    return Policy(
        first_release_max=int(raw["priorities"]["first_release_max"]),
        min_features=int(raw["priorities"]["min_features"]),
        max_options=int(raw["priorities"]["max_options"]),
        max_named_references=int(raw["references"]["max_named"]),
        readback_min=int(raw["voice"]["min_statements_for_readback"]),
        split_min_words=int(raw["voice"]["min_words_to_split"]),
        max_statements=int(raw["voice"]["max_statements"]),
        max_statement_words=int(raw["voice"]["max_statement_words"]),
        apps_phase_at=int(raw["apps"]["phase_at"]),
        apps_main_now=int(raw["apps"]["main_now"]),
        apps_max_listed_later=int(raw["apps"]["max_listed_later"]),
        locked_max_consumers=int(raw["locked"]["max_preview_consumers"]),
        backedge_max_variants=int(raw["backedge"]["max_variants"]),
    )


def number_word(n: int, language: str) -> str:
    """ "five" / "पाँच" for ``n`` when flows.yaml has it, else the digits."""
    words: dict[Any, Any] = load_flows()["priorities"]["number_words"]
    return str(words.get(language, words["en"]).get(n) or words["en"].get(n) or n)
