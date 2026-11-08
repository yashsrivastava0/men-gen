"""Flatten the Brief and the working slots into plain-text facts for the wire inputs of the calls.

Used by the adapters of ``cmp.compile_text``, ``cmp.seed_research`` and ``cmp.probe_handoff`` (all three
send the model text, never domain objects). Pure functions; the catalog says which slot lives where
(``brief_path``), so no slot name is hardcoded here.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import DICT_SECTIONS, Brief
from mde_compass.engine.ports import TurnContext

GEO_KEYS = ("city", "region", "country")


@dataclass(frozen=True)
class SlotFact:
    slot_id: str
    label: str
    text: str
    state: str


def value_text(value: JsonValue) -> str:
    """A JSON value as one plain line (lists joined by commas; geography as "city, region, country")."""
    if value is None or isinstance(value, bool):
        return "" if value is None else str(value).lower()
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return ", ".join(t for t in (value_text(v) for v in value) if t)
    if set(value) <= set(GEO_KEYS):
        return ", ".join(t for t in (value_text(value.get(k)) for k in GEO_KEYS) if t)
    return "; ".join(f"{k}: {t}" for k, t in ((k, value_text(v)) for k, v in value.items()) if t)


def _dict_item(brief: Brief, path: str) -> tuple[str, str] | None:
    section, _, key = path.partition(".")
    if section not in DICT_SECTIONS or not key:
        return None
    table = getattr(brief, section)
    for candidate in (key, key.replace(".", "_"), key.rsplit(".", 1)[-1]):
        item = table.get(candidate)
        if item is not None and item.has_value():
            return value_text(item.value), item.status
    return None


def _list_item(brief: Brief, path: str) -> tuple[str, str] | None:
    names: list[str]
    status = "assumed"
    if path == "apps" and brief.apps:
        names, status = [a.name for a in brief.apps], brief.apps[0].status
    elif path == "roles" and brief.roles:
        names, status = [r.name_in_user_words for r in brief.roles], brief.roles[0].status
    elif path == "scope" and brief.scope:
        live = [f for f in brief.scope if f.scope_status == "in"]
        names, status = [f.name for f in live], (live[0].status if live else "assumed")
    elif path == "references" and brief.references:
        names, status = [r.label for r in brief.references], brief.references[0].status
    elif path == "glossary" and brief.glossary:
        names, status = [g.term for g in brief.glossary], brief.glossary[0].status
    elif path == "integrations" and brief.integrations:
        names, status = [i.name for i in brief.integrations], brief.integrations[0].status
    else:
        return None
    text = ", ".join(n.strip() for n in names if n.strip())
    return (text, status) if text else None


def slot_facts(ctx: TurnContext, brief: Brief, catalog: Catalog) -> dict[str, SlotFact]:
    """Every catalog slot that has a value: from the Brief first, then from the working slots."""
    working = {s.slot_id: s for s in ctx.slots}
    facts: dict[str, SlotFact] = {}
    for slot_id, slot in catalog.slots.items():
        found = _dict_item(brief, slot.brief_path) or _list_item(brief, slot.brief_path)
        if found is None and slot_id in working and working[slot_id].state != "empty":
            text = value_text(working[slot_id].value)
            found = (text, working[slot_id].state) if text else None
        if found is not None and found[0]:
            facts[slot_id] = SlotFact(slot_id, slot.label.get(ctx.language), found[0], found[1])
    return facts


def split_list(text: str) -> list[str]:
    """Items of a comma-joined slot text (empty pieces dropped)."""
    return [p.strip() for p in text.split(",") if p.strip()]
