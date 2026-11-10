"""Slots to Brief (and back): the deterministic mapping of ``catalog/slots.yaml`` onto ``brief.json``.

* ``brief_path`` decides where a slot lands. Dict sections (``idea``, ``context``, ``audience``,
  ``experience``, ``access``, ``data``, ``constraints``) get a ``BriefItem`` keyed by everything after
  the section (``experience.brand.name`` -> key ``brand.name``). List slots become typed records
  (``apps``, ``roles``, ``scope``, ``references``, ``integrations``, ``glossary``).
* Ids come from the ledger that travels in the Brief and are stable: a record is matched by ``id``
  (when the slot value carries one) or by name, case-insensitively; a record that disappears is
  tombstoned and its id is never reused.
* Every item copies tier, provenance, confidence, status and history from its slot record; the tier
  is re-clamped with ``max_tier`` so the evidence rule holds whatever the slot says.
* ``apply_slots`` is idempotent and never mutates its input.

Paths that the Brief schema has no home for are mapped here (listed in ``_ORG_SECTION`` and
``scope.priority`` / ``scope.must_not`` -> ``constraints``); see the engine-a report for the
requested contract change.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import JsonValue, ValidationError

from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.domain.brief import (
    DICT_SECTIONS,
    AppRecord,
    Brief,
    BriefMeta,
    FeatureRecord,
    GlossaryRecord,
    IntegrationRecord,
    ReferenceRecord,
    RoleRecord,
)
from mde_compass.domain.common import BriefItem, ItemMeta, Provenance, max_tier
from mde_compass.domain.ids import IdLedger
from mde_compass.domain.slots import SlotRecord
from mde_compass.engine.slots import SlotStore
from mde_core.clock import Clock

META_FIELDS: frozenset[str] = frozenset(ItemMeta.model_fields)
_RANK = {"empty": 0, "assumed": 1, "volunteered": 2, "disputed": 2, "confirmed": 3}
_TIER_RANK = {"Open": 0, "Default": 1, "Locked": 2}
_ORG_SECTION = {
    "profile": "context",
    "approvers": "context",
    "design_system": "experience",
    "api_catalog": "data",
}
_PREF_FROM_MODE = {"mock_first": "real_later", "mock_only": "mock", "real_from_start": "undecided"}
_MODE_FROM_PREF = {"real_later": "mock_first", "mock": "mock_only"}
_REAL_NOTE = "real integration from the start"
LOCALES_SLOT = "locales"
INTEGRATION_MODE_SLOT = "integrations.mode"


@dataclass(frozen=True)
class Target:
    """Where one slot lands in the Brief."""

    kind: Literal["dict", "list", "integration_mode", "skip"]
    section: str = ""
    key: str = ""


@dataclass(frozen=True)
class ListSpec:
    section: str
    model: type[ItemMeta]
    prefix: str | None
    name_field: str
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def fields(self) -> list[str]:
        return [f for f in self.model.model_fields if f not in META_FIELDS and f != "id"]


LIST_SPECS: dict[str, ListSpec] = {
    s.section: s
    for s in (
        ListSpec("apps", AppRecord, "APP", "name"),
        ListSpec(
            "roles",
            RoleRecord,
            "ROL",
            "name_in_user_words",
            {"name": "name_in_user_words", "permissions": "rough_permissions"},
        ),
        ListSpec(
            "scope",
            FeatureRecord,
            "FTR",
            "name",
            {"status": "scope_status", "tier": "capability_tier"},
        ),
        ListSpec("references", ReferenceRecord, None, "label"),
        ListSpec("integrations", IntegrationRecord, None, "name"),
        ListSpec("glossary", GlossaryRecord, None, "term"),
    )
}


def target_of(slot: SlotDef) -> Target:
    """The Brief location of a slot, from its ``brief_path`` (data, with the few documented fixes)."""
    section, _, rest = slot.brief_path.partition(".")
    if section in DICT_SECTIONS:
        return Target("dict", section, rest or slot.id)
    if section == "meta" and rest.startswith("org."):
        name = rest[len("org.") :]
        return Target("dict", _ORG_SECTION.get(name, "context"), rest)
    if section == "scope" and rest:
        return Target("dict", "constraints", f"scope_{rest}")
    if section == "integrations" and rest == "preference":
        return Target("integration_mode")
    if section in LIST_SPECS and not rest:
        return Target("list", section)
    return Target("skip")


def dict_targets(catalog: Catalog) -> dict[tuple[str, str], str]:
    """(section, key) -> slot id for every dict-section slot."""
    out: dict[tuple[str, str], str] = {}
    for slot in catalog.slots.values():
        t = target_of(slot)
        if t.kind == "dict":
            out[(t.section, t.key)] = slot.id
    return out


def empty_brief(session_id: str, mode: str, locale: str) -> Brief:
    """A new Brief: version 0, no content, the chat locale as the first locale."""
    meta = BriefMeta.model_validate({"session_id": session_id, "mode": mode, "locales": [locale]})
    return Brief(meta=meta)


# ----------------------------------------------------------------------------------- helpers


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _clamp(meta: ItemMeta, *, has_value: bool) -> ItemMeta:
    """Never trust a tier: it cannot exceed what the evidence allows."""
    allowed = max_tier(has_value=has_value, provenance=meta.provenance)
    if _TIER_RANK[meta.tier] > _TIER_RANK[allowed]:
        meta = meta.model_copy(update={"tier": allowed})
    if meta.status == "assumed" and meta.tier == "Locked":
        meta = meta.model_copy(update={"tier": "Default"})
    return meta


def _meta_of(src: ItemMeta) -> dict[str, Any]:
    return {
        "tier": src.tier,
        "provenance": [p.model_copy(deep=True) for p in src.provenance],
        "confidence": src.confidence,
        "status": src.status,
        "history": [h.model_copy(deep=True) for h in src.history],
    }


def _item_meta(raw: Any, slot: SlotRecord) -> ItemMeta:
    """The per-record meta carried in a slot item (``_meta``), else the slot's own."""
    if isinstance(raw, dict):
        try:
            return ItemMeta.model_validate(raw)
        except ValidationError:
            pass
    return ItemMeta.model_validate(_meta_of(slot))


