"""Realism rules for the structure of the app, from ``structure_rules.yaml`` (ADR 0017, plan 5d.2).

Data, not logic (hard rule 8). The one reader of that file; validated when first loaded (cached).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_compass.catalog.loader import CatalogError

RULES_PATH = Path(__file__).with_name("structure_rules.yaml")
KEYWORD_LANGS = ("en", "hi", "hinglish")
WHY_LANGS = ("en", "hi")
CONDITIONS = (
    "sensitive_any",
    "auth_any",
    "locales_min",
    "integration_words",
    "roles_min",
    "role_words",
)


CHECK_CODES: tuple[str, ...] = (
    "dangling_ref",
    "screen_without_module",
    "module_without_screens",
    "module_entry_missing",
    "screen_duplicate_name",
    "screen_near_duplicate",
    "flow_too_short",
    "flow_role_cannot_access_step",
    "entity_without_fields",
    "entity_unused",
    "entity_access_missing",
    "screen_entity_access_conflict",
    "entity_key_invalid",
    "field_key_invalid",
    "field_type_money",
    "field_type_hint",
    "role_without_access",
    "role_without_home",
    "app_without_screens",
    "feature_without_screen",
    "must_feature_without_flow",
    "feature_thin",
    "module_thin",
    "screen_for_excluded_feature",
    "screen_unreachable",
    "screen_without_roles",
    "module_too_big",
    "thin_draft",
    "over_band",
    "fact_orphan",
    "rule_unmet",
    "critic_finding",
    "unknown_pattern",
    "unknown_field_type",
)
SEVERITIES = ("error", "warn")
FIXES = ("code", "repair", "expand", "none")


@dataclass(frozen=True)
class CheckPolicy:
    severity: str
    fix: str


@dataclass(frozen=True)
class Requirement:
    id: str
    words: frozenset[str]
    why: dict[str, str]


@dataclass(frozen=True)
class Rule:
    id: str
    sensitive_any: frozenset[str]
    auth_any: frozenset[str]
    locales_min: int
    integration_words: frozenset[str]
    roles_min: int
    role_words: frozenset[str]
    requires: tuple[Requirement, ...]


def _fail(where: str, why: str) -> CatalogError:
    return CatalogError(f"structure_rules.yaml {where}: {why}")


def _strings(raw: Any, where: str) -> frozenset[str]:
    if (
        not isinstance(raw, list)
        or not raw
        or not all(isinstance(v, str) and v.strip() for v in raw)
    ):
        raise _fail(where, "must be a non-empty list of texts")
    return frozenset(v.casefold() for v in raw)


def _requirement(raw: Any, where: str) -> Requirement:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        raise _fail(where, "needs an id")
    words = raw.get("words")
    if not isinstance(words, dict) or not set(words) <= set(KEYWORD_LANGS) or "en" not in words:
        raise _fail(f"{where}.words", f"per language of {KEYWORD_LANGS}, English required")
    flat: set[str] = set()
    for lang, items in words.items():
        flat |= _strings(items, f"{where}.words.{lang}")
    why = raw.get("why")
    if not isinstance(why, dict) or set(why) != set(WHY_LANGS):
        raise _fail(f"{where}.why", f"needs exactly the languages {WHY_LANGS}")
    for lang, text in why.items():
        if not isinstance(text, str) or not text.strip():
            raise _fail(f"{where}.why.{lang}", "must be a non-empty text")
    return Requirement(str(raw["id"]), frozenset(flat), {str(k): str(v) for k, v in why.items()})


def _rule(raw: Any, i: int) -> Rule:
    where = f"rules[{i}]"
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        raise _fail(where, "needs an id")
    when = raw.get("when")
    if not isinstance(when, dict) or not when or not set(when) <= set(CONDITIONS):
        raise _fail(f"{where}.when", f"needs at least one of {CONDITIONS}")
    for key in ("locales_min", "roles_min"):
        value = when.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise _fail(f"{where}.when.{key}", "must be a whole number")
    requires = raw.get("requires")
    if not isinstance(requires, list) or not requires:
        raise _fail(f"{where}.requires", "needs at least one capability")
    reqs = tuple(_requirement(r, f"{where}.requires[{j}]") for j, r in enumerate(requires))
    if len({r.id for r in reqs}) != len(reqs):
        raise _fail(f"{where}.requires", "capability ids must be distinct")

    def words(key: str) -> frozenset[str]:
        return _strings(when[key], f"{where}.when.{key}") if key in when else frozenset()

    return Rule(
        id=str(raw["id"]),
        sensitive_any=words("sensitive_any"),
        auth_any=words("auth_any"),
        locales_min=int(when.get("locales_min", 0)),
        integration_words=words("integration_words"),
        roles_min=int(when.get("roles_min", 0)),
        role_words=words("role_words"),
        requires=reqs,
    )


@lru_cache(maxsize=1)
def load_structure_rules() -> tuple[Rule, ...]:
    data = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise _fail("version", "must be 1")
    raw = data.get("rules")
    if not isinstance(raw, list) or not raw:
        raise _fail("rules", "must be a non-empty list")
    rules = tuple(_rule(item, i) for i, item in enumerate(raw))
    if len({r.id for r in rules}) != len(rules):
        raise _fail("rules", "rule ids must be distinct")
    return rules


@lru_cache(maxsize=1)
def load_check_policy() -> dict[str, CheckPolicy]:
    """Severity and who fixes it, for every check code (the yaml must name exactly ``CHECK_CODES``)."""
    data = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    raw = data.get("checks") if isinstance(data, dict) else None
    if not isinstance(raw, dict) or set(raw) != set(CHECK_CODES):
        missing = sorted(set(CHECK_CODES) - set(raw or {}))
        extra = sorted(set(raw or {}) - set(CHECK_CODES))
        raise _fail(
            "checks", f"must name exactly the check codes (missing {missing}, unknown {extra})"
        )
    out: dict[str, CheckPolicy] = {}
    for code, item in raw.items():
        if (
            not isinstance(item, dict)
            or item.get("severity") not in SEVERITIES
            or item.get("fix") not in FIXES
        ):
            raise _fail(f"checks.{code}", f"needs severity of {SEVERITIES} and fix of {FIXES}")
        out[str(code)] = CheckPolicy(str(item["severity"]), str(item["fix"]))
    return out


__all__ = [
    "CHECK_CODES",
    "CheckPolicy",
    "Requirement",
    "Rule",
    "load_check_policy",
    "load_structure_rules",
]
