"""Mechanical repairs of a PROD-MODULE answer, made by code before the checks and the patch (R-code: logged, never silent).

A model sometimes invents entities (an account, a code, a session) and calls them primary or secondary. Such an entity would
have no screen and fail the inventory check (V-29), and the system set already covers accounts and codes. A NEW entity that
is not a join or log entity is therefore dropped with everything that hangs on it, and the drop is logged. Nothing a
validator asks for is weakened: the answer simply no longer contains what could not be valid."""

from __future__ import annotations

from mde_compass_intake.text import stem_set
from mde_spec_agent.lanes.product.kit.common import (
    invented_numbers,
    scrub,
    scrub_row,
    snake,
    source_numbers,
)
from mde_spec_core.callkit.ladder import FixKind
from mde_spec_core.ids import is_valid_key

from .baseline import acceptance_for, cover_stories
from .schema import ModuleIn, ModuleOut, StoryRec


def repair(inp: ModuleIn, out: ModuleOut) -> tuple[ModuleOut, list[dict[str, str]]]:
    """The answer after the mechanical repairs, and the R-code rows that say what was changed (see the module docstring)."""
    out, rows = _drop_invented_entities(out)
    out = out.model_copy(
        deep=True
    )  # the steps below edit in place; the caller's answer is never touched
    for step in (_attach_stories, _stories_and_acceptance, _fields, _lifecycles, _rules, _texts):
        out, more = step(inp, out)
        rows += more
    return out, rows


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
            rows.append(_attach_row("exact_synonym", s.id, f"attached to {fid}", "", fid, s))
        else:
            gone.add(s.id)
            rows.append(
                _attach_row(
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


def _attach_row(
    kind: str, node_id: str, what: str, before: str, after: str, s: StoryRec
) -> dict[str, str]:
    return {
        "kind": kind,
        "node_id": node_id,
        "what": f"story {s.id} cites no in feature and no policy id: {what}",
        "before": before,
        "after": after,
    }


# ------------------------------------------------------------------------------------------------ helpers
def _row(
    kind: FixKind | str, node_id: str, what: str, before: str = "", after: str = ""
) -> dict[str, str]:
    return {
        "kind": kind.value if isinstance(kind, FixKind) else kind,
        "node_id": node_id,
        "what": what,
        "before": before[:160],
        "after": after[:160],
    }


def _source(inp: ModuleIn) -> tuple[str, set[str]]:
    src = "\n".join([*inp.slice, *inp.facts, *inp.research])
    return src, source_numbers(src, inp.baseline.model_dump_json())


def _role_names(inp: ModuleIn) -> dict[str, str]:
    return dict(x.split("=", 1) for x in inp.role_names if "=" in x)


# ------------------------------------------------------------------------------------------------ stories and acceptance
def _stories_and_acceptance(
    inp: ModuleIn, out: ModuleOut
) -> tuple[ModuleOut, list[dict[str, str]]]:
    """A draft story the answer left out comes back with its acceptance; every story has acceptance; every screen of the
    call is cited by a story (an assumed one, built from what the draft knows)."""
    rows: list[dict[str, str]] = []
    have = {s.id for s in out.stories}
    for st in inp.baseline.stories:
        if st.id not in have:
            out.stories.append(st.model_copy(deep=True))
            out.acceptance += [a.model_copy() for a in inp.baseline.acceptance if a.story == st.id]
            rows.append(
                _row(
                    FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                    st.id,
                    "the draft story was left out: restored",
                )
            )
    names = _role_names(inp)
    with_acc = {a.story for a in out.acceptance}
    for st in out.stories:
        if st.id not in with_acc:
            out.acceptance.append(acceptance_for(st, names.get(st.role, st.role)))
            rows.append(
                _row(
                    FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                    st.id,
                    "the story had no acceptance: one made from its wording",
                )
            )
    added = cover_stories(inp, out)
    for st, acc in added:
        out.stories.append(st)
        out.acceptance.append(acc)
        rows.append(
            _row(
                FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                st.id,
                f"no story cited {st.screen_ids[0]}: an assumed one added",
            )
        )
    return out, rows


# ------------------------------------------------------------------------------------------------ fields
def _fields(inp: ModuleIn, out: ModuleOut) -> tuple[ModuleOut, list[dict[str, str]]]:
    rows: list[dict[str, str]] = []
    draft = {(f.entity, f.key): f for f in inp.baseline.fields}
    known = set(inp.entity_ids) | {e.id for e in out.entities} | set(inp.ref_ids)
    seen: set[tuple[str, str]] = set()
    keep = []
    for f in out.fields:
        if not is_valid_key(f.key):
            new = snake(f.key, "field")
            rows.append(
                _row(
                    FixKind.EXACT_SYNONYM,
                    f"{f.entity}.{f.key}",
                    "field key made lower snake case",
                    f.key,
                    new,
                )
            )
            f.key = new
        if (f.entity, f.key) in seen:
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    f"{f.entity}.{f.key}",
                    "the field was listed twice: the second dropped",
                )
            )
            continue
        seen.add((f.entity, f.key))
        if f.ref_entity and f.ref_entity not in known:
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    f"{f.entity}.{f.key}",
                    "ref_entity does not exist: cleared",
                    f.ref_entity,
                )
            )
            f.ref_entity = ""
        d = draft.get((f.entity, f.key))
        if f.type == "ref" and not f.ref_entity:
            f.type = d.type if d is not None and d.type != "ref" else "text"
            rows.append(
                _row(
                    FixKind.EXACT_SYNONYM,
                    f"{f.entity}.{f.key}",
                    "a ref with no entity became its draft type",
                    "ref",
                    f.type,
                )
            )
        if f.type == "enum" and not f.enum_values:
            if d is not None and d.enum_values:
                f.enum_values = list(d.enum_values)
            else:
                f.type = "text"
            rows.append(
                _row(
                    FixKind.EXACT_SYNONYM,
                    f"{f.entity}.{f.key}",
                    "an enum with no values took the draft's values or became text",
                )
            )
        keep.append(f)
    out.fields = keep
    return out, rows


