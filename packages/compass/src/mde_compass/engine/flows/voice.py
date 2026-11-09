"""Voice dictation run-ons (IV-10): split into atomic statements, echo names, numbers and places, read back a
numbered list when there are three or more statements or anything is ambiguous, let the user remove items.

Splitting is rule-based and linear: sentence punctuation first, then literal joiner words (``and also``, ``and
then``, ``plus``, ``aur``...), with two guards so a list of languages ("hindi and english both") or a
one-word fragment is not torn apart. Fillers dictation adds at the start of a statement are dropped. Joiners and
fillers are data in ``flows.yaml``; the text is clipped before anything runs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from pydantic import JsonValue

from mde_compass.engine.flows import data
from mde_compass.engine.language import contains_phrase, normalize

_SENTENCE_END = re.compile(r"(?<=[.!?;])\s{1,5}|\n+")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_LIST_LINE = re.compile(r"^\s{0,3}(\d{1,2})[.)]\s{1,3}(.{1,400})$")
_HIDDEN = {
    "and": "\ue001",
    "aur": "\ue002",
    "और": "\ue003",
}  # joiner words kept inside one statement


@dataclass(frozen=True)
class Echo:
    """A name, number or place to read back for confirmation."""

    kind: str  # place | name | number
    text: str


@dataclass(frozen=True)
class VoicePlan:
    statements: list[str] = field(default_factory=list)
    echoes: list[Echo] = field(default_factory=list)
    readback: bool = (
        False  # a numbered list is warranted (three or more statements, or any ambiguity)
    )


@lru_cache(maxsize=1)
def _splitter_regex() -> re.Pattern[str]:
    words = sorted(data.phrases("voice", "splitters"), key=len, reverse=True)
    alt = "|".join(re.escape(w) for w in words)
    return re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=1)
def _filler_regex() -> re.Pattern[str]:
    words = sorted(data.phrases("voice", "fillers"), key=len, reverse=True)
    alt = "|".join(re.escape(w) for w in words)
    return re.compile(rf"^(?:(?:{alt})(?!\w)\s{{0,3}})+", re.IGNORECASE)


@lru_cache(maxsize=1)
def _language_names() -> tuple[str, ...]:
    return tuple(sorted(data.phrases("languages"), key=len, reverse=True))


def _protect_lists(text: str) -> str:
    """Keep "hindi and english" (a list of language names) in one piece: the joiner word is swapped for a
    private-use character while the text is split, and restored by ``_clean``."""
    alt = "|".join(re.escape(n) for n in _language_names())
    pat = re.compile(rf"(?<!\w)({alt})\s{{1,3}}(and|aur|और)\s{{1,3}}({alt})(?!\w)", re.IGNORECASE)

    def hide(m: re.Match[str]) -> str:
        return f"{m.group(1)}{_HIDDEN[m.group(2).casefold()]}{m.group(3)}"

    return pat.sub(hide, text)


def _clean(piece: str) -> str:
    for word, mark in _HIDDEN.items():
        piece = piece.replace(mark, f" {word} ")
    piece = re.sub(r"\s+", " ", piece).strip(" \t,.;:!?-")
    piece = _filler_regex().sub("", piece).strip(" ,.;:-")
    return piece


def segment(text: str) -> list[str]:
    """Atomic statements of one dictation (one statement when it is short)."""
    pol = data.policy()
    clipped = text[: data.SCAN_LIMIT].strip()
    if len(clipped.split()) < pol.split_min_words:
        one = _clean(clipped)
        return [one] if one else []
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(clipped):
        protected = _protect_lists(sentence)
        parts = _splitter_regex().split(protected)
        for part in parts:
            cleaned = _clean(part)
            if not cleaned:
                continue
            if pieces and len(cleaned.split()) < 2:
                pieces[-1] = (
                    f"{pieces[-1]} {cleaned}"  # a one-word fragment belongs to the line before
                )
                continue
            pieces.append(cleaned)
    out: list[str] = []
    for p in pieces[: pol.max_statements]:
        words = p.split()
        out.append(" ".join(words[: pol.max_statement_words]))
    return [s[0].upper() + s[1:] if s else s for s in out]


def _place_names(geo: JsonValue) -> list[str]:
    if isinstance(geo, Mapping):
        return [str(v) for v in geo.values() if isinstance(v, str) and v.strip()]
    return [geo] if isinstance(geo, str) and geo.strip() else []


def _names(brand: JsonValue, references: JsonValue) -> list[str]:
    out = [brand] if isinstance(brand, str) and brand.strip() else []
    for item in references if isinstance(references, list) else []:
        label = item.get("label") if isinstance(item, Mapping) else item
        if isinstance(label, str) and label.strip():
            out.append(label)
    return out


def echoes_for(
    text: str, *, geo: JsonValue = None, brand: JsonValue = None, references: JsonValue = None
) -> list[Echo]:
    """Names, numbers and places in the dictation, worth reading back (deduplicated, in order of kind)."""
    seen: set[str] = set()
    out: list[Echo] = []

    def add(kind: str, value: str) -> None:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(Echo(kind, value))

    for place in _place_names(geo):
        add("place", place)
    for name in _names(brand, references):
        add("name", name)
    for m in _NUMBER.finditer(text[: data.SCAN_LIMIT]):
        add("number", m.group(0))
    return out


def plan(
    text: str,
    *,
    geo: JsonValue = None,
    brand: JsonValue = None,
    references: JsonValue = None,
    ambiguous: bool = False,
) -> VoicePlan:
    """What to do with one dictation: the statements, the echoes, and whether a numbered read-back is due."""
    statements = segment(text)
    echoes = echoes_for(text, geo=geo, brand=brand, references=references)
    readback = len(statements) >= data.policy().readback_min or (ambiguous and len(statements) >= 2)
    return VoicePlan(statements, echoes, readback)


def numbered(statements: Sequence[str]) -> str:
    """ "1. first\\n2. second" (the shape the removal parser reads back)."""
    return "\n".join(f"{i}. {s}" for i, s in enumerate(statements, start=1))


def last_list(lines: Sequence[str]) -> list[str]:
    """The numbered statements of the latest assistant message that holds a list, looking only at the last two
    assistant messages so an old read-back is not still "on screen" (empty when none)."""
    for line in reversed(lines[-2:]):
        items: list[str] = []
        for raw in line.splitlines():
            m = _LIST_LINE.match(raw)
            if m and int(m.group(1)) == len(items) + 1:
                items.append(m.group(2).strip())
        if items:
            return items
    return []


def item_text(item: JsonValue) -> str:
    """The words of one list item (a name or label for records, the text itself otherwise)."""
    if isinstance(item, Mapping):
        for key in ("name", "label", "term", "name_in_user_words"):
            if isinstance(item.get(key), str):
                return str(item[key])
        return ""
    return item if isinstance(item, str) else ""


def removable(value: JsonValue, statement_norm: str) -> JsonValue:
    """What to clear from a slot when its supporting words sit in a removed statement: a single value goes
    whole; of a list only the items the statement names (never the others); None when nothing is certain."""
    if not isinstance(value, list):
        return value
    if len(value) == 1:
        return value
    named = [i for i in value if (t := normalize(item_text(i))) and t in statement_norm]
    return named or None


def removal_request(text: str, count: int) -> list[int]:
    """The 1-based statement numbers the user asks to remove ("remove 2 and 4"), else an empty list.

    A removal is a short message made only of a removal word, numbers and a few filler words ("please remove
    number 2 and 4"); anything else, such as "remove 2 features", is a normal message."""
    norm = normalize(text[:300])
    if len(norm.split()) > 12:
        return []
    words = data.phrases("voice", "remove_words")
    rest = norm
    found = False
    for phrase in sorted(words, key=len, reverse=True):
        if contains_phrase(rest, phrase):
            found = True
            rest = re.sub(rf"(?<!\w){re.escape(phrase)}(?!\w)", " ", rest)
    if not found:
        return []
    fillers = set(data.phrases("voice", "remove_fillers"))
    tokens = rest.split()
    if any(not (t.isdigit() or t in fillers) for t in tokens):
        return []
    nums = [int(t) for t in tokens if t.isdigit() and len(t) <= 2]
    return sorted({n for n in nums if 1 <= n <= count})
