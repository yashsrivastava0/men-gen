"""Fact kinds (ADR 0016): what a domain detail IS, from ``fact_kinds.yaml`` (data, not logic; hard rule 8).

A kind groups the details of the Brief (rule, number, acceptance, screen, flow, entity, compliance, nfr,
integration_detail, constraint, example, preference, context, other) and says which downstream agents need it by
default. This module is the one reader of that file:

* ``kind_for(fact_type, topics)``: the kind of a fact, from its topic words first, then its type;
* ``default_consumers(kind)``: the catalog ``Consumer`` values that need a fact of that kind;
* ``views_for(consumer)``: the consumer VIEWS a fact tagged for that consumer reaches;
* ``route_groups(fact_type, kind, topics)``: the ``cmp.compile_brief`` section groups a fact is sent to;
* ``theme_for(kind)``: the Gate 1 assumption theme of a detail of that kind.

Everything is validated when the file is loaded (cached): an unknown consumer, fact type, view or theme raises
``CatalogError`` with the place, so a typo never reaches a running session.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, get_args

import yaml

from mde_compass.catalog.loader import CatalogError, Consumer
from mde_compass.domain.brief import Theme
from mde_compass.domain.interview import FACT_TYPES

FACT_KINDS_PATH = Path(__file__).with_name("fact_kinds.yaml")
KIND_NAMES: tuple[str, ...] = (
    "rule",
    "number",
    "acceptance",
    "screen",
    "flow",
    "entity",
    "compliance",
    "nfr",
    "integration_detail",
    "constraint",
    "example",
    "preference",
    "context",
    "other",
)
VIEW_NAMES: tuple[str, ...] = ("research", "blueprint", "design", "verify")
FALLBACK_KIND = "other"
GENERAL_GROUP = "general"
_CONSUMERS: frozenset[str] = frozenset(get_args(Consumer))
_THEMES: frozenset[str] = frozenset(get_args(Theme))
_LANGS = ("en", "hi", "hinglish")
_WORDS = re.compile(r"[^\w\u0900-\u097F]+|_+", re.UNICODE)  # Devanagari marks stay inside a word


@dataclass(frozen=True)
class FactKind:
    key: str
    description: str
    consumers: tuple[str, ...]
    theme: str


@dataclass(frozen=True)
class CompileGroup:
    """One parallel ``cmp.compile_brief`` call: the Brief ``sections`` it may fill and the facts it receives."""

    id: str
    description: str
    sections: tuple[str, ...]
    types: frozenset[str]
    kinds: frozenset[str]
    topics: frozenset[str]


@dataclass(frozen=True)
class FactKinds:
    kinds: dict[str, FactKind]
    from_type: dict[str, str]
    topic_words: tuple[tuple[str, frozenset[str]], ...]  # (kind, words), in priority order
    view_routes: dict[str, tuple[str, ...]]
    groups: tuple[CompileGroup, ...]
    high_impact_kinds: frozenset[str] = frozenset()
    overlap_stopwords: frozenset[str] = frozenset()


def words_of(text: str) -> list[str]:
    """Lower-case whole words of a topic or a statement (``no_shows`` gives ``no``, ``shows``)."""
    return [w for w in _WORDS.split(text.casefold()) if w]


def _fold(word: str) -> str:
    """Plural folding for matching (``screens`` and ``screen`` are one word)."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss") and word.isascii():
        return word[:-1]
    return word


def _flat(block: Any, where: str) -> list[str]:
    if not isinstance(block, dict):
        raise CatalogError(f"fact_kinds.yaml: {where} must be a mapping of language to word lists")
    out: list[str] = []
    for lang in _LANGS:
        words = block.get(lang) or []
        if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
            raise CatalogError(f"fact_kinds.yaml: {where}.{lang} must be a list of words")
        out.extend(words)
    return out