# ------------------------------------------------------------------------------------------------ lifecycles
def _valid(states: list[str], initial: str | None, trans: list[tuple[str, str]]) -> bool:
    if len(states) != len(set(states)):
        return False
    if len(states) < 2:
        return True
    if initial not in states:
        return False
    reach, stack = set(), [initial]
    while stack:
        cur = stack.pop()
        if cur in reach:
            continue
        reach.add(cur)
        stack += [b for a, b in trans if a == cur]
    return set(states) <= reach and not all(any(a == s for a, _ in trans) for s in states)


def _lifecycles(inp: ModuleIn, out: ModuleOut) -> tuple[ModuleOut, list[dict[str, str]]]:
    """A lifecycle the checks (V-09) would refuse is mended: unknown roles and statuses are removed, a transition with no
    role takes the draft's roles for it, one initial status is fixed; if it is still wrong (an unreachable status, none
    with no way out) the entity takes the draft's own lifecycle, or none when the draft has none."""
    rows: list[dict[str, str]] = []
    roles = set(inp.role_ids)
    for ent in out.entities:
        srows = [s for s in out.states if s.entity == ent.id]
        if not srows:
            continue
        d_states = [s for s in inp.baseline.states if s.entity == ent.id]
        d_trans = [t for t in inp.baseline.transitions if t.entity == ent.id]
        names = list(dict.fromkeys(s.state for s in srows))
        init = [s.state for s in srows if s.initial]
        initial = init[0] if len(init) >= 1 else names[0]
        trans = [
            t
            for t in out.transitions
            if t.entity == ent.id and t.from_state in names and t.to_state in names
        ]
        draft_roles = {(t.from_state, t.to_state): t.roles for t in d_trans}
        any_roles = sorted({r for t in d_trans for r in t.roles})
        fixed = []
        for t in trans:
            t.roles = [r for r in t.roles if r in roles]
            if not t.roles:
                t.roles = list(draft_roles.get((t.from_state, t.to_state)) or any_roles)
            if t.roles:
                fixed.append(t)
        pairs = [(t.from_state, t.to_state) for t in fixed]
        if _valid(names, initial, pairs):
            out.states = [s for s in out.states if s.entity != ent.id] + [
                type(srows[0])(entity=ent.id, state=n, initial=n == initial) for n in names
            ]
            out.transitions = [t for t in out.transitions if t.entity != ent.id] + fixed
            continue
        out.states = [s for s in out.states if s.entity != ent.id] + [
            s.model_copy() for s in d_states
        ]
        out.transitions = [t for t in out.transitions if t.entity != ent.id] + [
            t.model_copy(deep=True) for t in d_trans
        ]
        rows.append(
            _row(
                FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                ent.id,
                "the lifecycle had an unreachable status or none with no way out: the draft's lifecycle is used"
                if d_states
                else "the lifecycle was not valid and the draft has none: left out",
                " > ".join(names),
                " > ".join(s.state for s in d_states),
            )
        )
    # the status values of an entity are its lifecycle statuses
    for ent in out.entities:
        names = [s.state for s in out.states if s.entity == ent.id]
        if not names:
            continue
        for f in out.fields:
            if (
                f.entity == ent.id
                and f.key == "status"
                and f.type == "enum"
                and set(f.enum_values) != set(names)
            ):
                f.enum_values = list(names)
    return out, rows


