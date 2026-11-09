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

from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.engine.flows import data

_SENTENCE_END = re.compile(r"(?<=[.!?;])\s{1,5}|\n+")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_LIST_LINE = re.compile(r"^\s{0,3}(\d{1,2})[.)]\s{1,3}(.{1,400})$")
_REMOVE_NUMBERS = re.compile(r"\d{1,2}")
_JOIN = "⁠"  # word joiner: protects "a and b" from the splitter


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
    names = {lx.normalize(str(n)) for ns in lx.load_lexicon().raw["languages"].values() for n in ns}
    return tuple(sorted((n for n in names if n), key=len, reverse=True))


def _protect_lists(text: str) -> str:
    """Keep "hindi and english" (a list of language names) in one piece."""
    alt = "|".join(re.escape(n) for n in _language_names())
    pat = re.compile(
        rf"(?<!\w)({alt})(\s{{1,3}}(?:and|aur|और)\s{{1,3}})({alt})(?!\w)", re.IGNORECASE
    )
    return pat.sub(
        lambda m: m.group(1) + _JOIN + m.group(2).replace(" ", _JOIN) + _JOIN + m.group(3), text
    )


def _clean(piece: str) -> str:
    piece = piece.replace(_JOIN, " ")
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
    """The numbered statements of the most recent assistant message that holds a list (empty when none)."""
    for line in reversed(lines):
        items: list[str] = []
        for raw in line.splitlines():
            m = _LIST_LINE.match(raw)
            if m and int(m.group(1)) == len(items) + 1:
                items.append(m.group(2).strip())
        if items:
            return items
    return []


def removal_request(text: str, count: int) -> list[int]:
    """The 1-based statement numbers the user asks to remove ("remove 2 and 4"), else an empty list.

    A removal is a short message with a removal word and at least one valid number; anything else is a normal
    message."""
    norm = lx.normalize(text[:300])
    if (
        len(norm.split()) > 12
        or data.find_phrase(norm, data.phrases("voice", "remove_words")) is None
    ):
        return []
    nums = [int(n) for n in _REMOVE_NUMBERS.findall(norm)]
    return sorted({n for n in nums if 1 <= n <= count})
