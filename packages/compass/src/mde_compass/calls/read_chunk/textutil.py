"""Small linear-time text helpers shared by the three document-side calls (no regexes on user text).

Tokenisation is Unicode aware (Devanagari matras are kept inside words), so English, Hindi and Hinglish
rules work with the same code. Every function is a single pass over the input.
"""

from __future__ import annotations

import unicodedata

_WORD_PUNCT = "'-_"
_SYMBOL_TOKENS = "%₹$€£"
_SENTENCE_END = ".!?।…"  # includes the Devanagari danda and the ellipsis
_BULLETS = ("-", "*", "•", "–", "—", "●", "▪", ">")


def _is_mark(ch: str) -> bool:
    return unicodedata.category(ch) in ("Mn", "Mc")


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens. Numbers keep inner ``.`` and ``,`` (``5.5``, ``1,000``); ``%`` and currency
    symbols are their own tokens; apostrophes and hyphens stay inside words."""
    s = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    out: list[str] = []
    cur: list[str] = []
    n = len(s)
    for i, ch in enumerate(s):
        if (
            ch.isalnum()
            or _is_mark(ch)
            or (ch in _WORD_PUNCT and cur)
            or (ch in ".," and cur and cur[-1].isdigit() and i + 1 < n and s[i + 1].isdigit())
        ):
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur).strip(_WORD_PUNCT))
                cur = []
            if ch in _SYMBOL_TOKENS:
                out.append(ch)
    if cur:
        out.append("".join(cur).strip(_WORD_PUNCT))
    return [t for t in out if t]


def normalize(text: str) -> str:
    """Canonical text for duplicate detection: case, punctuation and spacing removed."""
    return " ".join(tokens(text))


def has_phrase(norm: str, phrase: str) -> bool:
    """Whole-word phrase test on already normalised text (``norm``/``phrase`` come from ``normalize``)."""
    return f" {phrase} " in f" {norm} "


def is_number(token: str) -> bool:
    return bool(token) and token[0].isdigit()


def split_units(text: str, abbreviations: frozenset[str]) -> list[tuple[int, int]]:
    """Split ``text`` into unit spans ``(start, end)`` that are verbatim substrings of it: lines, bullets
    stripped, table rows kept whole, long lines cut at sentence ends (not after known abbreviations)."""
    lines: list[tuple[int, int]] = []
    pos = 0
    n = len(text)
    while pos <= n:
        nl = text.find("\n", pos)
        end = n if nl < 0 else nl
        lines.append(_trim(text, pos, end))
        if nl < 0:
            break
        pos = nl + 1
    spans: list[tuple[int, int]] = []
    for k, (s, e) in enumerate(lines):
        if _is_table_header(text, lines, k):
            continue
        spans.extend(_line_units(text, s, e, abbreviations))
    return spans


def _is_table_header(text: str, lines: list[tuple[int, int]], k: int) -> bool:
    """A table row directly followed by a ``|---|---|`` separator row is the header: not a statement."""
    s, e = lines[k]
    if e <= s or text.count("|", s, e) < 2 or k + 1 >= len(lines):
        return False
    s2, e2 = lines[k + 1]
    return e2 > s2 and _is_separator_row(text[s2:e2]) and "|" in text[s2:e2]


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _strip_bullet(text: str, start: int, end: int) -> int:
    """Index after a leading bullet or list number (``-``, ``*``, ``1.``, ``2)``), else ``start``."""
    if start >= end:
        return start
    if text[start] in _BULLETS and start + 1 < end and text[start + 1].isspace():
        return start + 1
    j = start
    while j < end and j - start < 4 and text[j].isdigit():
        j += 1
    if 0 < j - start < 4 and j < end - 1 and text[j] in ".)" and text[j + 1].isspace():
        return j + 1
    return start


def _is_separator_row(line: str) -> bool:
    body = line.replace("|", "").replace("-", "").replace(":", "").replace("+", "")
    return not body.strip() and ("-" in line or "=" in line)


def _line_units(
    text: str, start: int, end: int, abbreviations: frozenset[str]
) -> list[tuple[int, int]]:
    start, end = _trim(text, start, end)
    if start >= end:
        return []
    line = text[start:end]
    if line[0] == "#" or _is_separator_row(line) or set(line) <= set("=-_*# "):
        return []  # headings and rules are structure, not statements
    if line.count("|") >= 2:  # a table row stays one unit; outer pipes dropped
        s, e = _trim(text, start + (line[0] == "|"), end - (line[-1] == "|"))
        return [(s, e)] if s < e else []
    start = _strip_bullet(text, start, end)
    start, end = _trim(text, start, end)
    units: list[tuple[int, int]] = []
    unit_start = start
    i = start
    while i < end:
        ch = text[i]
        if ch in _SENTENCE_END and (i + 1 >= end or text[i + 1].isspace()):
            if ch == "." and _is_abbreviation(text, unit_start, i, abbreviations):
                i += 1
                continue
            s, e = _trim(text, unit_start, i + 1)
            if s < e:
                units.append((s, e))
            unit_start = i + 1
        i += 1
    s, e = _trim(text, unit_start, end)
    if s < e:
        units.append((s, e))
    return units


def _is_abbreviation(text: str, unit_start: int, dot: int, abbreviations: frozenset[str]) -> bool:
    j = dot
    while j > unit_start and (text[j - 1].isalpha() or text[j - 1] == "."):
        j -= 1
    word = text[j:dot].casefold().strip(".")
    return word in abbreviations or (len(word) == 1 and word.isalpha())
