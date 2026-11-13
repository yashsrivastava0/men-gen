"""Brief validation (code, tested): the rules a Brief must satisfy before it is saved or shown.

* tier rule: a ``Locked`` item without evidence is an error (``tier_errors``);
* ids are unique, never tombstoned, and never ahead of the ledger counters (so they cannot be reused);
* list references are valid: a feature's ``apps`` and an app's ``roles`` point at live records;
* a capability tier C feature cannot be ``in`` scope.

Plus ``must_slots_missing``: which ``must`` slots still have no value (respecting ``ask_if``).
"""

from __future__ import annotations

from typing import Any

from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import DICT_SECTIONS, ID_FIELD_BY_SECTION, SECTION_KEYS, Brief
from mde_compass.domain.common import ItemMeta, tier_errors
from mde_compass.domain.ids import parse_id
from mde_compass.engine.brief_builder import dict_targets
from mde_compass.engine.expr import holds
from mde_compass.engine.slots import SlotStore

_PREFIX_BY_SECTION = {
    "apps": "APP",
    "roles": "ROL",
    "scope": "FTR",
    "modules": "MOD",
    "screens": "SCR",
    "flows": "FLW",
    "entities": "ENT",
}


def _tier_problems(brief: Brief) -> list[str]:
    errors: list[str] = []
    for section in DICT_SECTIONS:
        for key, item in getattr(brief, section).items():
            errors += tier_errors(item, has_value=item.has_value(), where=f"{section}.{key}")
    for section in SECTION_KEYS:
        if section in DICT_SECTIONS:
            continue
        for i, rec in enumerate(getattr(brief, section)):
            if isinstance(rec, ItemMeta):
                where = f"{section}.{getattr(rec, 'id', i)}"
                errors += tier_errors(rec, has_value=True, where=where)
    return errors


def _id_problems(brief: Brief) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for section in ID_FIELD_BY_SECTION:
        for rec in getattr(brief, section):
            rid: str = getattr(rec, "id", "")
            if rid in seen:
                errors.append(f"{section}: duplicate id {rid}")
            seen.add(rid)
            if brief.ids.is_deleted(rid):
                errors.append(f"{section}: id {rid} is in the tombstones (ids are never reused)")
            parsed = parse_id(rid)
            if parsed is None:
                errors.append(f"{section}: malformed id {rid!r}")
            elif parsed[1] > brief.ids.counters.get(parsed[0], 0):
                errors.append(f"{section}: id {rid} is ahead of the id ledger")
            expected = _PREFIX_BY_SECTION.get(section)
            if parsed and expected and parsed[0] != expected:
                errors.append(f"{section}: id {rid} should start with {expected}-")
    return errors


def _reference_problems(brief: Brief) -> list[str]:
    errors: list[str] = []
    app_ids = {a.id for a in brief.apps}
    role_ids = {r.id for r in brief.roles}
    for app in brief.apps:
        for ref in app.roles:
            if ref not in role_ids:
                errors.append(f"apps.{app.id}: role {ref!r} does not exist")
    for feature in brief.scope:
        for ref in feature.apps:
            if ref not in app_ids:
                errors.append(f"scope.{feature.id}: app {ref!r} does not exist")
        if feature.capability_tier == "C" and feature.scope_status == "in":
            errors.append(f"scope.{feature.id}: capability tier C cannot be in scope")
    return errors + _structure_reference_problems(brief)


def _structure_reference_problems(brief: Brief) -> list[str]:
    """The structure points only at records that exist (the deeper logic, such as who may open a step, is
    ``engine.structure_checks``)."""
    errors: list[str] = []
    ids = {
        "app": {a.id for a in brief.apps},
        "role": {r.id for r in brief.roles},
        "feature": {f.id for f in brief.scope},
        "module": {m.id for m in brief.modules},
        "screen": {s.id for s in brief.screens},
        "entity": {e.id for e in brief.entities},
    }

    def check(where: str, kind: str, refs: list[str]) -> None:
        for ref in refs:
            if ref and ref not in ids[kind]:
                errors.append(f"{where}: {kind} {ref!r} does not exist")

    for module in brief.modules:
        where = f"modules.{module.id}"
        check(where, "app", module.apps)
        check(where, "feature", module.features)
        check(where, "role", module.roles)
        check(where, "screen", [module.entry])
    for screen in brief.screens:
        where = f"screens.{screen.id}"
        check(where, "module", [screen.module])
        check(where, "app", screen.apps)
        check(where, "role", screen.roles)
        check(where, "feature", screen.serves)
        check(where, "entity", [use.entity for use in screen.entities])
    for flow in brief.flows:
        where = f"flows.{flow.id}"
        check(where, "role", [flow.role])
        check(where, "feature", flow.features)
        check(where, "screen", [step.screen for step in flow.steps])
    for entity in brief.entities:
        where = f"entities.{entity.id}"
        check(where, "module", [entity.module])
        check(where, "role", list(entity.access))
        check(where, "entity", [f.ref for f in entity.fields])
    return errors


def validate_brief(brief: Brief, catalog: Catalog) -> list[str]:
    """All rule violations in the Brief (empty list: valid). Deterministic order."""
    errors = _tier_problems(brief) + _id_problems(brief) + _reference_problems(brief)
    allowed: dict[str, set[str]] = {s: set() for s in DICT_SECTIONS}
    for section, key in dict_targets(catalog):
        allowed[section].add(key)
    for section in DICT_SECTIONS:
        for key in getattr(brief, section):
            if key not in allowed[section]:
                errors.append(f"{section}.{key}: not a catalog field")
    return errors


def must_slots_missing(store: SlotStore, catalog: Catalog, *, mode: str) -> list[str]:
    """Ids of ``must`` slots that are empty and apply now (their ``ask_if`` holds), in catalog order.

    Company-only (``org``) slots count only in enterprise mode.
    """
    records: dict[str, Any] = store.all()
    missing: list[str] = []
    for slot in catalog.slots.values():
        if slot.level != "must" or (slot.group == "org" and mode != "enterprise"):
            continue
        if not holds(slot.ask_if, records):
            continue
        if not store.filled(slot.id):
            missing.append(slot.id)
    return missing
