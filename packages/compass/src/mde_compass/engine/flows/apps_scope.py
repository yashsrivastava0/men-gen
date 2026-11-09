"""Five or more apps requested (DC-11): propose the main two now and put the rest in later phases.

The Brief has no phase field on an app, so a later-phase app is kept in the scope ledger as a feature with
status ``later`` (the Phase 2 backlog, source ``later_phase_app``) and leaves the ``apps`` list; the first
release holds only the main apps. The main ones are the first the user named. Pure functions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pydantic import JsonValue

from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.engine.flows import data

SOURCE = "later_phase_app"
APPS_SLOT = "apps.list"
FEATURES_SLOT = "scope.features"


@dataclass(frozen=True)
class PhasePlan:
    now: list[dict[str, Any]]
    later: list[dict[str, Any]]


def _name(item: Any) -> str:
    return str(item.get("name", "")).strip() if isinstance(item, Mapping) else ""


@lru_cache(maxsize=1)
def _named_regex() -> tuple[re.Pattern[str], dict[str, str]]:
    surfaces = lx.load_lexicon().raw["surface_words"]
    kind_of = {lx.normalize(str(w)): str(kind) for kind, words in surfaces.items() for w in words}
    alt = "|".join(re.escape(w) for w in sorted(kind_of, key=len, reverse=True))
    # "<name word> <app noun>": literal alternation, one bounded gap, linear
    return re.compile(
        rf"(?<!\w)([^\W\d_][\w'’-]{{1,24}})\s{{1,3}}({alt})(?!\w)", re.IGNORECASE
    ), kind_of


def named_apps(text: str) -> list[dict[str, Any]]:
    """The apps a message names ("a driver app, an admin panel"): ``{"name", "surface"}`` in order, no duplicates.

    Needed beside the understander's own list because a rule-based understander only knows a few role words;
    the split below must see every app the user listed."""
    pattern, kind_of = _named_regex()
    stop = {lx.normalize(w) for w in data.load_flows()["apps"]["name_stop"]}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in pattern.finditer(text[: data.SCAN_LIMIT]):
        word, noun = m.group(1), m.group(2)
        if lx.normalize(word) in stop:
            continue
        name = f"{word} {noun}"
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append(
            {
                "name": name,
                "surface": "web" if kind_of.get(lx.normalize(noun)) == "web" else "mobile",
            }
        )
    return out


def union(named: list[dict[str, Any]], held: JsonValue) -> list[dict[str, Any]]:
    """The apps in the message first (their order is the user's priority), then any the slot already holds."""
    out = list(named)
    have = {_name(a).casefold() for a in out}
    for item in held if isinstance(held, list) else []:
        if isinstance(item, dict) and _name(item) and _name(item).casefold() not in have:
            out.append({k: v for k, v in item.items() if k != "_meta"})
            have.add(_name(item).casefold())
    return out


def phase_plan(apps: JsonValue) -> PhasePlan | None:
    """The split when the list reaches ``phase_at`` apps; None below that."""
    items = [
        i for i in (apps if isinstance(apps, list) else []) if isinstance(i, dict) and _name(i)
    ]
    pol = data.policy()
    if len(items) < pol.apps_phase_at:
        return None
    return PhasePlan(now=items[: pol.apps_main_now], later=items[pol.apps_main_now :])


def later_records(later: list[dict[str, Any]], intent_prefix: str) -> list[dict[str, Any]]:
    """Scope-ledger records (status later, priority later) for the apps that wait."""
    out: list[dict[str, Any]] = []
    for app in later:
        purpose = str(app.get("purpose") or app.get("surface") or "").strip()
        intent = f"{intent_prefix}: {purpose}" if purpose else intent_prefix
        out.append(
            {
                "name": _name(app),
                "intent": intent,
                "scope_status": "later",
                "priority": "later",
                "source": SOURCE,
            }
        )
    return out


def merge_features(existing: JsonValue, records: list[dict[str, Any]]) -> list[JsonValue]:
    """The features list plus the later-phase records not already there (matched by name)."""
    items: list[JsonValue] = list(existing) if isinstance(existing, list) else []
    have = {_name(i).casefold() for i in items if isinstance(i, dict)}
    for r in records:
        if _name(r).casefold() not in have:
            items.append(r)
            have.add(_name(r).casefold())
    return items


def names(apps: list[dict[str, Any]], limit: int | None = None) -> str:
    listed = [_name(a) for a in apps][: limit if limit is not None else len(apps)]
    return ", ".join(listed)
