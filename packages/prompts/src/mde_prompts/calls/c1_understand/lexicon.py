"""Phrase tables and small text helpers shared by the Discover mock handlers and the IntentRouter.

The data is ``lexicon.yaml`` (English, Hindi and Hinglish). Everything here is deterministic and
offline. The helpers are pure functions over a cached, immutable :class:`Lexicon`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

_PUNCT = re.compile(r"[^\w\sऀ-ॿ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
_APOSTROPHES = re.compile(r"['’‘`]")

# Every regex that sees user, upload or model text must be linear in the length of that text (a
# quadratic one freezes the shared event loop: one request stalls every tenant). The rules for the
# patterns of this package: no two adjacent unbounded quantifiers over overlapping classes, no
# unbounded quantifier tried from every start of a run (anchor the start with a lookbehind or bound
# the repeat), whitespace gaps bounded. ``squash_spaces`` and ``clip`` are applied before matching.
MAX_SCAN_CHARS = 20_000  # the most text a rule-based (mock or fallback) handler looks at


def squash_spaces(text: str) -> str:
    """Every run of whitespace becomes one space (one linear pass, nothing else changes)."""
    return _SPACES.sub(" ", text)


def clip(text: str, limit: int = MAX_SCAN_CHARS) -> str:
    """At most ``limit`` characters (the rest is not looked at by rule-based handlers)."""
    return text if len(text) <= limit else text[:limit]


def _normalize(text: str) -> str:
    out = unicodedata.normalize("NFC", text).casefold()
    out = _APOSTROPHES.sub("", out)
    out = _PUNCT.sub(" ", out)
    return _SPACES.sub(" ", out).strip()


@lru_cache(maxsize=8)
def _normalize_long(text: str) -> str:
    return _normalize(text)


def normalize(text: str) -> str:
    """NFC, casefold, drop apostrophes, turn other punctuation into spaces, collapse spaces.

    A handler normalises the same long message or upload dozens of times (once per keyword list):
    texts of a page or more are memoised (the last few only).
    """
    return _normalize_long(text) if len(text) >= 2048 else _normalize(text)


def words(text: str) -> list[str]:
    return normalize(text).split()


@lru_cache(maxsize=4096)
def _word_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(normalize(phrase))}(?!\w)")


@lru_cache(maxsize=4096)
def _phrase_norm(phrase: str) -> str:
    return normalize(phrase)


def _occurs(hay: str, phrase: str) -> bool:
    """The normalised ``phrase`` occurs in ``hay`` as whole words.

    A plain substring test first (C speed): most phrases are absent, and the regex (a lookbehind
    pattern, tried from every position) is only run for the few that are present.
    """
    return phrase in hay and _word_pattern(phrase).search(hay) is not None


def keyword_hits(text: str, keywords: list[str] | tuple[str, ...]) -> list[str]:
    """Keywords that occur in ``text`` as whole words (case-insensitive, any language)."""
    hay = normalize(text)
    return [k for k in keywords if (n := _phrase_norm(k)) and _occurs(hay, n)]


def has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    """True when any normalised phrase occurs as whole words in the text."""
    hay = normalize(text)
    return any(_occurs(hay, _phrase_norm(p)) for p in phrases)


@dataclass(frozen=True)
class Lexicon:
    intents: dict[str, tuple[str, ...]]
    leading: tuple[str, ...]
    trailing: tuple[str, ...]
    hinglish: frozenset[str]
    language_markers: dict[str, frozenset[str]]
    scripts: tuple[tuple[str, str, str], ...]
    unsafe: dict[str, tuple[str, ...]]
    injection: tuple[re.Pattern[str], ...]
    slot_labels: dict[str, dict[str, str]]
    feature_keywords: dict[str, tuple[str, ...]]
    theme_keywords: dict[str, tuple[str, ...]]
    color_words: tuple[str, ...]
    knowledge_topics: dict[str, tuple[str, ...]]
    raw: dict[str, Any]


def norm_all(items: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        n = normalize(str(item))
        if n:
            seen[n] = None
    return tuple(seen)


@lru_cache(maxsize=1)
def load_lexicon() -> Lexicon:
    text = (
        resources.files("mde_prompts.calls.c1_understand")
        .joinpath("lexicon.yaml")
        .read_text(encoding="utf-8")
    )
    raw: dict[str, Any] = yaml.safe_load(text)
    return Lexicon(
        intents={k: norm_all(v) for k, v in raw["intents"].items()},
        leading=norm_all(raw["fillers"]["leading"]),
        trailing=norm_all(raw["fillers"]["trailing"]),
        hinglish=frozenset(norm_all(raw["hinglish_markers"])),
        language_markers={k: frozenset(norm_all(v)) for k, v in raw["language_markers"].items()},
        scripts=tuple((a, b, c) for a, b, c in raw["scripts"]),
        unsafe={k: norm_all(v) for k, v in raw["safety"]["unsafe"].items()},
        injection=tuple(re.compile(p, re.IGNORECASE) for p in raw["safety"]["injection"]),
        slot_labels=raw["slot_labels"],
        feature_keywords={k: norm_all(v) for k, v in raw["feature_keywords"].items()},
        theme_keywords={k: norm_all(v) for k, v in raw["theme_keywords"].items()},
        color_words=norm_all(raw["color_words"]),
        knowledge_topics={k: norm_all(v) for k, v in raw["knowledge_topics"].items()},
        raw=raw,
    )


# --------------------------------------------------------------------------- phrase matching


def strip_fillers(norm: str) -> str:
    """Trim polite fillers from both ends of a normalised message (never down to nothing)."""
    lex = load_lexicon()
    cur = norm
    changed = True
    while changed:
        changed = False
        for f in lex.leading:
            if cur.startswith(f + " ") and cur[len(f) + 1 :].strip():
                cur = cur[len(f) + 1 :].strip()
                changed = True
        for f in lex.trailing:
            if cur.endswith(" " + f) and cur[: -(len(f) + 1)].strip():
                cur = cur[: -(len(f) + 1)].strip()
                changed = True
    return cur


def is_phrase(text: str, intent: str) -> bool:
    """The whole message is one of the intent's phrases (polite fillers ignored)."""
    norm = normalize(text)
    if not norm:
        return False
    phrases = load_lexicon().intents[intent]
    return norm in phrases or strip_fillers(norm) in phrases


