"""Names for ids: every owner-facing sentence says what a thing is called, never a bare id (hard rule of the Bench).

``plain(text, graph)`` replaces every id token in a free text by the name of the node it points to. An id that the graph
does not know (a Compass fact id, an acceptance id) becomes a short phrase, so no bare id reaches the owner.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from typing import Any

from mde_spec_core.graph import NODE_TYPES, Envelope, SpecGraph

_KIND_WORD = r"(?:app|role|screen|feature|entity|module|flow|operation|rule|story|integration)"
ID_IN_TEXT = re.compile(
    rf"(?:\b{_KIND_WORD}\s+)?(?P<id>(?:HERO-ENT-\d+-\d+|(?:APP|ROL|MOD|SCR|FLW|ENT|FTR|OP|RUL|US|PER|INT|BLK|ACC|JRN|ASSET|FCT)-S?\d{{1,6}}))\b(?:\s+'(?P<quote>[^']*)')?"
)
ID_RE = re.compile(
    r"\b(?:HERO-ENT-\d+-\d+|(?:APP|ROL|MOD|SCR|FLW|ENT|FTR|OP|RUL|US|PER|INT|BLK|ACC|JRN|ASSET|FCT)-S?\d{1,6})\b"
)
_UNKNOWN = {
    "FCT": "something you said in the Brief",
    "HERO": "an example record",
    "ACC": "an acceptance check",
    "BLK": "a shared building block",
    "PER": "a persona",
    "JRN": "a journey",
    "ASSET": "an asset",
}


def node_name(node: Envelope | None) -> str:
    """The best human name of a node, whatever its kind."""
    if node is None:
        return ""
    d: Any = node
    for attr in ("name", "subject", "want", "goal", "text", "statement"):
        v = getattr(d, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def name_of(graph: SpecGraph, node_id: str) -> str:
    """The name of an id (with aliases followed); a short phrase when the id is unknown."""
    node = graph.find(node_id)
    if node is None:
        for kind in NODE_TYPES:
            for n in getattr(graph, kind).values():
                if node_id in n.aliases:
                    node = n
                    break
            if node is not None:
                break
    nm = node_name(node)
    if nm:
        return nm
    prefix = node_id.split("-", 1)[0]
    return _UNKNOWN.get(prefix, "an item")


def plain(text: str, graph: SpecGraph) -> str:
    """``text`` with every id replaced by its name."""
    if not text:
        return ""

    def one(m: re.Match[str]) -> str:
        nm = name_of(graph, m.group("id"))
        if nm in _UNKNOWN.values() or nm == "an item":
            return (
                m.group("quote") or nm
            )  # an id nobody knows: the text's own quoted name, else a short phrase
        return nm

    return ID_IN_TEXT.sub(one, str(text))


def names(graph: SpecGraph, ids: Iterable[str]) -> list[str]:
    return [name_of(graph, i) for i in ids]


def join_words(items: list[str], last: str = "and") -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def usd_text(micros: int, *, up: bool = False) -> str:
    """Integer micro-USD as ``USD 1.23`` (whole cents; ``up`` rounds a cost up, never down). No floats."""
    cents = micros // 10_000
    if up and micros % 10_000:
        cents += 1
    return f"USD {cents // 100}.{cents % 100:02d}"


def esc(text: object) -> str:
    """One string made safe for HTML text and quoted attributes; URL schemes are defanged (a pack never makes links)."""
    s = html.escape(str(text), quote=True)
    return s.replace("://", ":​//")
