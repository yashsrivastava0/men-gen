"""Reading the fact ledger text back, and cutting long text into pieces the gateway will not truncate.

The ledger travels as plain text (``engine.context_pack.full_ledger``): optional ``# topic`` headers and one line
per fact::

    FCT-014 [rule|pricing] statement (from Q-03) | conf 0.80 | stated | consumers: research,design | quote: "..."

``parse_ledger`` recovers what code needs from those lines (id, type, topic, statement, consumers) without
guessing: a line that does not start with a fact id and a ``[type|topics]`` tag is ignored. All patterns are
linear-time (no nested quantifiers, no regex over the long free-text parts).

``chunk_text`` exists because the gateway caps every untrusted input at ``GatewayConfig.max_untrusted_chars``
(20,000 by default) and silently appends ``[truncated]``. A ledger of 300 facts with quotes is far longer, so the
wire carries text as a list of pieces; the gateway wraps and caps each piece on its own, and nothing is cut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, get_args

from mde_compass.catalog.loader import Consumer
from mde_compass.engine.facts import GENERAL_TOPIC

CHUNK_CHARS = 15_000
"""Per-piece size. The gateway cap is 20,000 characters per piece and defusing a wrapper tag can grow a piece by
about a sixth, so 15,000 stays below the cap in the worst case."""
SUMMARY_CHARS = 120
CONSUMERS: frozenset[str] = frozenset(get_args(Consumer))
ALL: Final = "all"

_LINE = re.compile(r"^(FCT-\d+) \[([^\]]*)\] ?(.*)$")
_SEP = " | "
_FROM = " (from "


@dataclass(frozen=True)
class LedgerFact:
    """One parsed ledger line."""

    fact_id: str
    type: str
    topic: str  # the first topic of the tag, else ``general``
    statement: str
    consumers: tuple[str, ...] = ()  # only valid consumer names, in the order the line gave them
    topics: tuple[str, ...] = ()  # every topic of the tag (``topic`` is the first)


def _statement(rest: str) -> str:
    head = rest.split(_SEP, 1)[0]
    cut = head.rfind(_FROM)
    if cut >= 0 and head.endswith(")") and ")" not in head[cut + len(_FROM) : -1]:
        head = head[:cut]
    return head.strip()


def _consumers(rest: str) -> tuple[str, ...]:
    for segment in rest.split(_SEP)[1:]:
        if segment.startswith("consumers:"):
            names = [n.strip() for n in segment[len("consumers:") :].split(",")]
            return tuple(dict.fromkeys(n for n in names if n in CONSUMERS))
    return ()


def parse_line(line: str) -> LedgerFact | None:
    match = _LINE.match(line.strip())
    if match is None:
        return None
    fact_id, tag, rest = match.groups()
    fact_type, _, topics = tag.partition("|")
    listed = tuple(t.strip() for t in topics.split(",") if t.strip())
    topic = listed[0] if listed else GENERAL_TOPIC
    return LedgerFact(
        fact_id=fact_id,
        type=fact_type.strip(),
        topic=topic,
        statement=_statement(rest),
        consumers=_consumers(rest),
        topics=listed,
    )


def parse_ledger(text: str) -> list[LedgerFact]:
    """The facts of a ledger text in line order; a repeated id keeps its first line."""
    seen: dict[str, LedgerFact] = {}
    for line in text.split("\n"):
        fact = parse_line(line)
        if fact is not None and fact.fact_id not in seen:
            seen[fact.fact_id] = fact
    return list(seen.values())


def ledger_ids(text: str) -> list[str]:
    return [f.fact_id for f in parse_ledger(text)]


def short_summary(facts: list[LedgerFact], limit: int = SUMMARY_CHARS) -> str:
    """The first fact's statement, cut to ``limit`` characters (the deterministic summary)."""
    if not facts:
        return ""
    text = " ".join(facts[0].statement.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Cut ``text`` into pieces of at most ``limit`` characters at line boundaries (a single longer line is
    split hard). Joining the pieces with a newline gives the text back; empty text gives no pieces."""
    if not text.strip():
        return []
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                pieces.append("\n".join(current))
                current, size = [], 0
            pieces.append(line[:limit])
            line = line[limit:]
        cost = len(line) + (1 if current else 0)
        if current and size + cost > limit:
            pieces.append("\n".join(current))
            current, size, cost = [], 0, len(line)
        current.append(line)
        size += cost
    if current:
        pieces.append("\n".join(current))
    return pieces


def join_text(pieces: list[str]) -> str:
    return "\n".join(pieces)