def contains_phrase(text: str, intent: str, *, min_words: int = 1) -> bool:
    """A phrase of the intent occurs as whole words; ``min_words`` skips one-word phrases."""
    phrases = tuple(p for p in load_lexicon().intents[intent] if len(p.split()) >= min_words)
    return has_phrase(text, phrases)


def phrase_ends_message(text: str, intent: str) -> bool:
    """A phrase of the intent starts or ends the message (after fillers): 'ok, build it now'."""
    norm = strip_fillers(normalize(text))
    for p in load_lexicon().intents[intent]:
        if norm == p or norm.startswith(p + " ") or norm.endswith(" " + p):
            return True
    return False


# --------------------------------------------------------------------------- language


def detect_language(text: str, previous: str | None = None) -> str:
    """Best-effort language tag: ``hi`` (Devanagari), ``hi-Latn`` (Hinglish), ``en`` and a few more.

    Short, language-neutral messages ("ok") keep ``previous``.
    """
    lex = load_lexicon()
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return previous or "en"
    counts: dict[str, int] = {}
    for c in letters:
        for lo, hi, lang in lex.scripts:
            if lo <= c <= hi:
                counts[lang] = counts.get(lang, 0) + 1
    if counts:
        lang, n = max(counts.items(), key=lambda kv: kv[1])
        if n / len(letters) >= 0.3:
            return lang
    toks = words(text)
    hits = sum(1 for t in toks if t in lex.hinglish)
    if hits >= 2 or (hits == 1 and len(toks) <= 3):
        return "hi-Latn"
    for lang, markers in lex.language_markers.items():
        if sum(1 for t in toks if t in markers) >= 3:
            return lang
    if len(toks) >= 3:
        return "en"
    return previous or "en"


