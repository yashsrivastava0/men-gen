"""Language detection and the text normalisation shared by the router, the gate and the safety screen.

``detect_language`` returns ``en`` or ``hi`` (the two languages Compass has strings for). Rules, all linear:

* Devanagari script: when at least 30% of the letters (or of the words) are Devanagari the text is Hindi.
* Hinglish (Hindi in Latin script) maps to ``hi`` ONLY when strong Hindi markers are present: at least two
  distinct markers, or one marker in a message of at most four words. Words that are also common English
  words (``do``, ``to``, ``par``, ``sab``...) are deliberately not markers.
* Otherwise the previous language is kept (a short "ok" or a name never flips the chat language), except a
  clearly English sentence (four or more words, no Hindi marker, at least two common English function
  words) switches ``hi`` back to ``en`` (IV-09, a language switch).

Phrase matching helpers (``normalize``, ``contains_phrase``) are copy-adapted from the Discover lexicon: NFC,
casefold, apostrophes dropped, punctuation to spaces, whole-word matching.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_PUNCT = re.compile(r"[^\w\sऀ-ॿ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
_APOSTROPHES = re.compile(r"['’‘`]")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

SCAN_LIMIT = 20_000  # characters of a message the language rules look at (linear, bounded)

HINDI_MARKERS: frozenset[str] = frozenset(
    {
        "hai", "hain", "nahi", "nahin", "kya", "kyun", "kyon", "kaise", "kitna", "kitne", "kitni",
        "mujhe", "mera", "meri", "mere", "aap", "apna", "apni", "tum", "chahiye", "chahta", "chahti",
        "banana", "banani", "banao", "karo", "karna", "karni", "karein", "kijiye", "wala", "wali",
        "wale", "dukaan", "dukan", "haan", "theek", "thik", "bilkul", "zaroor", "samajh", "batao",
        "bataiye", "dikhao", "accha", "achha", "toh", "liye", "abhi", "kuch", "bhai", "yaar", "shuru",
        "ruko", "rukiye", "kaun", "kahan", "agar", "lekin", "magar", "isliye", "sirf", "sabhi",
    }
)  # fmt: skip
ENGLISH_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "the", "and", "is", "to", "a", "for", "of", "my", "i", "want", "with", "that", "this", "are",
        "it", "in", "on", "we", "our", "you", "your", "can", "will", "need", "would", "like",
    }
)  # fmt: skip
HINDI_SHARE = 0.3


def normalize(text: str) -> str:
    """NFC, casefold, drop apostrophes, punctuation to spaces, collapse spaces."""
    out = unicodedata.normalize("NFC", text).casefold()
    out = _APOSTROPHES.sub("", out)
    out = _PUNCT.sub(" ", out)
    return _SPACES.sub(" ", out).strip()


@lru_cache(maxsize=4096)
def _word_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")


def contains_phrase(haystack_norm: str, phrase_norm: str) -> bool:
    """The already-normalised ``phrase_norm`` occurs in ``haystack_norm`` as whole words."""
    return bool(phrase_norm) and phrase_norm in haystack_norm and (
        _word_pattern(phrase_norm).search(haystack_norm) is not None
    )  # fmt: skip


def devanagari_share(text: str) -> float:
    """Share (0..1) of the letters of ``text`` that are Devanagari (0.0 when there are no letters)."""
    letters = 0
    deva = 0
    for ch in text[:SCAN_LIMIT]:
        if ch.isalpha():
            letters += 1
            if "ऀ" <= ch <= "ॿ":
                deva += 1
    return deva / letters if letters else 0.0


def _has_devanagari(token: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in token)


def detect_language(text: str, previous: str = "en") -> str:
    """``en`` or ``hi`` for ``text``; ``previous`` when the text does not say (see the module docstring)."""
    prev = previous if previous in ("en", "hi") else "en"
    clipped = text[:SCAN_LIMIT]
    if devanagari_share(clipped) >= HINDI_SHARE:
        return "hi"
    tokens = clipped.split()
    if tokens and sum(1 for t in tokens if _has_devanagari(t)) / len(tokens) >= HINDI_SHARE:
        return "hi"  # mixed text: at least 30% of the words are Devanagari
    words = [w.casefold() for w in _WORD.findall(clipped)]
    if not words:
        return prev
    markers = {w for w in words if w in HINDI_MARKERS}
    if len(markers) >= 2 or (markers and len(words) <= 4):
        return "hi"
    if prev == "hi" and len(words) >= 4 and not markers:
        english = sum(1 for w in words if w in ENGLISH_FUNCTION_WORDS)
        if english >= 2:
            return "en"
    return prev
