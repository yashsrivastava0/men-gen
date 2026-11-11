"""Small text helpers shared by intake, query anatomy, norm keys and passage selection. Pure code.

Tokens are runs of letters, combining marks and digits (the ``regex`` module's Unicode classes), so a
Devanagari or Kannada word with its vowel signs stays ONE token (Python's ``\\w`` splits it at the signs).
The stemmer only touches plain ASCII English-like words; every other script is compared as written
(casefolded). It is a matching aid, not a linguistic tool: two names are "the same thing" when their
stem sets are equal, or one contains the other (at least two stems), or they overlap enough.
"""

from __future__ import annotations

import unicodedata

import regex

_WORD = regex.compile(r"[\p{L}\p{M}\p{N}]+")
STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "by",
        "with",
        "at",
        "from",
        "into",
        "my",
        "your",
        "our",
        "their",
        "its",
        "is",
        "are",
        "be",
        "this",
        "that",
        "app",
        "apps",
        "application",
        "mobile",
        "new",
        "best",
        "top",
    ]
)


def tokens(text: str) -> list[str]:
    """Casefolded NFKC tokens of ``text`` in order."""
    return _WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def stem(word: str) -> str:
    """A crude English stem (booking -> book, payments -> pay, classes -> class); others unchanged."""
    if len(word) <= 3 or not (word.isascii() and word.isalpha()):
        return word
    w = word
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith(("ies", "ied")) and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("es") and len(w) > 4 and w[:-2].endswith(("s", "x", "z", "ch", "sh")):
        w = w[:-2]
    else:
        for suf in (
            "ments",
            "ment",
            "ations",
            "ation",
            "ions",
            "ion",
            "ings",
            "ing",
            "ers",
            "er",
            "ed",
        ):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                break
        else:
            if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
                w = w[:-1]
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    return w


def stems(text: str, *, stop: frozenset[str] = STOP_WORDS) -> tuple[str, ...]:
    """Stemmed content tokens of ``text`` in order (stop words dropped)."""
    return tuple(stem(t) for t in tokens(text) if t not in stop)


def stem_set(text: str, *, stop: frozenset[str] = STOP_WORDS) -> frozenset[str]:
    return frozenset(stems(text, stop=stop))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_thing(a: str, b: str, *, threshold: float = 0.6) -> bool:
    """Do two short names mean one thing? (``Online payment`` ~ ``Pay for orders online``.)"""
    sa, sb = stem_set(a), stem_set(b)
    if not sa or not sb:
        return a.casefold().strip() == b.casefold().strip()
    if sa == sb:
        return True
    small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(small) >= 2 and small <= big:
        return True
    return jaccard(sa, sb) >= threshold


def dedupe_names(items: list[tuple[str, str]], *, threshold: float = 0.6) -> dict[str, str]:
    """``{id: representative_id}``: each name maps to the FIRST id (in list order) that means the same."""
    reps: list[tuple[str, str]] = []
    out: dict[str, str] = {}
    for item_id, name in items:
        for rep_id, rep_name in reps:
            if same_thing(name, rep_name, threshold=threshold):
                out[item_id] = rep_id
                break
        else:
            reps.append((item_id, name))
            out[item_id] = item_id
    return out


def word_count(text: str) -> int:
    return len(text.split())


def clean_spaces(text: str) -> str:
    return " ".join(text.split())


# --- mojibake: UTF-8 read as a single-byte code page, then written out again -------------------------------------
# Some sites publish text that was decoded as Windows-1252 or Latin-1 and encoded as UTF-8 a second time ("double
# encoded"): an apostrophe becomes ``â`` plus two invisible controls, the rupee sign ``₹`` becomes ``â`` + control + ``¹``.
# Downstream that is worse than ugly: control characters are stripped and NFKC folds ``¹`` to ``1``, so "₹350" reads
# "1350" and the number check rightly refuses every price on the page (Small run 2026-11-11: 7 of the 10 number
# rejections were this, on the one page that held the studio prices). The repair is exact, not a guess: a run is
# rewritten only when its characters are the bytes of ONE valid UTF-8 sequence; ordinary accented text never is.
_CP1252_CONTINUATION = (
    "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"  # what bytes 0x80-0x9f look like in Windows-1252
)
_LEAD = regex.compile("[Â-ô]")


def _as_byte(ch: str) -> int | None:
    """The byte a character stands for when text is misread as Windows-1252 or Latin-1; None for any other character."""
    code = ord(ch)
    if 0x80 <= code <= 0xFF:  # Latin-1 range (the C1 controls 0x80-0x9f are Latin-1 only)
        return code
    if ch in _CP1252_CONTINUATION:
        return ch.encode("cp1252")[0]
    return None


def repair_mojibake(text: str) -> str:
    """``text`` with every double-encoded UTF-8 sequence rewritten to the character it stands for; the rest untouched."""
    if not text or text.isascii():
        return text
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if _LEAD.fullmatch(ch):
            lead = ord(ch)
            size = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
            chunk = text[i : i + size]
            raw = [_as_byte(c) for c in chunk]
            if len(chunk) == size and None not in raw and all(0x80 <= b <= 0xBF for b in raw[1:]):  # type: ignore[operator]
                try:
                    out.append(bytes(raw).decode("utf-8"))  # type: ignore[arg-type]
                    i += size
                    continue
                except UnicodeDecodeError:
                    pass
        out.append(ch)
        i += 1
    return "".join(out)
