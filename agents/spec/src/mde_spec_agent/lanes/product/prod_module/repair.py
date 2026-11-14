"""Mechanical repairs of a PROD-MODULE answer, made by code before the checks and the patch (R-code: logged, never silent).

A model sometimes invents entities (an account, a code, a session) and calls them primary or secondary. Such an entity would
have no screen and fail the inventory check (V-29), and the system set already covers accounts and codes. A NEW entity that
is not a join or log entity is therefore dropped with everything that hangs on it, and the drop is logged. Nothing a
validator asks for is weakened: the answer simply no longer contains what could not be valid."""

from __future__ import annotations

from mde_compass_intake.text import stem_set
from mde_spec_core.callkit.ladder import FixKind

from .schema import ModuleIn, ModuleOut, StoryRec


def repair(inp: ModuleIn, out: ModuleOut) -> tuple[ModuleOut, list[dict[str, str]]]:
    """The answer after the mechanical repairs, and the R-code rows that say what was changed (see the module docstring)."""
    out, rows = _drop_invented_entities(out)
    out, more = _attach_stories(inp, out)
    return out, [*rows, *more]


def _drop_invented_entities(out: ModuleOut) -> tuple[ModuleOut, list[dict[str, str]]]:
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


def _attach_stories(inp: ModuleIn, out: ModuleOut) -> tuple[ModuleOut, list[dict[str, str]]]:
    in_features = set(inp.in_feature_ids)
    known = set(inp.feature_ids)
    policies = set(inp.policy_refs)
    names = dict(x.split("=", 1) for x in inp.feature_names if "=" in x)
    todo = [
        s
        for s in out.stories
        if s.id.startswith("NEW-")
        and not any(f in in_features for f in s.feature_ids)
        and not (s.policy_id and (not policies or s.policy_id in policies))
        and all(
            f in known for f in s.feature_ids
        )  # a cited id that does not exist stays a validator error
    ]
    if not todo:
        return out, []
    o = out.model_copy(deep=True)
    rows: list[dict[str, str]] = []
    gone: set[str] = set()
    for s in o.stories:
        if s.id not in {t.id for t in todo}:
            continue
        fid = _fit(inp, s, names)
        if fid:
            s.feature_ids = [fid]
            s.prov = [*s.prov, f"assumed:attached to feature {fid} by code"]
            rows.append(_row("exact_synonym", s.id, f"attached to {fid}", "", fid, s))
        else:
            gone.add(s.id)
            rows.append(
                _row(
                    "drop_unknown_optional",
                    s.id,
                    "dropped: it serves no in feature and no policy",
                    s.want,
                    "",
                    s,
                )
            )
    o.stories = [s for s in o.stories if s.id not in gone]
    o.acceptance = [a for a in o.acceptance if a.story not in gone]
    return o, rows


def _fit(inp: ModuleIn, s: StoryRec, names: dict[str, str]) -> str | None:
    words = stem_set(f"{s.id.removeprefix('NEW-').replace('_', ' ')} {s.want} {s.so_that}")
    by_name = [f for f in inp.in_feature_ids if stem_set(names.get(f, "")) & words]
    if len(by_name) == 1:
        return by_name[0]
    own = sorted(set(inp.module_feature_ids) & set(inp.in_feature_ids))
    return own[0] if len(own) == 1 else None


def _row(
    kind: str, node_id: str, what: str, before: str, after: str, s: StoryRec
) -> dict[str, str]:
    return {
        "kind": kind,
        "node_id": node_id,
        "what": f"story {s.id} cites no in feature and no policy id: {what}",
        "before": before,
        "after": after,
    }
