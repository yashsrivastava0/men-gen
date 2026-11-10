"""What the person does to the structure of the app, as patch operations (ADR 0017): pure functions, no turn, no model.

Every action is a list of ``PatchOp`` applied as ONE change set (the patch engine is atomic and every op has an exact
inverse, so undo restores everything, a removal's cascade included):

* ``accept``: sets the real ``decision`` field to ``accepted``. The op carries a provenance entry that is NOT evidence, so
  an accepted record is confirmed but stays ``Default`` (a bulk accept can never create ``Locked``; only the person's
  own typed words do). Accepting a module accepts what is in it.
* ``add``: a new record, id allocated by the patch engine; the person's own words are evidence (Locked-eligible).
* ``remove``: the record and what depends on it: a module takes its screens and the flow steps that pointed at them
  (a flow left with fewer than two steps goes too) and gives its entities to a module that still uses them (or goes with
  them when nothing does); a screen takes its flow steps and stops being an entry.
* ``regenerate`` and ``set_band`` are drafts, not patches: ``engine/structure_controller`` runs them and records the
  result as one change set with ``diff_ops``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import JsonValue

from mde_compass.domain.brief import Brief
from mde_compass.domain.changes import PatchOp
from mde_compass.domain.common import Provenance
from mde_compass.domain.structure import STRUCTURE_SECTIONS

SECTION_ROOTS = tuple(STRUCTURE_SECTIONS)


class ActionError(ValueError):
    """The action cannot be turned into operations (unknown record, nothing to do)."""


def _records(brief: Brief, section: str) -> list[Any]:
    if section not in SECTION_ROOTS:
        raise ActionError(f"{section!r} is not a part of the structure")
    return list(getattr(brief, section))


def find(brief: Brief, section: str, ident: str) -> Any:
    for rec in _records(brief, section):
        if rec.id == ident:
            return rec
    raise ActionError(f"{section}.{ident}: no such record")


def not_evidence(ref: str) -> list[Provenance]:
    """Provenance that records WHO looked at a record without claiming the person said it."""
    return [Provenance(type="assumed", ref=ref)]


# ------------------------------------------------------------------------------------------------- accept


def _set(path: str, value: JsonValue, prov: Sequence[Provenance]) -> PatchOp:
    return PatchOp(op="set", path=path, value=value, provenance=list(prov))


def records_in_module(brief: Brief, module_id: str) -> dict[str, list[str]]:
    """The ids that belong to a module: its screens, the entities it owns and the flows that stay inside it."""
    screens = [s.id for s in brief.screens if s.module == module_id]
    inside = set(screens)
    flows = [f.id for f in brief.flows if f.steps and all(st.screen in inside for st in f.steps)]
    entities = [e.id for e in brief.entities if e.module == module_id]
    return {"screens": screens, "flows": flows, "entities": entities}


def accept_ops(brief: Brief, section: str, ident: str, prov: Sequence[Provenance]) -> list[PatchOp]:
    """Accept a record; a module also accepts its screens, its entities and the flows that stay inside it."""
    find(brief, section, ident)
    targets: list[tuple[str, str]] = [(section, ident)]
    if section == "modules":
        for sec, ids in records_in_module(brief, ident).items():
            targets += [(sec, i) for i in ids]
    ops: list[PatchOp] = []
    for sec, i in targets:
        rec = find(brief, sec, i)
        if rec.decision != "accepted":
            ops.append(_set(f"{sec}.{i}.decision", "accepted", prov))
    return ops


def accept_all_ops(brief: Brief, prov: Sequence[Provenance]) -> list[PatchOp]:
    """Everything in the structure that is still proposed (what Approve All adds to its change set)."""
    ops: list[PatchOp] = []
    for sec in SECTION_ROOTS:
        for rec in _records(brief, sec):
            if rec.decision != "accepted":
                ops.append(_set(f"{sec}.{rec.id}.decision", "accepted", prov))
    return ops


def unreviewed_modules(brief: Brief) -> int:
    return sum(1 for m in brief.modules if m.decision != "accepted")


# --------------------------------------------------------------------------------------------------- add


def add_ops(section: str, value: JsonValue, prov: Sequence[Provenance]) -> list[PatchOp]:
    if section not in SECTION_ROOTS:
        raise ActionError(f"{section!r} is not a part of the structure")
    if not isinstance(value, dict) or not str(value.get("name", "")).strip():
        raise ActionError("a new record needs at least a name")
    fields = {
        k: v
        for k, v in value.items()
        if k not in ("id", "tier", "provenance", "confidence", "status", "history", "decision")
    }
    return [PatchOp(op="add", path=section, value=fields, provenance=list(prov))]


# ------------------------------------------------------------------------------------------------ remove


def remove_ops(
    brief: Brief, section: str, ident: str, prov: Sequence[Provenance] = ()
) -> list[PatchOp]:
    """The removal of a record and its cascade, in an order that keeps every intermediate state valid."""
    find(brief, section, ident)
    if section == "modules":
        return _remove_module(brief, ident, prov)
    if section == "screens":
        return _remove_screens(brief, {ident}, True, prov)
    if section == "flows":
        return [PatchOp(op="remove", path=f"flows.{ident}")]
    return _remove_entity(brief, ident, prov)


def _remove_screens(
    brief: Brief, gone: set[str], keep_module: bool, prov: Sequence[Provenance]
) -> list[PatchOp]:
    ops: list[PatchOp] = []
    removed_flows: set[str] = set()
    for flow in brief.flows:
        steps = [st for st in flow.steps if st.screen not in gone]
        if len(steps) == len(flow.steps):
            continue
        if len(steps) < 2:
            ops.append(PatchOp(op="remove", path=f"flows.{flow.id}"))
            removed_flows.add(flow.id)
        else:
            ops.append(
                _set(f"flows.{flow.id}.steps", [st.model_dump(mode="json") for st in steps], prov)
            )
    if keep_module:
        for m in brief.modules:
            if m.entry in gone:
                rest = [s.id for s in brief.screens if s.module == m.id and s.id not in gone]
                ops.append(_set(f"modules.{m.id}.entry", rest[0] if rest else "", prov))
    ops += [PatchOp(op="remove", path=f"screens.{sid}") for sid in sorted(gone)]
    return ops


def _remove_entity(
    brief: Brief,
    ident: str,
    prov: Sequence[Provenance],
    skip: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[PatchOp]:
    ops: list[PatchOp] = []
    for s in brief.screens:
        if s.id in skip:  # a screen that is being removed in the same change needs no clean-up
            continue
        uses = [u for u in s.entities if u.entity != ident]
        if len(uses) != len(s.entities):
            ops.append(
                _set(f"screens.{s.id}.entities", [u.model_dump(mode="json") for u in uses], prov)
            )
    for e in brief.entities:
        if e.id != ident and any(f.ref == ident for f in e.fields):
            fields = [f.model_copy(update={"ref": ""}) if f.ref == ident else f for f in e.fields]
            ops.append(
                _set(f"entities.{e.id}.fields", [f.model_dump(mode="json") for f in fields], prov)
            )
    ops.append(PatchOp(op="remove", path=f"entities.{ident}"))
    return ops


def _remove_module(brief: Brief, module_id: str, prov: Sequence[Provenance]) -> list[PatchOp]:
    gone = {s.id for s in brief.screens if s.module == module_id}
    ops = _remove_screens(brief, gone, False, prov)
    for e in brief.entities:
        if e.module != module_id:
            continue
        users = [
            s.module
            for s in brief.screens
            if s.id not in gone and any(u.entity == e.id for u in s.entities)
        ]
        if users:
            ops.append(_set(f"entities.{e.id}.module", users[0], prov))
        else:
            ops += _remove_entity(brief, e.id, prov, gone)
    ops.append(PatchOp(op="remove", path=f"modules.{module_id}"))
    return ops


# -------------------------------------------------------------------------------------------------- diff


def diff_ops(
    before: Brief, after: Brief, sections: Iterable[str] = SECTION_ROOTS
) -> tuple[list[PatchOp], list[PatchOp]]:
    """Operations that turn ``before`` into ``after`` for the structure sections, and their inverse (what undo replays):
    a record that is new is an ``add`` (inverse ``remove``), one that is gone a ``remove`` (inverse ``add``), one that
    changed a whole-record ``set`` (inverse: the old record). Order is dependency-safe: adds of modules first, removals
    of modules last."""
    ops: list[PatchOp] = []
    inverse: list[PatchOp] = []
    order = ("modules", "entities", "screens", "flows")
    for sec in [s for s in order if s in set(sections)]:
        old = {r.id: r for r in getattr(before, sec)}
        new = {r.id: r for r in getattr(after, sec)}
        for ident, rec in new.items():
            if ident not in old:
                ops.append(
                    PatchOp(op="add", path=f"{sec}.{ident}", value=rec.model_dump(mode="json"))
                )
                inverse.insert(0, PatchOp(op="remove", path=f"{sec}.{ident}"))
            elif rec != old[ident]:
                ops.append(
                    PatchOp(
                        op="set",
                        path=f"{sec}.{ident}",
                        value=rec.model_dump(mode="json"),
                        old_value=old[ident].model_dump(mode="json"),
                    )
                )
                inverse.insert(
                    0,
                    PatchOp(
                        op="set", path=f"{sec}.{ident}", value=old[ident].model_dump(mode="json")
                    ),
                )
        for ident, rec in old.items():
            if ident not in new:
                ops.append(PatchOp(op="remove", path=f"{sec}.{ident}"))
                inverse.insert(
                    0, PatchOp(op="add", path=f"{sec}.{ident}", value=rec.model_dump(mode="json"))
                )
    return ops, inverse