def _str_list(raw: Any, where: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise CatalogError(f"fact_kinds.yaml: {where} must be a list of non-empty strings")
    return [x.strip() for x in raw]


def _kinds(raw: Any) -> dict[str, FactKind]:
    if not isinstance(raw, dict):
        raise CatalogError("fact_kinds.yaml: 'kinds' must be a mapping")
    out: dict[str, FactKind] = {}
    for key, body in raw.items():
        where = f"kinds.{key}"
        if not isinstance(body, dict) or not str(body.get("description", "")).strip():
            raise CatalogError(f"fact_kinds.yaml: {where} needs a description")
        consumers = _str_list(body.get("consumers"), f"{where}.consumers")
        bad = [c for c in consumers if c not in _CONSUMERS]
        if not consumers or bad:
            raise CatalogError(f"fact_kinds.yaml: {where}.consumers has unknown values {bad}")
        theme = str(body.get("theme", ""))
        if theme not in _THEMES:
            raise CatalogError(f"fact_kinds.yaml: {where}.theme {theme!r} is not a Gate 1 theme")
        out[str(key)] = FactKind(
            str(key), str(body["description"]).strip(), tuple(dict.fromkeys(consumers)), theme
        )
    if set(out) != set(KIND_NAMES):
        raise CatalogError(
            f"fact_kinds.yaml: kinds must be exactly {sorted(KIND_NAMES)}, got {sorted(out)}"
        )
    return out


def _groups(raw: Any, kinds: dict[str, FactKind]) -> tuple[CompileGroup, ...]:
    if not isinstance(raw, dict) or GENERAL_GROUP not in raw:
        raise CatalogError(f"fact_kinds.yaml: 'compile_groups' needs a {GENERAL_GROUP!r} group")
    groups: list[CompileGroup] = []
    for gid, body in raw.items():
        where = f"compile_groups.{gid}"
        if not isinstance(body, dict):
            raise CatalogError(f"fact_kinds.yaml: {where} must be a mapping")
        types = _str_list(body.get("types") or [], f"{where}.types")
        kind_list = _str_list(body.get("kinds") or [], f"{where}.kinds")
        if [t for t in types if t not in FACT_TYPES] or [k for k in kind_list if k not in kinds]:
            raise CatalogError(f"fact_kinds.yaml: {where} lists an unknown fact type or kind")
        sections = _str_list(body.get("sections"), f"{where}.sections")
        topics = _str_list(body.get("topics") or [], f"{where}.topics")
        groups.append(
            CompileGroup(
                id=str(gid),
                description=str(body.get("description", "")).strip(),
                sections=tuple(sections),
                types=frozenset(types),
                kinds=frozenset(kind_list),
                topics=frozenset(_fold(w) for t in topics for w in words_of(t)),
            )
        )
    return tuple(groups)


@lru_cache(maxsize=1)
def load_fact_kinds() -> FactKinds:
    raw = yaml.safe_load(FACT_KINDS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CatalogError("fact_kinds.yaml: top level must be a mapping")
    kinds = _kinds(raw.get("kinds"))
    from_type = raw.get("from_type")
    if not isinstance(from_type, dict) or set(from_type) != set(FACT_TYPES):
        raise CatalogError("fact_kinds.yaml: 'from_type' must map every fact type")
    if any(v not in kinds for v in from_type.values()):
        raise CatalogError("fact_kinds.yaml: 'from_type' maps to an unknown kind")
    topic_raw = raw.get("from_topics")
    if not isinstance(topic_raw, dict) or any(k not in kinds for k in topic_raw):
        raise CatalogError("fact_kinds.yaml: 'from_topics' keys must be kinds")
    topic_words = tuple(
        (
            str(kind),
            frozenset(_fold(w) for t in _flat(block, f"from_topics.{kind}") for w in words_of(t)),
        )
        for kind, block in topic_raw.items()
    )
    routes_raw = raw.get("view_routes")
    if not isinstance(routes_raw, dict) or set(routes_raw) != _CONSUMERS:
        raise CatalogError("fact_kinds.yaml: 'view_routes' must list every consumer value")
    routes: dict[str, tuple[str, ...]] = {}
    for consumer, views in routes_raw.items():
        listed = _str_list(views, f"view_routes.{consumer}")
        if not listed or [v for v in listed if v not in VIEW_NAMES]:
            raise CatalogError(f"fact_kinds.yaml: view_routes.{consumer} must name consumer views")
        routes[str(consumer)] = tuple(dict.fromkeys(listed))
    high = frozenset(_str_list(raw.get("high_impact_kinds") or [], "high_impact_kinds"))
    if high - set(kinds):
        raise CatalogError("fact_kinds.yaml: 'high_impact_kinds' lists an unknown kind")
    stop = frozenset(
        w.casefold() for w in _str_list(raw.get("overlap_stopwords") or [], "overlap_stopwords")
    )
    return FactKinds(
        kinds=kinds,
        from_type={str(k): str(v) for k, v in from_type.items()},
        topic_words=topic_words,
        view_routes=routes,
        groups=_groups(raw.get("compile_groups"), kinds),
        high_impact_kinds=high,
        overlap_stopwords=stop,
    )


# --------------------------------------------------------------------------------------------- queries
def kind_for(fact_type: str, topics: Iterable[str] = ()) -> str:
    """The kind of a fact: a topic word first (``screens`` gives ``screen``), then the type map, else ``other``."""
    data = load_fact_kinds()
    folded = {_fold(w) for t in topics for w in words_of(t)}
    if folded:
        for kind, words in data.topic_words:
            if folded & words:
                return kind
    return data.from_type.get(fact_type, FALLBACK_KIND)


def is_kind(value: str) -> bool:
    return value in load_fact_kinds().kinds


def normalize_kind(value: str, fact_type: str, topics: Iterable[str] = ()) -> str:
    """The model's kind when it is a known one, else the deterministic one (never empty)."""
    key = value.strip().casefold().replace(" ", "_").replace("-", "_")
    return key if is_kind(key) else kind_for(fact_type, topics)


def default_consumers(kind: str) -> tuple[str, ...]:
    """The consumers that need a fact of ``kind`` by default (an unknown kind gets those of ``other``)."""
    kinds = load_fact_kinds().kinds
    return (kinds.get(kind) or kinds[FALLBACK_KIND]).consumers


def theme_for(kind: str) -> str:
    kinds = load_fact_kinds().kinds
    return (kinds.get(kind) or kinds[FALLBACK_KIND]).theme


def views_for(consumer: str) -> tuple[str, ...]:
    """The consumer views a fact tagged ``consumer`` reaches (``qa`` reaches ``verify``, ``all`` reaches every view)."""
    return load_fact_kinds().view_routes.get(consumer, ())


def views_of(consumers: Iterable[str]) -> frozenset[str]:
    """Every consumer view that a fact with these consumer tags reaches."""
    return frozenset(v for c in consumers for v in views_for(c))


def route_groups(fact_type: str, kind: str, topics: Iterable[str] = ()) -> tuple[str, ...]:
    """The compile groups a fact goes to, in file order; ``general`` only when no other group takes it."""
    data = load_fact_kinds()
    folded = {_fold(w) for t in topics for w in words_of(t)}
    hit = [
        g.id
        for g in data.groups
        if g.id != GENERAL_GROUP
        and (fact_type in g.types or kind in g.kinds or bool(folded & g.topics))
    ]
    return tuple(hit) or (GENERAL_GROUP,)


def is_high_impact_kind(kind: str) -> bool:
    """A drafted detail of this kind always needs the person's decision (it is never a low-impact assumption)."""
    return kind in load_fact_kinds().high_impact_kinds


def overlap_stopwords() -> frozenset[str]:
    return load_fact_kinds().overlap_stopwords


def group(group_id: str) -> CompileGroup:
    return next(g for g in load_fact_kinds().groups if g.id == group_id)


__all__ = [
    "FALLBACK_KIND",
    "GENERAL_GROUP",
    "KIND_NAMES",
    "VIEW_NAMES",
    "CompileGroup",
    "FactKind",
    "FactKinds",
    "default_consumers",
    "group",
    "is_high_impact_kind",
    "is_kind",
    "kind_for",
    "load_fact_kinds",
    "normalize_kind",
    "overlap_stopwords",
    "route_groups",
    "theme_for",
    "views_for",
    "views_of",
    "words_of",
]