# ------------------------------------------------------------------------------------------------ rules
def _rules(inp: ModuleIn, out: ModuleOut) -> tuple[ModuleOut, list[dict[str, str]]]:
    """A rule of the draft is the code's own (the patch keeps its text and number). A rule the model adds states a number
    only when a fact states it and its own sources are the user's: otherwise the value is dropped (the rule stays, assumed)."""
    rows: list[dict[str, str]] = []
    src, nums = _source(inp)
    facts_nums = source_numbers("\n".join(inp.facts), "\n".join(inp.research))
    drop: set[str] = set()
    for r in out.rules:
        if not r.id.startswith("NEW-"):
            continue
        if r.value and (r.value.replace(",", ".") not in facts_nums | nums or not r.unit):
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    r.id,
                    "a value no fact states (or with no unit): the rule stays without it",
                    f"{r.value} {r.unit}",
                )
            )
            r.value = r.unit = ""
        for field in ("statement", "condition", "subject"):
            before = getattr(r, field)
            after, gone = scrub(before, src, nums)
            if gone:
                setattr(r, field, after)
                rows.append(scrub_row(r.id, field, before, after, gone))
        if not r.statement.strip():
            drop.add(r.id)
    if drop:
        out.rules = [r for r in out.rules if r.id not in drop]
        for f in out.fields:
            if f.derived_rule in drop:
                f.derived_rule = ""
    return out, rows


# ------------------------------------------------------------------------------------------------ texts
def _texts(inp: ModuleIn, out: ModuleOut) -> tuple[ModuleOut, list[dict[str, str]]]:
    """Layout or widget words and invented numbers in the model's free text: the clause is cut, never re-asked."""
    rows: list[dict[str, str]] = []
    src, nums = _source(inp)
    names = _role_names(inp)
    draft_story = {s.id: s for s in inp.baseline.stories}
    gone: set[str] = set()
    for st in out.stories:
        for field in ("want", "so_that"):
            before = getattr(st, field)
            after, cut = scrub(before, src, nums)
            if not cut:
                continue
            if not after.strip() and st.id in draft_story:
                after = getattr(draft_story[st.id], field)
            setattr(st, field, after)
            rows.append(scrub_row(st.id, field, before, after, cut))
        if not st.want.strip() or not st.so_that.strip():
            gone.add(st.id)
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    st.id,
                    "nothing is left of the story's wording: dropped",
                )
            )
    if gone:
        out.stories = [s for s in out.stories if s.id not in gone]
        out.acceptance = [a for a in out.acceptance if a.story not in gone]
    by_id = {s.id: s for s in out.stories}
    for a in out.acceptance:
        for field in ("given", "when", "then"):
            before = getattr(a, field)
            after, cut = scrub(before, src, nums)
            if not cut and before.strip():
                continue
            st = by_id.get(a.story)
            if (
                not after.strip() and st is not None
            ):  # nothing left: the same part of the made-from-the-story acceptance
                after = getattr(acceptance_for(st, names.get(st.role, st.role)), field)
            setattr(a, field, after)
            if cut:
                rows.append(scrub_row(a.story, f"acceptance {field}", before, after, cut))
            else:
                rows.append(
                    _row(
                        FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                        a.story,
                        f"acceptance {field} was blank: made from the story",
                    )
                )
    for op in out.operations:
        before = op.name
        after, cut = scrub(before, src, nums)
        if cut:
            op.name = after or op.id.removeprefix("NEW-").replace("_", " ")
            rows.append(scrub_row(op.id, "name", before, op.name, cut))
    for f in out.fields:
        before = f.constraint
        after, cut = scrub(before, src, nums)
        if cut:
            f.constraint = after
            rows.append(scrub_row(f"{f.entity}.{f.key}", "constraint", before, after, cut))
    _ = invented_numbers
    return out, rows