def _item_dicts(value: JsonValue, spec: ListSpec) -> list[dict[str, Any]]:
    """Normalise a list-slot value to dicts keyed by model field names."""
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    out: list[dict[str, Any]] = []
    for raw in raw_items:
        if isinstance(raw, dict):
            item: dict[str, Any] = {}
            for k, v in raw.items():
                if k == "tier" and v in ("Locked", "Default", "Open"):
                    continue
                item[spec.aliases.get(k, k)] = v
        elif isinstance(raw, str | int | float) and not isinstance(raw, bool):
            item = {spec.name_field: str(raw)}
        else:
            continue
        out.append(item)
    return out


def _build_record(spec: ListSpec, data: dict[str, Any]) -> ItemMeta:
    """Validate a record, dropping fields the model rejects (slot values may be imperfect)."""
    data = dict(data)
    for _ in range(4):
        try:
            return spec.model.model_validate(data)
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e["loc"]}
            droppable = bad - {"id", spec.name_field}
            if not droppable:
                raise
            for name in droppable:
                data.pop(name, None)
    return spec.model.model_validate(data)


def _name_of(item: dict[str, Any], spec: ListSpec) -> str:
    raw = item.get(spec.name_field)
    return raw.strip() if isinstance(raw, str) else ""


def _sync_list(
    brief: Brief,
    spec: ListSpec,
    rec: SlotRecord,
    ledger: IdLedger,
    mode_pref: str | None,
) -> list[Any]:
    existing: list[Any] = list(getattr(brief, spec.section))
    by_id = {r.id: r for r in existing if hasattr(r, "id")}
    by_name = {str(getattr(r, spec.name_field)).strip().casefold(): r for r in existing}
    claimed: set[int] = set()
    seen: set[str] = set()
    out: list[Any] = []
    items = _item_dicts(rec.value, spec) if _filled(rec) else []
    for item in items:
        name = _name_of(item, spec)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        old = by_id.get(str(item.get("id"))) if item.get("id") else None
        if old is None:
            old = by_name.get(name.casefold())
        if old is not None and id(old) in claimed:
            old = None
        base: dict[str, Any] = {}
        if old is not None:
            claimed.add(id(old))
            base = old.model_dump(mode="python", exclude=META_FIELDS)
        fields = {k: v for k, v in item.items() if k in spec.fields}
        meta = _item_meta(item.get("_meta"), rec)
        data: dict[str, Any] = {**base, **fields, spec.name_field: name, **_meta_of(meta)}
        if spec.prefix:
            data["id"] = old.id if old is not None else ledger.allocate(spec.prefix)
        if spec.section == "integrations" and mode_pref and "preference" not in fields:
            data["preference"] = mode_pref
            data["notes"] = _REAL_NOTE if mode_pref == "undecided" else base.get("notes", "")
        record = _build_record(spec, data)
        record = record.model_copy(update=_meta_of(_clamp(record, has_value=True)))
        out.append(record)
    if spec.prefix:
        for old in existing:
            if id(old) not in claimed and old.id not in {r.id for r in out}:
                ledger.tombstone(old.id)
    return out