# --------------------------------------------------------------------------- safety


def unsafe_category(text: str) -> str | None:
    """``illegal`` | ``harmful`` | ``abuse`` when an unsafe phrase is present, else None."""
    lex = load_lexicon()
    for category in ("illegal", "harmful", "abuse"):
        if has_phrase(text, lex.unsafe.get(category, ())):
            return category
    return None


def injection_suspected(text: str) -> bool:
    """Text that tries to instruct the assistant (kept as data, never obeyed).

    Runs of whitespace are folded to one space first (one linear pass), so a phrase split by a
    newline, a tab or double spaces is still caught and a long whitespace run costs nothing.
    """
    clean = squash_spaces(unicodedata.normalize("NFC", text))
    return any(p.search(clean) for p in load_lexicon().injection)


# --------------------------------------------------------------------------- labels and keywords


def _lang_pick(table: dict[str, str], lang: str | None) -> str:
    from mde_content.questions import lang_chain

    for tag in lang_chain(lang):
        if tag in table:
            return table[tag]
    return next(iter(table.values()))


def slot_label(slot_id: str, lang: str | None = "en") -> str:
    """Plain-language name of a slot in the user's language (English fallback)."""
    table = load_lexicon().slot_labels.get(slot_id)
    if table is None:
        return slot_id.replace(".", " ").replace("_", " ")
    return _lang_pick(table, lang)


def localized(table: dict[str, Any], lang: str | None = "en") -> Any:
    """Pick the entry of a ``{lang: value}`` table following the content language chain."""
    return _lang_pick(table, lang)


def theme_words(theme_id: str, label: str = "") -> tuple[str, ...]:
    """Words that point to a look: the lexicon synonyms plus the words of its label."""
    extra = tuple(w for w in words(label) if len(w) > 3 and w not in ("and", "with"))
    base = load_lexicon().theme_keywords.get(theme_id, ())
    return tuple(dict.fromkeys((*base, *extra)))


def nearest_theme(text: str, themes: dict[str, str]) -> str | None:
    """The theme id whose words appear most in ``text`` (``themes``: id -> label). None if none."""
    best: tuple[int, str] | None = None
    for tid, label in themes.items():
        score = len(keyword_hits(text, theme_words(tid, label)))
        if tid in normalize(text).replace(" ", "-"):
            score += 3
        if score and (best is None or score > best[0]):
            best = (score, tid)
    return best[1] if best else None


def color_word(text: str) -> str | None:
    hits = keyword_hits(text, load_lexicon().color_words)
    return hits[0] if hits else None


@lru_cache(maxsize=16)
def feature_options(archetype: str) -> tuple[tuple[str, str], ...]:
    """(feature id, English label) for the archetype's feature checklist, in card order."""
    from mde_content.questions import load_question_banks

    cards = load_question_banks().cards_for_slot("scope.features", archetype, "guided")
    return tuple((o.id, o.label) for c in cards[:1] for o in c.options)


def feature_keywords(archetype: str, feature_id: str, label: str) -> tuple[str, ...]:
    """Keywords for one feature: its label (and the halves of 'A and B'), id words and synonyms."""
    extra = load_lexicon().feature_keywords.get(feature_id, ())
    parts = [label, feature_id.replace("_", " ")]
    for chunk in re.split(r"\band\b|,|/", label, flags=re.IGNORECASE):
        if len(chunk.strip()) > 3:
            parts.append(chunk.strip())
    return tuple(dict.fromkeys(norm_all([*parts, *extra])))


def match_features(text: str, archetype: str) -> list[str]:
    """Feature ids of ``archetype`` whose keywords appear in ``text``, in checklist order."""
    found: list[str] = []
    for fid, label in feature_options(archetype):
        if keyword_hits(text, feature_keywords(archetype, fid, label)):
            found.append(fid)
    return found
