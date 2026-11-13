"""Parse the fact digest the summary call is given: one line per fact, ``FCT-014 [rule|pricing] statement (from Q-03)``.

The digest is built by the engine from the fact ledger. Parsing is plain string work, linear in the text: a line
that does not start with a fact id is ignored, a missing ``[type|topic]`` tag means no type and the topic
``general``, a missing ``(from ...)`` means no source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mde_compass.domain.interview import FACT_ID_RE

GENERAL = "general"
"""The topic of a fact whose digest line carries no topic tag."""
MAX_LINES = 5_000
MAX_TOPIC_CHARS = 60
_ID = re.compile(r"\s{0,8}(FCT-\d{3,})(?!\w)")
_TAG_SPLIT = re.compile(r"[,;/]")


@dataclass(frozen=True)
class DigestLine:
    fact_id: str
    kind: str  # the fact type tag, or ""
    topic: str  # the first topic tag, or GENERAL
    statement: str
    source: str  # "Q-03" from "(from Q-03)", or ""


def normalize_topic(raw: str) -> str:
    """A topic name: whitespace squashed and clipped; empty becomes GENERAL."""
    name = " ".join(raw.split())[:MAX_TOPIC_CHARS].strip()
    return name or GENERAL


def topic_key(name: str) -> str:
    """The key two topic names are compared by (case-insensitive, whitespace-insensitive)."""
    return " ".join(name.split()).casefold()


def parse_line(line: str) -> DigestLine | None:
    m = _ID.match(line)
    if m is None:
        return None
    rest = line[m.end() :].strip()
    kind = ""
    topic = GENERAL
    if rest.startswith("["):
        close = rest.find("]")
        if close != -1:
            tag = rest[1:close]
            rest = rest[close + 1 :].strip()
            kind_part, _, topic_part = tag.partition("|")
            kind = kind_part.strip()
            topic = normalize_topic(_TAG_SPLIT.split(topic_part, maxsplit=1)[0])
    source = ""
    if rest.endswith(")"):
        start = rest.rfind("(from ")
        if start != -1:
            source = rest[start + 6 : -1].strip()
            rest = rest[:start].strip()
    return DigestLine(
        fact_id=m.group(1), kind=kind, topic=topic, statement=" ".join(rest.split()), source=source
    )


def parse_digest(text: str) -> list[DigestLine]:
    """Every fact line of the digest, in order (an id that repeats keeps its first line)."""
    lines: list[DigestLine] = []
    seen: set[str] = set()
    for raw in text.splitlines()[:MAX_LINES]:
        parsed = parse_line(raw)
        if parsed is not None and parsed.fact_id not in seen:
            seen.add(parsed.fact_id)
            lines.append(parsed)
    return lines


def digest_ids(text: str) -> list[str]:
    return [line.fact_id for line in parse_digest(text)]


def is_fact_id(value: str) -> bool:
    return bool(FACT_ID_RE.match(value))