def _filled(rec: SlotRecord | None) -> bool:
    return rec is not None and rec.status != "empty" and not _blank(rec.value)


def locales_of(value: JsonValue) -> list[str]:
    """``meta.locales`` from the locales slot: chat language first, then app languages."""
    found: list[str] = []

    def add(v: Any) -> None:
        if isinstance(v, str) and v.strip() and v.strip() not in found:
            found.append(v.strip())
        elif isinstance(v, list):
            for x in v:
                add(x)

    if isinstance(value, dict):
        add(value.get("chat"))
        add(value.get("app"))
    else:
        add(value)
    return found


def _resolve_refs(brief: Brief) -> None:
    """Turn names in ``app.roles`` / ``feature.apps`` into ids where a live record matches."""
    role_by_name = {r.name_in_user_words.strip().casefold(): r.id for r in brief.roles}
    role_ids = {r.id for r in brief.roles}
    app_by_name = {a.name.strip().casefold(): a.id for a in brief.apps}
    app_ids = {a.id for a in brief.apps}

    def resolve(refs: list[str], ids: set[str], names: dict[str, str]) -> list[str]:
        out: list[str] = []
        for ref in refs:
            new = ref if ref in ids else names.get(ref.strip().casefold(), ref)
            if new not in out:
                out.append(new)
        return out

    from mde_compass.engine.fact_projector import _same_element

    for app in brief.apps:
        app.roles = resolve(app.roles, role_ids, role_by_name)
        if (
            not app.roles
        ):  # an app named after its people ("Members") belongs to the role of that name
            match = next(
                (
                    r
                    for r in brief.roles
                    if _same_element({"name": app.name}, {"name": r.name_in_user_words})
                ),
                None,
            )
            if match is not None:
                app.roles = [match.id]
    for feature in brief.scope:
        feature.apps = resolve(feature.apps, app_ids, app_by_name)


# ----------------------------------------------------------------------------------- slots -> Brief


def apply_slots(brief: Brief, store: SlotStore, catalog: Catalog) -> Brief:
    """A NEW Brief with every slot in ``store`` mapped to its ``brief_path`` (input untouched).

    Slots the store has never seen leave their section as it is; a slot that was cleared removes its
    item or records (tombstoning list ids).
    """
    out = brief.model_copy(deep=True)
    ledger = out.ids
    mode_rec = store.get(INTEGRATION_MODE_SLOT)
    mode_pref = _PREF_FROM_MODE.get(str(mode_rec.value)) if _filled(mode_rec) and mode_rec else None
    for slot in catalog.slots.values():
        rec: SlotRecord | None = store.get(slot.id)
        if rec is None:
            continue
        target = target_of(slot)
        if target.kind == "dict":
            section: dict[str, BriefItem] = getattr(out, target.section)
            if _filled(rec):
                item = BriefItem(value=rec.value, **_meta_of(rec))
                clamped = _clamp(item, has_value=item.has_value())
                section[target.key] = BriefItem(value=item.value, **_meta_of(clamped))
            else:
                section.pop(target.key, None)
            if slot.id == LOCALES_SLOT and _filled(rec):
                out.meta.locales = locales_of(rec.value)
        elif target.kind == "list":
            spec = LIST_SPECS[target.section]
            pref = mode_pref if spec.section == "integrations" else None
            setattr(out, spec.section, _sync_list(out, spec, rec, ledger, pref))
    _resolve_refs(out)
    return out


