"""Path helpers shared by the view modules (pure, deterministic).

The path grammar is the ChangeSet one (``mde_compass.domain.changes``): dict sections are addressed
``<section>.<key>`` (``idea.summary``, ``experience.brand.name``), list sections address a record by
its key and optionally a field (``scope.FTR-004``, ``scope.FTR-004.priority``). Records without an
``id`` field are keyed by their natural name: integrations by ``name``, glossary by ``term``,
references by ``label``, declined by ``what``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, JsonValue

from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.domain.brief import DICT_SECTIONS, ID_FIELD_BY_SECTION, LIST_SECTIONS, Brief
from mde_compass.domain.common import BriefItem, ItemMeta

# Natural key field of the list sections that have no ``id``.
NATURAL_KEY_FIELD: dict[str, str] = {
    "integrations": "name",
    "glossary": "term",
    "references": "label",
    "declined": "what",
}
KEY_FIELD: dict[str, str] = {**ID_FIELD_BY_SECTION, **NATURAL_KEY_FIELD}

# Which Brief section lands in which view section (fixed frame; ``context.geo`` is "who it is for").
_VIEW_SECTION_BY_ROOT: dict[str, str] = {
    "idea": "idea",
    "context": "idea",
    "audience": "who",
    "apps": "apps_roles",
    "roles": "apps_roles",
    "scope": "features",
    "references": "look",
    "experience": "look",
    "access": "access",
    "integrations": "integrations_data",
    "data": "integrations_data",
    "constraints": "limits",
    "glossary": "words",
    "assumptions": "assumed",
    "open_questions": "unknown",
    "research_requests": "lookup",
    "sources": "sources",
    "assets": "sources",
    "declined": "cannot_build",
}

TURN_ID_RE = re.compile(r"^(evt_[A-Za-z0-9_]+)")

JsonDict = dict[str, JsonValue]


def view_section_for_path(path: str) -> str | None:
    """The view section id a ChangeSet-grammar path belongs to (None for bookkeeping paths)."""
    root, _, rest = path.partition(".")
    if root == "context" and rest.split(".", 1)[0] == "geo":
        return "who"
    if root == "summary" or root == "pitch":
        return "idea"
    return _VIEW_SECTION_BY_ROOT.get(root)


def record_key(section: str, record: BaseModel) -> str:
    """The id (or natural key) a list record is addressed by."""
    return str(getattr(record, KEY_FIELD.get(section, "id"), "") or "")


def iter_records(brief: Brief, section: str) -> Iterator[tuple[str, Any]]:
    """Yield ``(key, record)`` for a list section."""
    for record in getattr(brief, section):
        yield record_key(section, record), record


def json_dump(model: BaseModel) -> JsonDict:
    return model.model_dump(mode="json")


# ---------------------------------------------------------------------------------------------
# Dict sections: which key holds a catalog slot
# ---------------------------------------------------------------------------------------------


def slot_key_candidates(slot: SlotDef) -> tuple[str, list[str]]:
    """``(section, [dict keys])`` where a slot's value may live, most likely first."""
    root, _, rest = slot.brief_path.partition(".")
    keys = [rest] if rest else []
    for name in (slot.id, rest):
        if name:
            keys.append(name)
            keys.append(name.replace(".", "_"))
    keys.append(slot.id.split(".", 1)[-1])
    seen: list[str] = []
    for key in keys:
        if key and key not in seen:
            seen.append(key)
    return root, seen


def find_dict_item(
    section: Mapping[str, BriefItem], candidates: list[str]
) -> tuple[str, BriefItem] | None:
    for key in candidates:
        if key in section:
            return key, section[key]
    return None


def slot_item(brief: Brief, slot: SlotDef) -> tuple[str, str, BriefItem] | None:
    """``(section, key, item)`` of a slot in a dict section, or None."""
    root, keys = slot_key_candidates(slot)
    sections = [root] if root in DICT_SECTIONS else list(DICT_SECTIONS)
    for name in sections:
        found = find_dict_item(getattr(brief, name), keys)
        if found is not None:
            return name, found[0], found[1]
    return None


def slot_value(brief: Brief, slot: SlotDef) -> JsonValue:
    """The current value of a catalog slot in the Brief (None when empty)."""
    root = slot.brief_path.partition(".")[0]
    if root in LIST_SECTIONS and slot.brief_path == root:
        records = getattr(brief, root)
        return [json_dump(r) for r in records] if records else None
    found = slot_item(brief, slot)
    if found is None:
        return None
    item = found[2]
    return item.value if item.has_value() else None


def is_empty_value(value: JsonValue) -> bool:
    return value is None or value == "" or value == [] or value == {}


def slot_filled(brief: Brief, slot: SlotDef) -> bool:
    return not is_empty_value(slot_value(brief, slot))


def slot_label(catalog: Catalog, slot_id: str, language: str) -> str:
    slot = catalog.slots.get(slot_id)
    return slot.label.get(language) if slot else slot_id


def slot_for_key(catalog: Catalog, section: str, key: str) -> SlotDef | None:
    """The catalog slot a dict item ``<section>.<key>`` stands for (None for unknown keys)."""
    for slot in catalog.slots.values():
        root, keys = slot_key_candidates(slot)
        if root == section and key in keys:
            return slot
    return None


# ---------------------------------------------------------------------------------------------
# Resolving an item path to its model
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedItem:
    """A path resolved to its item: ``meta`` carries tier/provenance/history, ``value`` the value."""

    path: str
    section: str
    meta: ItemMeta
    value: JsonValue
    field: str | None = None


def _record_value(record: BaseModel, field: str | None) -> JsonValue:
    dumped = json_dump(record)
    if field is None:
        for meta_key in ("history", "provenance", "confidence"):
            dumped.pop(meta_key, None)
        return dumped
    return dumped.get(field)


def resolve_item(brief: Brief, path: str) -> ResolvedItem | None:
    """Resolve ``path`` (ChangeSet grammar) to an item, or None when it does not exist."""
    root, _, rest = path.partition(".")
    if root in DICT_SECTIONS:
        section = getattr(brief, root)
        if rest in section:
            item = section[rest]
            return ResolvedItem(path, root, item, item.value)
        # ``experience.brand.name.tier``: a trailing field name on a dict item
        head, _, tail = rest.rpartition(".")
        if head in section:
            item = section[head]
            value = {"tier": item.tier, "status": item.status}.get(tail, item.value)
            return ResolvedItem(path, root, item, value, tail)
        return None
    if root in LIST_SECTIONS and rest:
        for key, record in iter_records(brief, root):
            if not key:
                continue
            if rest == key:
                return ResolvedItem(path, root, record, _record_value(record, None))
            if rest.startswith(key + "."):
                field = rest[len(key) + 1 :]
                if field in type(record).model_fields:
                    return ResolvedItem(path, root, record, _record_value(record, field), field)
    return None
