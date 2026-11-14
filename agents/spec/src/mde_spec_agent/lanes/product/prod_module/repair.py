"""Mechanical repairs of a PROD-MODULE answer, made by code before the checks and the patch (R-code: logged, never silent).

A model sometimes invents entities (an account, a code, a session) and calls them primary or secondary. Such an entity would
have no screen and fail the inventory check (V-29), and the system set already covers accounts and codes. A NEW entity that
is not a join or log entity is therefore dropped with everything that hangs on it, and the drop is logged. Nothing a
validator asks for is weakened: the answer simply no longer contains what could not be valid."""

from __future__ import annotations

from mde_spec_core.callkit.ladder import FixKind

from .schema import ModuleOut


def repair(out: ModuleOut) -> tuple[ModuleOut, list[dict[str, str]]]:
    """The answer without invented non-join, non-log entities, and the R-code rows that say what was dropped."""
    drop = {
        e.id: e for e in out.entities if e.id.startswith("NEW-") and e.klass not in ("join", "log")
    }
    if not drop:
        return out, []
    o = out.model_copy(deep=True)
    o.entities = [e for e in o.entities if e.id not in drop]
    gone_fields = {(f.entity, f.key) for f in o.fields if f.entity in drop or f.ref_entity in drop}
    o.fields = [f for f in o.fields if (f.entity, f.key) not in gone_fields]
    o.states = [s for s in o.states if s.entity not in drop]
    o.transitions = [t for t in o.transitions if t.entity not in drop]
    for r in o.rules:
        if r.entity in drop:
            r.entity = ""
    for op in o.operations:
        if op.entity in drop:
            op.entity = ""
    rows = [
        {
            "kind": FixKind.DROP_UNKNOWN_OPTIONAL.value,
            "node_id": ent.id,
            "what": f"dropped the invented entity {ent.name!r} ({ent.klass}): a new entity must be a join or log entity, "
            "and accounts and codes belong to the system set",
            "before": ent.name,
            "after": "",
        }
        for ent in drop.values()
    ]
    return o, rows
