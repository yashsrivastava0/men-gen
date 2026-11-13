"""Vocabularies and templates for the structure of the app, from ``structure_patterns.yaml`` (ADR 0017).

Data, not logic (hard rule 8). The one reader of that file; validated when first loaded (cached) so a typo raises
``CatalogError`` with the place and never reaches a running session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_compass.catalog.loader import CatalogError

PATTERNS_PATH = Path(__file__).with_name("structure_patterns.yaml")
LANGS = ("en", "hi")
KEYWORD_LANGS = ("en", "hi", "hinglish")
BANDS = ("small", "medium", "large")
_WORD = re.compile(r"[^\wऀ-ॿ]+|_+", re.UNICODE)


@dataclass(frozen=True)
class Pattern:
    key: str
    home: bool
    chrome: bool
    description: str


@dataclass(frozen=True)
class FieldHint:
    type: str
    auto: bool
    words: frozenset[str]


@dataclass(frozen=True)
class AccountScreen:
    pattern: str
    name: dict[str, str]
    purpose: dict[str, str]
    when_auth: tuple[str, ...]


@dataclass(frozen=True)
class SupportModule:
    id: str
    bands: tuple[str, ...]
    roles_min: int
    name: dict[str, str]
    purpose: dict[str, str]
    screens: tuple[AccountScreen, ...]


@dataclass(frozen=True)
class Floor:
    feature_patterns: tuple[str, ...]
    screens_per_feature: dict[str, int]
    labels: dict[str, dict[str, str]]
    purposes: dict[str, dict[str, str]]
    group_features_by_family: bool
    account_name: dict[str, str]
    account_purpose: dict[str, str]
    account_screens: tuple[AccountScreen, ...]
    support_modules: tuple[SupportModule, ...]
    actions: dict[str, dict[str, str]]
    acceptance: dict[str, str]


@dataclass(frozen=True)
class StructurePatterns:
    patterns: dict[str, Pattern]
    field_types: tuple[str, ...]
    field_hints: tuple[FieldHint, ...]
    floor: Floor

    def home_patterns(self) -> frozenset[str]:
        return frozenset(k for k, p in self.patterns.items() if p.home)

    def chrome_patterns(self) -> frozenset[str]:
        return frozenset(k for k, p in self.patterns.items() if p.chrome)


def _fail(where: str, why: str) -> CatalogError:
    return CatalogError(f"structure_patterns.yaml {where}: {why}")


def _text_map(raw: Any, where: str) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != set(LANGS):
        raise _fail(where, f"needs exactly the languages {LANGS}")
    out: dict[str, str] = {}
    for lang in LANGS:
        value = raw[lang]
        if not isinstance(value, str) or not value.strip():
            raise _fail(f"{where}.{lang}", "must be a non-empty text")
        out[lang] = value
    return out


def words_of(text: str) -> list[str]:
    """Lower-case words of a text (Devanagari marks stay inside a word)."""
    return [w for w in _WORD.split(text.casefold()) if w]


def _patterns(raw: Any) -> dict[str, Pattern]:
    if not isinstance(raw, dict) or not raw:
        raise _fail("patterns", "must be a non-empty mapping")
    out: dict[str, Pattern] = {}
    for key, item in raw.items():
        if not isinstance(item, dict) or not isinstance(item.get("home"), bool):
            raise _fail(f"patterns.{key}", "needs home: true or false")
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise _fail(f"patterns.{key}", "needs a description")
        chrome = item.get("chrome", False)
        if not isinstance(chrome, bool):
            raise _fail(f"patterns.{key}", "chrome must be true or false")
        out[str(key)] = Pattern(str(key), item["home"], chrome, description)
    if not any(p.home for p in out.values()):
        raise _fail("patterns", "at least one pattern must be a home")
    return out


def _hints(raw: Any, field_types: tuple[str, ...]) -> tuple[FieldHint, ...]:
    if not isinstance(raw, list):
        raise _fail("field_hints", "must be a list")
    hints: list[FieldHint] = []
    for i, item in enumerate(raw):
        where = f"field_hints[{i}]"
        if not isinstance(item, dict) or item.get("type") not in field_types:
            raise _fail(where, f"type must be one of {field_types}")
        if not isinstance(item.get("auto"), bool):
            raise _fail(where, "needs auto: true or false")
        words = item.get("words")
        if not isinstance(words, dict) or not set(words) <= set(KEYWORD_LANGS):
            raise _fail(where, f"words are per language of {KEYWORD_LANGS}")
        flat = {w for ws in words.values() for w in ws}
        if not flat:
            raise _fail(where, "needs at least one word")
        hints.append(
            FieldHint(str(item["type"]), item["auto"], frozenset(w.casefold() for w in flat))
        )
    return tuple(hints)


def _floor(raw: Any, patterns: dict[str, Pattern]) -> Floor:
    if not isinstance(raw, dict):
        raise _fail("floor", "must be a mapping")
    feature_patterns = raw.get("feature_patterns")
    if (
        not isinstance(feature_patterns, list)
        or not feature_patterns
        or any(p not in patterns for p in feature_patterns)
    ):
        raise _fail("floor.feature_patterns", "must be a list of known patterns")
    per = raw.get("screens_per_feature")
    if not isinstance(per, dict) or set(per) != set(BANDS):
        raise _fail("floor.screens_per_feature", f"needs exactly {BANDS}")
    for band, n in per.items():
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(feature_patterns):
            raise _fail(
                f"floor.screens_per_feature.{band}", "must be between 1 and the number of patterns"
            )
    labels = {k: _text_map(v, f"floor.labels.{k}") for k, v in (raw.get("labels") or {}).items()}
    purposes = {
        k: _text_map(v, f"floor.purposes.{k}") for k, v in (raw.get("purposes") or {}).items()
    }
    for pattern in feature_patterns:
        for what, table in (("labels", labels), ("purposes", purposes)):
            if pattern not in table:
                raise _fail(f"floor.{what}", f"missing the pattern {pattern!r}")
            if "{feature}" not in table[pattern]["en"]:
                raise _fail(f"floor.{what}.{pattern}", "the text must contain {feature}")
    account = raw.get("account_module")
    if not isinstance(account, dict):
        raise _fail("floor.account_module", "must be a mapping")
    screens: list[AccountScreen] = []
    for i, item in enumerate(account.get("screens") or []):
        where = f"floor.account_module.screens[{i}]"
        if not isinstance(item, dict) or item.get("pattern") not in patterns:
            raise _fail(where, "needs a known pattern")
        screens.append(
            AccountScreen(
                pattern=str(item["pattern"]),
                name=_text_map(item.get("name"), f"{where}.name"),
                purpose=_text_map(item.get("purpose"), f"{where}.purpose"),
                when_auth=tuple(str(v) for v in item.get("when_auth", [])),
            )
        )
    if not screens:
        raise _fail("floor.account_module.screens", "needs at least one screen")
    support: list[SupportModule] = []
    for i, item in enumerate(raw.get("support_modules") or []):
        where = f"floor.support_modules[{i}]"
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise _fail(where, "needs an id")
        bands = item.get("bands")
        if not isinstance(bands, list) or not bands or any(b not in BANDS for b in bands):
            raise _fail(f"{where}.bands", f"must be a list of {BANDS}")
        roles_min = item.get("roles_min", 0)
        if not isinstance(roles_min, int) or isinstance(roles_min, bool) or roles_min < 0:
            raise _fail(f"{where}.roles_min", "must be a whole number")
        mod_screens: list[AccountScreen] = []
        for j, sc in enumerate(item.get("screens") or []):
            if not isinstance(sc, dict) or sc.get("pattern") not in patterns:
                raise _fail(f"{where}.screens[{j}]", "needs a known pattern")
            mod_screens.append(
                AccountScreen(
                    pattern=str(sc["pattern"]),
                    name=_text_map(sc.get("name"), f"{where}.screens[{j}].name"),
                    purpose=_text_map(sc.get("purpose"), f"{where}.screens[{j}].purpose"),
                    when_auth=(),
                )
            )
        if not mod_screens:
            raise _fail(f"{where}.screens", "needs at least one screen")
        support.append(
            SupportModule(
                id=str(item["id"]),
                bands=tuple(str(b) for b in bands),
                roles_min=int(roles_min),
                name=_text_map(item.get("name"), f"{where}.name"),
                purpose=_text_map(item.get("purpose"), f"{where}.purpose"),
                screens=tuple(mod_screens),
            )
        )
    if len({m.id for m in support}) != len(support):
        raise _fail("floor.support_modules", "ids must be distinct")
    actions = {k: _text_map(v, f"floor.actions.{k}") for k, v in (raw.get("actions") or {}).items()}
    if "other" not in actions:
        raise _fail("floor.actions", "needs an 'other' entry")
    acceptance = _text_map(raw.get("acceptance"), "floor.acceptance")
    if "{goal}" not in acceptance["en"]:
        raise _fail("floor.acceptance.en", "the text must contain {goal}")
    group = raw.get("group_features_by_family")
    if not isinstance(group, bool):
        raise _fail("floor.group_features_by_family", "must be true or false")
    return Floor(
        feature_patterns=tuple(str(p) for p in feature_patterns),
        screens_per_feature={str(k): int(v) for k, v in per.items()},
        labels=labels,
        purposes=purposes,
        group_features_by_family=group,
        account_name=_text_map(account.get("name"), "floor.account_module.name"),
        account_purpose=_text_map(account.get("purpose"), "floor.account_module.purpose"),
        account_screens=tuple(screens),
        support_modules=tuple(support),
        actions=actions,
        acceptance=acceptance,
    )


@lru_cache(maxsize=1)
def load_structure_patterns() -> StructurePatterns:
    data = yaml.safe_load(PATTERNS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise _fail("version", "must be 1")
    patterns = _patterns(data.get("patterns"))
    types = data.get("field_types")
    if not isinstance(types, list) or not types or len(set(types)) != len(types):
        raise _fail("field_types", "must be a list of distinct names")
    field_types = tuple(str(t) for t in types)
    return StructurePatterns(
        patterns=patterns,
        field_types=field_types,
        field_hints=_hints(data.get("field_hints"), field_types),
        floor=_floor(data.get("floor"), patterns),
    )


def text_for(table: dict[str, str], lang: str) -> str:
    """A localised text from a ``{en, hi}`` map; any other language gets English."""
    return table.get(lang) or table["en"]


__all__ = [
    "AccountScreen",
    "FieldHint",
    "Floor",
    "Pattern",
    "StructurePatterns",
    "SupportModule",
    "load_structure_patterns",
    "text_for",
    "words_of",
]
