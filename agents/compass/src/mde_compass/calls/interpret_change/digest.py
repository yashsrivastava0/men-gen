"""Build the compact Brief digest the interpreter sees: only the targeted items, with tier and valid values.

Pure functions over the ``Brief`` and the catalog. The digest is also what the adapter uses to compute
``touches_locked`` in code, whatever the model says.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from mde_compass.calls.interpret_change.schema import DigestItem
from mde_compass.calls.interpret_change.validate import (
    ADD_SECTIONS,
    FIELD_KINDS,
    NAME_FIELD,
    owner_item,
    parse_path,
)
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import DICT_SECTIONS, Brief
from mde_compass.domain.common import BriefItem
from mde_compass.engine.ports import ResolvedTarget

MAX_ITEMS = 30
MAX_TEXT = 200
MAX_LIST = 12
RECORD_SECTIONS: tuple[str, ...] = tuple(NAME_FIELD)
ID_PREFIX_SECTION = {"FTR": "scope", "APP": "apps", "ROL": "roles", "ASM": "assumptions"}
SECTION_ALIASES = {"features": "scope", "assumed": "assumptions", "we_assumed": "assumptions"}


def shrink(value: Any) -> Any:
    """A JSON value cut down so it stays valid JSON and short (long texts and lists are trimmed)."""
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, list):
        return [shrink(v) for v in value[:MAX_LIST]]
    if isinstance(value, dict):
        return {str(k): shrink(v) for k, v in list(value.items())[:MAX_LIST]}
    return value


def dumps(value: Any) -> str:
    return json.dumps(shrink(value), ensure_ascii=False, sort_keys=True)


def _slot_by_path(catalog: Catalog) -> dict[str, Any]:
    return {s.brief_path: s for s in catalog.slots.values()}


def _dict_item(section: str, key: str, item: BriefItem, catalog: Catalog, lang: str) -> DigestItem:
    path = f"{section}.{key}"
    slot = _slot_by_path(catalog).get(path)
    return DigestItem(
        path=path,
        label=slot.label.get(lang) if slot else key.replace("_", " "),
        value_json=dumps(item.value),
        tier=item.tier,
        status=item.status,
        value_kind=slot.value_schema.kind if slot else "text",
        options=list(slot.value_schema.options) if slot else [],
    )


def _record_item(section: str, rec: BaseModel) -> DigestItem:
    data = rec.model_dump(mode="json")
    fields = {f: data.get(f) for f in FIELD_KINDS[section]}
    return DigestItem(
        path=f"{section}.{data['id']}",
        label=str(data.get(NAME_FIELD[section]) or data["id"])[:MAX_TEXT],
        value_json=dumps(fields),
        tier=data["tier"],
        status=data["status"],
        value_kind="record",
        options=[],
    )


def _section_items(brief: Brief, section: str, catalog: Catalog, lang: str) -> list[DigestItem]:
    if section in DICT_SECTIONS:
        return [
            _dict_item(section, key, item, catalog, lang)
            for key, item in getattr(brief, section).items()
            if item.has_value()
        ]
    if section in RECORD_SECTIONS:
        return [_record_item(section, rec) for rec in getattr(brief, section)]
    return []


def _resolve_path(target_id: str, catalog: Catalog) -> str:
    tid = target_id.strip()
    if tid in catalog.slots:
        return catalog.slots[tid].brief_path
    if "." not in tid and "-" in tid:
        section = ID_PREFIX_SECTION.get(tid.split("-", 1)[0].upper())
        if section:
            return f"{section}.{tid.upper()}"
    return tid


def target_items(
    brief: Brief, target: ResolvedTarget, catalog: Catalog, lang: str = "en"
) -> list[DigestItem]:
    """The digest items for a target: a whole section, one row, or (unknown) a broad overview."""
    kind, tid = target.kind, target.id.strip()
    section = SECTION_ALIASES.get(tid, tid)
    if kind == "unknown" or not tid:
        out: list[DigestItem] = []
        for sec in (*RECORD_SECTIONS[:3], *DICT_SECTIONS):
            out.extend(_section_items(brief, sec, catalog, lang))
        return out[:MAX_ITEMS]
    if kind == "section" or (section in (*DICT_SECTIONS, *RECORD_SECTIONS) and "." not in tid):
        return _section_items(brief, section, catalog, lang)[:MAX_ITEMS]
    pp = parse_path(_resolve_path(tid, catalog))
    if pp is None:
        return []
    candidates = _section_items(brief, pp.section, catalog, lang)
    path = _resolve_path(tid, catalog)
    wanted = {i.path: i for i in candidates}
    found = owner_item(wanted, path)
    return [found] if found else []


def add_sections_for(target: ResolvedTarget, catalog: Catalog) -> list[str]:
    """List sections where the change may add a new record."""
    tid = target.id.strip()
    if target.kind == "unknown" or not tid:
        return list(ADD_SECTIONS)
    section = SECTION_ALIASES.get(tid, tid)
    if "." not in tid and section in ADD_SECTIONS and target.kind == "section":
        return [section]
    pp = parse_path(_resolve_path(tid, catalog))
    return [pp.section] if pp and pp.section in ADD_SECTIONS else []


def locked_paths(ops: list[tuple[str, str]], items: list[DigestItem]) -> list[str]:
    """Paths of Locked digest items touched by ``(op, path)`` pairs (an ``add`` never touches one)."""
    by_path = {i.path: i for i in items}
    found: list[str] = []
    for op, path in ops:
        if op == "add":
            continue
        owner = owner_item(by_path, path)
        if owner is not None and owner.tier == "Locked" and owner.path not in found:
            found.append(owner.path)
    return found