# ----------------------------------------------------------------------------------- Brief -> slots


def _aggregate(metas: list[ItemMeta]) -> dict[str, Any]:
    """One slot-level meta for several records: the weakest status, tier and confidence."""
    provenance: list[Provenance] = []
    for m in metas:
        for p in m.provenance:
            if p not in provenance:
                provenance.append(p)
    return {
        "tier": min((m.tier for m in metas), key=lambda t: _TIER_RANK[t]),
        "status": min((m.status for m in metas), key=lambda s: _RANK[s]),
        "confidence": min(m.confidence for m in metas),
        "provenance": provenance,
        "history": list(metas[0].history),
    }


def _stamp(provenance: list[Provenance], now: datetime) -> list[Provenance]:
    return [p if p.date else p.model_copy(update={"date": now}) for p in provenance]


def slots_from_brief(brief: Brief, catalog: Catalog, clock: Clock) -> list[SlotRecord]:
    """The inverse of :func:`apply_slots`: slot records for everything the Brief holds.

    Used after a patch or undo so the slot store matches the Brief again. List slots carry the
    record ids and each record's own meta (``_meta``), so ``apply_slots`` restores them exactly.
    """
    now = clock.now()
    out: list[SlotRecord] = []
    for slot in catalog.slots.values():
        target = target_of(slot)
        if target.kind == "dict":
            item: BriefItem | None = getattr(brief, target.section).get(target.key)
            if item is None or not item.has_value():
                continue
            meta = _meta_of(item)
            meta["provenance"] = _stamp(meta["provenance"], now)
            out.append(SlotRecord(slot_id=slot.id, value=item.value, **meta))
        elif target.kind == "list":
            spec = LIST_SPECS[target.section]
            records: list[Any] = list(getattr(brief, spec.section))
            if not records:
                continue
            mode = _derived_mode(brief) if spec.section == "integrations" else None
            values: list[JsonValue] = []
            for r in records:
                d = r.model_dump(mode="json", exclude=META_FIELDS)
                if (
                    spec.section == "integrations"
                    and mode
                    and _PREF_FROM_MODE.get(mode) == d.get("preference")
                ):
                    d.pop("preference", None)
                    if d.get("notes") == _REAL_NOTE:
                        d.pop("notes", None)
                d["_meta"] = r.model_dump(mode="json", include=META_FIELDS)
                values.append(d)
            meta = _aggregate(list(records))
            meta["provenance"] = _stamp(meta["provenance"], now)
            out.append(SlotRecord(slot_id=slot.id, value=values, **meta))
        elif target.kind == "integration_mode":
            mode = _derived_mode(brief)
            if mode and brief.integrations:
                meta = _aggregate(list(brief.integrations))
                meta["provenance"] = _stamp(meta["provenance"], now)
                out.append(SlotRecord(slot_id=slot.id, value=mode, **meta))
    return out


def _derived_mode(brief: Brief) -> str | None:
    """The ``integrations.mode`` value the integration records imply (the most common preference)."""
    counts: dict[str, int] = {}
    for r in brief.integrations:
        if r.preference in _MODE_FROM_PREF:
            mode = _MODE_FROM_PREF[r.preference]
        elif r.preference == "undecided" and r.notes == _REAL_NOTE:
            mode = "real_from_start"
        else:
            continue
        counts[mode] = counts.get(mode, 0) + 1
    return max(counts, key=lambda m: counts[m]) if counts else None


def known_ids(brief: Brief) -> Iterable[str]:
    """Every live id in the Brief (used by validation and tests)."""
    for section in (
        "apps",
        "roles",
        "scope",
        "modules",
        "screens",
        "flows",
        "entities",
        "assumptions",
        "open_questions",
        "assets",
        "sources",
    ):
        for r in getattr(brief, section):
            yield r.id
