"""The patch engine: apply ``PatchOp``s to a Brief on a copy, validate, and build revertible change sets.

Path grammar (``mde_compass.domain.changes``): ``<section>.<key>`` for dict sections
(``idea.summary``, ``experience.brand.name``) and ``<section>.<ID>[.<field>]`` for list sections
(``scope.FTR-004.priority``). Sections without ids (references, integrations, glossary, declined) are
addressed by their name instead (``glossary.mithai``).

Operations (what the applied ops and the inverse ops mean exactly, so undo restores the content hash):

* ``set`` a dict key, a record field or a whole record. A user edit writes provenance of the actor
  (``user_text`` unless given) with ``ref = turn_id``, status ``confirmed`` (``assumed`` for a non-user
  actor), the tier from ``max_tier``, and pushes the old value into ``history`` (with a reason that says
  when a Locked item was touched). A non-user actor can never overwrite a user-given value.
* ``add`` appends a record (allocating its id from the ledger) or, on a dict section, puts a whole item;
  a value that is a full item/record dump (it has ``provenance``) is stored as is, which is how undo
  restores an item exactly. On a list, ``old_value`` of an ``add`` is the insert position (applied ops
  report it). An explicit id is accepted only to restore a tombstoned id (its tombstone is removed).
* ``remove`` deletes a record (tombstoning its id, the id is never reused) or a dict key.
* ``clear`` empties a dict key (explicit negation) or resets a record field to its default.

Everything is applied on a copy; any error means nothing is applied and ``PatchResult.brief`` is the
unchanged input. ``inverse_ops`` are in application order (already reversed); applying them as a NEW
change set is undo, history is never rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticUndefined

from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import (
    DICT_SECTIONS,
    ID_FIELD_BY_SECTION,
    SECTION_KEYS,
    AppRecord,
    AssetRecord,
    AssumptionRecord,
    Brief,
    DeclinedRecord,
    FeatureRecord,
    GlossaryRecord,
    IntegrationRecord,
    OpenQuestion,
    ReferenceRecord,
    RoleRecord,
    SourceRecord,
)
from mde_compass.domain.changes import ChangeSet, ChangeSource, PatchOp
from mde_compass.domain.common import (
    EVIDENCE_TYPES,
    BriefItem,
    HistoryEntry,
    ItemMeta,
    Provenance,
    ProvenanceType,
    max_tier,
)
from mde_compass.domain.hashing import changed_sections, section_hashes
from mde_compass.domain.ids import ITEM_ID_RE, parse_id
from mde_compass.domain.research import ResearchRequest
from mde_compass.engine.brief_builder import META_FIELDS, dict_targets, locales_of
from mde_compass.engine.validate import validate_brief

RECORD_MODELS: dict[str, type[BaseModel]] = {
    "apps": AppRecord,
    "roles": RoleRecord,
    "scope": FeatureRecord,
    "references": ReferenceRecord,
    "integrations": IntegrationRecord,
    "glossary": GlossaryRecord,
    "assumptions": AssumptionRecord,
    "open_questions": OpenQuestion,
    "research_requests": ResearchRequest,
    "assets": AssetRecord,
    "sources": SourceRecord,
    "declined": DeclinedRecord,
}
NAME_FIELD: dict[str, str] = {
    "apps": "name",
    "roles": "name_in_user_words",
    "scope": "name",
    "references": "label",
    "integrations": "name",
    "glossary": "term",
    "declined": "what",
}
PREFIX_BY_SECTION: dict[str, str] = {
    "apps": "APP",
    "roles": "ROL",
    "scope": "FTR",
    "assumptions": "ASM",
    "sources": "SRC",
    "research_requests": "RR",
    "open_questions": "Q",
}
_USER_GIVEN = frozenset({"volunteered", "confirmed", "disputed"})
_LIST_KINDS = frozenset({"text_list", "enum_list", "object_list"})


class PatchError(ValueError):
    """One op cannot be applied (collected into ``PatchResult.errors``)."""


@dataclass
class PatchResult:
    """The outcome of ``apply_ops``. On errors ``brief`` is the unchanged input and ``ops`` are empty."""

    brief: Brief
    ops: list[PatchOp] = field(default_factory=list)
    inverse_ops: list[PatchOp] = field(default_factory=list)
    voided_sections: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class _Ctx:
    work: Brief
    catalog: Catalog
    now: datetime
    turn_id: str | None
    actor: ProvenanceType
    keys: dict[tuple[str, str], str]

    @property
    def evidence_actor(self) -> bool:
        return self.actor in EVIDENCE_TYPES

    def provenance(self, op: PatchOp) -> list[Provenance]:
        if op.provenance:
            return [
                p.model_copy(
                    update={"ref": p.ref or (self.turn_id or ""), "date": p.date or self.now}
                )
                for p in op.provenance
            ]
        return [Provenance(type=self.actor, ref=self.turn_id or "", date=self.now)]

    def meta(self, provenance: list[Provenance], history: list[HistoryEntry]) -> dict[str, Any]:
        """Item meta for an edit by the actor: user evidence -> confirmed, otherwise assumed."""
        evidence = any(p.is_evidence() for p in provenance) and self.evidence_actor
        status = "confirmed" if self.evidence_actor else "assumed"
        tier = max_tier(has_value=True, provenance=provenance)
        if status == "assumed" and tier == "Locked":
            tier = "Default"
        return {
            "tier": tier,
            "provenance": provenance,
            "confidence": 1.0 if evidence else 0.5,
            "status": status,
            "history": history,
        }


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _is_item_dump(value: Any) -> bool:
    return isinstance(value, dict) and "value" in value and "provenance" in value


def _check_value(kind: str, value: Any, where: str) -> None:
    bad = (
        (kind in ("text", "enum") and not isinstance(value, str))
        or (kind in _LIST_KINDS and not isinstance(value, list))
        or (kind == "object" and not isinstance(value, dict))
        or (kind == "bool" and not isinstance(value, bool))
    )
    if bad:
        raise PatchError(f"{where}: a {kind} value is expected")


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


# ----------------------------------------------------------------------------------- dict sections


def _dict_slot_kind(ctx: _Ctx, section: str, key: str) -> str:
    slot_id = ctx.keys.get((section, key))
    if slot_id is None:
        raise PatchError(f"{section}.{key} is not a catalog field")
    return ctx.catalog.slots[slot_id].value_schema.kind


def _apply_dict(ctx: _Ctx, section: str, key: str, op: PatchOp) -> tuple[PatchOp, PatchOp]:
    kind = _dict_slot_kind(ctx, section, key)
    items: dict[str, BriefItem] = getattr(ctx.work, section)
    old = items.get(key)
    path = f"{section}.{key}"
    restore = (
        PatchOp(op="add", path=path, value=_dump(old))
        if old is not None
        else PatchOp(op="remove", path=path)
    )
    old_value = old.value if old is not None else None
    if op.op in ("clear", "remove"):
        if old is None:
            raise PatchError(f"{path}: nothing to {op.op}")
        del items[key]
        return PatchOp(op=op.op, path=path, old_value=old_value), restore
    if op.op == "add" and _is_item_dump(op.value):
        items[key] = BriefItem.model_validate(op.value)
        return PatchOp(
            op="add", path=path, value=op.value, old_value=_dump(old) if old else None
        ), restore
    if op.op == "add" and old is not None:
        raise PatchError(f"{path}: already exists (use set)")
    if _blank(op.value):
        raise PatchError(f"{path}: an empty value needs op 'clear'")
    _check_value(kind, op.value, path)
    if old is not None and old.has_value() and old.status in _USER_GIVEN and not ctx.evidence_actor:
        raise PatchError(f"{path}: a non-user change cannot overwrite a user-given value")
    history = list(old.history) if old is not None else []
    if old is not None and old.has_value():
        reason = "edited (was Locked)" if old.tier == "Locked" else "edited"
        ref = old.provenance[-1].ref if old.provenance else ""
        history.append(
            HistoryEntry(
                value=old.value,
                status=old.status,
                turn_id=ref or ctx.turn_id,
                at=ctx.now,
                reason=reason,
            )
        )
    meta = ctx.meta(ctx.provenance(op), history)
    items[key] = BriefItem(value=op.value, **meta)
    applied = PatchOp(
        op=op.op,
        path=path,
        value=op.value,
        old_value=old_value,
        provenance=list(meta["provenance"]),
    )
    return applied, restore


# ----------------------------------------------------------------------------------- list sections


def _name_of(rec: Any, section: str) -> str:
    name_field = NAME_FIELD.get(section)
    return str(getattr(rec, name_field, "")).strip().casefold() if name_field else ""


def _find(ctx: _Ctx, section: str, rest: str) -> tuple[int, Any, str | None]:
    """(index, record, field) for ``<ID>[.<field>]`` or ``<name>[.<field>]``."""
    records: list[Any] = getattr(ctx.work, section)
    if section in ID_FIELD_BY_SECTION:
        head, _, fld = rest.partition(".")
        rid = ctx.work.ids.resolve(head)
        for i, rec in enumerate(records):
            if rec.id == rid:
                return i, rec, fld or None
        if ctx.work.ids.is_deleted(rid):
            raise PatchError(f"{section}.{head}: was removed (ids are never reused)")
        raise PatchError(f"{section}.{head}: no such record")
    candidates: list[tuple[str, str | None]] = [(rest, None)]
    if "." in rest:
        head, _, fld = rest.rpartition(".")
        candidates.append((head, fld))
    for name, fld in candidates:
        for i, rec in enumerate(records):
            if _name_of(rec, section) == name.strip().casefold():
                return i, rec, fld
    raise PatchError(f"{section}.{rest}: no such record")


def _record_path(section: str, rec: Any) -> str:
    if section in ID_FIELD_BY_SECTION:
        return f"{section}.{rec.id}"
    return f"{section}.{getattr(rec, NAME_FIELD[section])}"


def _fresh_meta(ctx: _Ctx, op: PatchOp) -> dict[str, Any]:
    return ctx.meta(ctx.provenance(op), [])


def _apply_add(ctx: _Ctx, section: str, rest: str, op: PatchOp) -> tuple[PatchOp, PatchOp]:
    model = RECORD_MODELS[section]
    records: list[Any] = getattr(ctx.work, section)
    if not isinstance(op.value, dict):
        raise PatchError(f"{section}: add needs a record (an object) as value")
    data: dict[str, Any] = dict(op.value)
    ledger = ctx.work.ids
    prefix = PREFIX_BY_SECTION.get(section)
    explicit = data.get("id") or (rest if ITEM_ID_RE.match(rest) else None)
    if section in ID_FIELD_BY_SECTION:
        if explicit:
            parsed = parse_id(str(explicit))
            if parsed is None or (prefix and parsed[0] != prefix):
                raise PatchError(f"{section}: bad id {explicit!r}")
            if any(r.id == explicit for r in records):
                raise PatchError(f"{section}.{explicit}: already exists")
            if explicit in ledger.tombstones:
                ledger.tombstones.remove(explicit)  # restoring: the same id comes back
            ledger.note_existing(str(explicit))
            data["id"] = str(explicit)
        elif prefix is None:
            raise PatchError(f"{section}: ids cannot be allocated here")
        else:
            data["id"] = ledger.allocate(prefix)
    elif "id" in data:
        data.pop("id")
    if "provenance" not in data and issubclass(model, ItemMeta):
        data.update(_fresh_meta(ctx, op))
    try:
        record = model.model_validate(data)
    except ValidationError as exc:
        raise PatchError(f"{section}: invalid record: {exc.errors()[0]['msg']}") from exc
    name = _name_of(record, section)
    if name and any(_name_of(r, section) == name for r in records):
        raise PatchError(f"{section}: {name!r} already exists")
    index = (
        op.old_value
        if isinstance(op.old_value, int) and 0 <= op.old_value <= len(records)
        else len(records)
    )
    records.insert(index, record)
    path = _record_path(section, record)
    return (
        PatchOp(
            op="add", path=path, value=_dump(record), old_value=index, provenance=op.provenance
        ),
        PatchOp(op="remove", path=path),
    )


def _apply_remove(ctx: _Ctx, section: str, rest: str) -> tuple[PatchOp, PatchOp]:
    index, rec, fld = _find(ctx, section, rest)
    if fld:
        raise PatchError(f"{section}.{rest}: remove needs a whole record (use clear for a field)")
    records: list[Any] = getattr(ctx.work, section)
    del records[index]
    if section in ID_FIELD_BY_SECTION:
        ctx.work.ids.tombstone(rec.id)
    path = _record_path(section, rec)
    dump = _dump(rec)
    return (
        PatchOp(op="remove", path=path, old_value=dump),
        PatchOp(op="add", path=path, value=dump, old_value=index),
    )


def _field_default(model: type[BaseModel], name: str) -> Any:
    info = model.model_fields[name]
    default = info.get_default(call_default_factory=True)
    if default is PydanticUndefined:
        raise PatchError(f"{name}: has no default, set it instead")
    return default.model_dump(mode="json") if isinstance(default, BaseModel) else default


def _apply_record(ctx: _Ctx, section: str, rest: str, op: PatchOp) -> tuple[PatchOp, PatchOp]:
    model = RECORD_MODELS[section]
    records: list[Any] = getattr(ctx.work, section)
    index, rec, fld = _find(ctx, section, rest)
    path = _record_path(section, rec)
    old_dump = _dump(rec)
    restore = PatchOp(op="set", path=path, value=old_dump)
    has_meta = isinstance(rec, ItemMeta)
    if fld is None:
        if op.op != "set" or not isinstance(op.value, dict):
            raise PatchError(f"{path}: set a whole record with an object value")
        data = {**old_dump, **op.value}
        if section in ID_FIELD_BY_SECTION:
            data["id"] = rec.id
        if "provenance" not in op.value and has_meta:
            data.update(_edit_meta(ctx, op, rec, "record"))
        new = _validated(model, data, path)
        records[index] = new
        return PatchOp(
            op="set", path=path, value=_dump(new), old_value=old_dump, provenance=op.provenance
        ), restore
    names = set(model.model_fields) - META_FIELDS - {"id"}
    if fld not in names:
        raise PatchError(f"{path}.{fld}: not an editable field")
    old_value = old_dump[fld]
    if op.op == "clear":
        new_value = _field_default(model, fld)
    elif op.op == "set":
        if op.value is None:
            raise PatchError(f"{path}.{fld}: an empty value needs op 'clear'")
        new_value = op.value
    else:
        raise PatchError(f"{path}.{fld}: {op.op} needs a whole record")
    data = {**old_dump, fld: new_value}
    if has_meta:
        data.update(_edit_meta(ctx, op, rec, fld, old_value))
    new = _validated(model, data, f"{path}.{fld}")
    records[index] = new
    applied = PatchOp(
        op=op.op,
        path=f"{path}.{fld}",
        value=op.value,
        old_value=old_value,
        provenance=op.provenance,
    )
    return applied, restore


def _validated(model: type[BaseModel], data: dict[str, Any], where: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise PatchError(f"{where}: {err['msg']} ({'.'.join(str(p) for p in err['loc'])})") from exc


def _edit_meta(
    ctx: _Ctx, op: PatchOp, rec: ItemMeta, what: str, old_value: Any = None
) -> dict[str, Any]:
    """The meta of an edited record: evidence appended, old value in history, status by actor."""
    if not ctx.evidence_actor and rec.status in _USER_GIVEN:
        raise PatchError(f"{what}: a non-user change cannot overwrite a user-given value")
    provenance = list(rec.provenance)
    for p in ctx.provenance(op):
        if p not in provenance:
            provenance.append(p)
    reason = f"{what} edited" + (" (was Locked)" if rec.tier == "Locked" else "")
    ref = rec.provenance[-1].ref if rec.provenance else ""
    history = [
        *rec.history,
        HistoryEntry(
            value=old_value,
            status=rec.status,
            turn_id=ref or ctx.turn_id,
            at=ctx.now,
            reason=reason,
        ),
    ]
    return ctx.meta(provenance, history)


# ----------------------------------------------------------------------------------- entry points


def _apply_one(ctx: _Ctx, op: PatchOp) -> tuple[PatchOp, PatchOp]:
    section, _, rest = op.path.partition(".")
    if section not in SECTION_KEYS:
        raise PatchError(f"{op.path}: unknown section {section!r}")
    if section in DICT_SECTIONS:
        if not rest:
            raise PatchError(f"{op.path}: a field name is needed")
        return _apply_dict(ctx, section, rest, op)
    if op.op == "add":
        return _apply_add(ctx, section, rest, op)
    if not rest:
        raise PatchError(f"{op.path}: a record is needed")
    if op.op == "remove":
        return _apply_remove(ctx, section, rest)
    return _apply_record(ctx, section, rest, op)


def apply_ops(
    brief: Brief,
    ops: Sequence[PatchOp],
    catalog: Catalog,
    *,
    now: datetime,
    turn_id: str | None = None,
    actor: ProvenanceType = "user_text",
) -> PatchResult:
    """Apply ``ops`` on a copy; all or nothing (see the module docstring for the semantics)."""
    work = brief.model_copy(deep=True)
    ctx = _Ctx(work, catalog, now, turn_id, actor, dict_targets(catalog))
    applied: list[PatchOp] = []
    inverses: list[PatchOp] = []
    errors: list[str] = []
    for op in ops:
        try:
            done, undo = _apply_one(ctx, op)
        except PatchError as exc:
            errors.append(str(exc))
            continue
        applied.append(done)
        inverses.append(undo)
    if not errors:
        before = set(validate_brief(brief, catalog))
        errors = [e for e in validate_brief(work, catalog) if e not in before]
    if errors or not applied:
        return PatchResult(brief=brief, errors=errors)
    locales = work.experience.get("locales")
    if locales is not None and locales.has_value() and locales_of(locales.value):
        work.meta.locales = locales_of(locales.value)  # meta.locales follows the locales item
    work.version = brief.version + 1
    voided = changed_sections(section_hashes(brief), section_hashes(work))
    return PatchResult(
        brief=work, ops=applied, inverse_ops=list(reversed(inverses)), voided_sections=voided
    )


def invert(change_set: ChangeSet) -> list[PatchOp]:
    """The ops that revert ``change_set`` (apply them as a NEW change set; history is never rewritten)."""
    return [op.model_copy(deep=True) for op in change_set.inverse_ops]


def build_change_set(
    result: PatchResult,
    *,
    id: str,
    base_version: int,
    source: ChangeSource,
    reason: str,
    summary: str,
    turn_id: str | None,
    undoes: str | None = None,
    reseeded_research: bool = False,
) -> ChangeSet:
    """Wrap a successful ``PatchResult`` as a ``ChangeSet`` (``new_version = base_version + 1``)."""
    return ChangeSet(
        id=id,
        base_version=base_version,
        new_version=base_version + 1,
        ops=[op.model_copy(deep=True) for op in result.ops],
        inverse_ops=[op.model_copy(deep=True) for op in result.inverse_ops],
        source=source,
        reason=reason,
        summary=summary,
        turn_id=turn_id,
        voided_sections=list(result.voided_sections),
        reseeded_research=reseeded_research,
        undoes=undoes,
    )
