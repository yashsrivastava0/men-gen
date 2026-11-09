"""IntentRouter: deterministic fast paths that run BEFORE any model call.

A typed message that is exactly one of the control phrases (greeting, "you decide", "finish with smart
defaults", stop, pause, start over, back, undo, "show what you know", "none of these") costs no model call.
Phrase tables are data (``phrases.yaml``: English, Hindi, Hinglish); longer free text returns ``None`` and goes
to ``cmp.understand_turn``. Taps (option selections, buttons) never reach this module: they are events.

``language`` is accepted for tie-breaking but the tables are searched in every language: Hinglish and mixed
messages are routine, and a phrase of one language is never a content word of another here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

from mde_compass.engine.language import contains_phrase, normalize

RouteKind = Literal[
    "greeting",
    "you_decide",
    "finish_with_defaults",
    "stop",
    "pause",
    "start_over",
    "none_of_these",
    "back",
    "undo",
    "show_what_you_know",
]
PHRASES_PATH = Path(__file__).resolve().parent / "phrases.yaml"
SHORT_WORDS = (
    6  # a control phrase may carry a few extra words ("finish with smart defaults please")
)
NEEDS_PENDING_CARD: frozenset[str] = frozenset({"you_decide", "none_of_these"})
# Order matters: the most specific / most destructive commands first.
_ORDER: tuple[str, ...] = (
    "start_over",
    "stop",
    "undo",
    "back",
    "pause",
    "finish_with_defaults",
    "show_what_you_know",
    "greeting",
    "you_decide",
    "none_of_these",
)
_EXACT_ONLY: frozenset[str] = frozenset({"greeting", "stop", "pause", "back", "undo", "start_over"})


@dataclass(frozen=True)
class Route:
    kind: RouteKind
    matched: str = ""  # the normalised phrase that matched (for logs and tests)


@dataclass(frozen=True)
class Phrases:
    intents: dict[str, tuple[str, ...]]
    leading: tuple[str, ...]
    trailing: tuple[str, ...]
    approve: tuple[str, ...]
    negative: tuple[str, ...]
    hedge: tuple[str, ...]
    change_cue: tuple[str, ...]


def _norm_all(items: list[object]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        n = normalize(str(item))
        if n:
            seen[n] = None
    return tuple(seen)


@lru_cache(maxsize=1)
def load_phrases() -> Phrases:
    """Load ``phrases.yaml`` once (normalised, de-duplicated)."""
    raw = yaml.safe_load(PHRASES_PATH.read_text(encoding="utf-8"))
    return Phrases(
        intents={k: _norm_all(v) for k, v in raw["intents"].items()},
        leading=_norm_all(raw["fillers"]["leading"]),
        trailing=_norm_all(raw["fillers"]["trailing"]),
        approve=_norm_all(raw["approve"]),
        negative=_norm_all(raw["negative"]),
        hedge=_norm_all(raw["hedge"]),
        change_cue=_norm_all(raw["change_cue"]),
    )


def strip_fillers(norm: str) -> str:
    """Drop polite leading and trailing fillers ("please", "kindly", "yaar") from a normalised message."""
    ph = load_phrases()
    changed = True
    while changed and norm:
        changed = False
        for filler in ph.leading:
            if norm.startswith(filler + " "):
                norm = norm[len(filler) + 1 :]
                changed = True
        for filler in ph.trailing:
            if norm.endswith(" " + filler):
                norm = norm[: -len(filler) - 1]
                changed = True
    return norm


def is_exact(norm: str, phrases: tuple[str, ...]) -> str | None:
    """The phrase equal to the whole message (fillers stripped), or None."""
    core = strip_fillers(norm)
    for phrase in phrases:
        if core == phrase or norm == phrase:
            return phrase
    return None


def find_phrase(norm: str, phrases: tuple[str, ...], *, min_words: int = 1) -> str | None:
    """The first multi-word-enough phrase that occurs in ``norm`` as whole words, or None."""
    for phrase in phrases:
        if len(phrase.split()) >= min_words and contains_phrase(norm, phrase):
            return phrase
    return None


HelpKind = Literal["explain", "dont_know"]


def route_help(text: str) -> HelpKind | None:
    """ "I do not understand" / "I do not know" about the question on screen. Used only by the planned interview
    (the legacy router never sees these kinds), so the older behaviour and its tests are untouched."""
    norm = normalize(text[:500])
    if not norm or len(norm.split()) > 8:
        return None
    ph = load_phrases()
    for kind in ("explain", "dont_know"):
        phrases = ph.intents[kind]
        if (
            is_exact(norm, phrases) is not None
            or find_phrase(norm, phrases, min_words=2) is not None
        ):
            return kind  # type: ignore[return-value]
    return None


def route(text: str, *, has_pending_card: bool, language: str = "en") -> Route | None:
    """The fast path for ``text``, or ``None`` when it needs the understander."""
    del language  # tables cover every language; see the module docstring
    norm = normalize(text[:2_000])
    if not norm:
        return None
    words = len(norm.split())
    ph = load_phrases()
    for kind in _ORDER:
        if kind in NEEDS_PENDING_CARD and not has_pending_card:
            continue
        phrases = ph.intents[kind]
        hit = is_exact(norm, phrases)
        if hit is not None:
            return Route(kind, hit)  # type: ignore[arg-type]
        if kind in _EXACT_ONLY:
            continue
        limit = SHORT_WORDS if kind in ("finish_with_defaults", "show_what_you_know") else 5
        if words <= limit and (hit := find_phrase(norm, phrases, min_words=2)) is not None:
            return Route(kind, hit)  # type: ignore[arg-type]
    return None
