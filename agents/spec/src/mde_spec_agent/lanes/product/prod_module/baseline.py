"""The code-made draft of a module in the output schema, and the deterministic completion both the mock and the fallback
use (every story gets acceptance, every screen of the module gets a story). Derived from the baseline graph only."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import clip
from mde_spec_agent.lanes.product.kit.slices import ModuleView
from mde_spec_agent.lanes.product.kit.types import ErrorCode, FieldType, Recovery
from mde_spec_core import libraries
from mde_spec_core.graph import Entity, Prov, Rule, SpecGraph, Story

from .schema import (
    AcceptRec,
    EntityRec,
    FieldRec,
    ModuleIn,
    ModuleOut,
    OperationRec,
    OpErrorRec,
    OpFieldRec,
    RuleRec,
    ScreenRef,
    StateRec,
    StoryRec,
    TransitionRec,
)


def _field_type(t: str) -> FieldType:
    """A draft field type in the closed list (an exact synonym, else text: the answer may retype it)."""
    if t in vocab.field_types():
        return t  # type: ignore[return-value]
    syn = libraries.canonical("input_type", t)
    return syn if syn in vocab.field_types() else "text"  # type: ignore[return-value]


def _error_code(c: str) -> ErrorCode:
    return c if c in vocab.error_codes() else "validation_failed"  # type: ignore[return-value]


def _recovery(r: str) -> Recovery:
    return r if r in vocab.recoveries() else "stay"  # type: ignore[return-value]


def prov_strings(prov: Sequence[Prov], fallback: str) -> list[str]:
    out = [f"{p.kind}:{p.ref}" for p in prov if p.ref]
    return out or [fallback]


def entity_recs(e: Entity) -> tuple[EntityRec, list[FieldRec], list[StateRec], list[TransitionRec]]:
    rec = EntityRec(
        id=e.id,
        name=e.name,
        klass=e.klass,
        typical_count=e.typical_count or 0,
        max_count=e.max_count or 0,
        prov=prov_strings(e.provenance, f"compass:{e.id}"),
    )
    fields = [
        FieldRec(
            entity=e.id,
            key=f.key,
            type=_field_type(f.type),
            required=f.required,
            enum_values=list(f.enum_values),
            ref_entity=f.ref_entity or "",
            derived_rule=f.derived or "",
            constraint=f.constraint,
            media=f.media,
        )
        for f in e.fields
    ]
    lc = e.lifecycle
    states = [
        StateRec(
            entity=e.id, state=s, initial=s == (lc.initial or (lc.states[0] if lc.states else ""))
        )
        for s in lc.states
    ]
    transitions = [
        TransitionRec(
            entity=e.id, from_state=t.from_state, to_state=t.to_state, roles=list(t.roles), on=t.on
        )
        for t in lc.transitions
    ]
    return rec, fields, states, transitions


def rule_rec(r: Rule) -> RuleRec:
    return RuleRec(
        id=r.id,
        subject=r.subject,
        statement=r.statement,
        condition=r.condition,
        value=r.value or "",
        unit=r.unit or "",
        entity="",
        confirm=r.confirm,
        prov=prov_strings(r.provenance, f"compass:{r.id}"),
    )


def story_recs(st: Story) -> tuple[StoryRec, list[AcceptRec]]:
    rec = StoryRec(
        id=st.id,
        role=st.role_id,
        want=st.want,
        so_that=st.so_that,
        feature_ids=list(st.feature_ids),
        policy_id=st.policy_id or "",
        screen_ids=list(st.screen_ids),
        voice=st.voice,
        prov=prov_strings(st.provenance, f"assumed:story of {st.id}"),
    )
    acc = [AcceptRec(story=st.id, given=a.given, when=a.when, then=a.then) for a in st.acceptance]
    return rec, acc


def baseline_module_out(
    graph: SpecGraph,
    view: ModuleView,
    *,
    entity_ids: Collection[str] | None = None,
    story_ids: Collection[str] | None = None,
    rule_ids: Collection[str] | None = None,
) -> ModuleOut:
    out = ModuleOut()
    ent_ids = {e.id for e in view.entities if entity_ids is None or e.id in entity_ids}
    for e in view.entities:
        if e.id not in ent_ids:
            continue
        rec, fields, states, transitions = entity_recs(e)
        out.entities.append(rec)
        out.fields += fields
        out.states += states
        out.transitions += transitions
    for r in view.rules:
        if rule_ids is None or r.id in rule_ids:
            out.rules.append(rule_rec(r))
    # custom operations that already exist (a re-run): kept in the draft so a re-ask can see them
    for o in sorted(graph.operations.values(), key=lambda x: x.id):
        if o.kind != "custom" or o.entity not in ent_ids:
            continue
        out.operations.append(
            OperationRec(
                id=o.id,
                name=o.name,
                entity=o.entity or "",
                method=o.method,
                path=o.path,
                roles=list(o.roles),
                idempotent=o.idempotent,
                paged=o.paged,
                prov=prov_strings(o.provenance, f"assumed:operation {o.id}"),
            )
        )
        out.op_fields += [
            OpFieldRec(
                op=o.id, side="request", key=f.key, type=_field_type(f.type), required=f.required
            )
            for f in o.request
        ] + [
            OpFieldRec(
                op=o.id, side="response", key=f.key, type=_field_type(f.type), required=f.required
            )
            for f in o.response
        ]
        out.op_errors += [
            OpErrorRec(op=o.id, code=_error_code(e.code), recovery=_recovery(e.recovery))
            for e in o.errors
        ]  # type: ignore[arg-type]
    for st in view.stories:
        if story_ids is None or st.id in story_ids:
            rec, acc = story_recs(st)
            out.stories.append(rec)
            out.acceptance += acc
    return out


def screen_refs(view: ModuleView) -> list[ScreenRef]:
    refs: list[ScreenRef] = []
    for s in view.screens:
        feats = sorted({p.ref for p in s.provenance if p.ref.startswith("FTR-")})
        ents = sorted(
            {b.split(".", 1)[0] for r in s.regions for b in r.bind if b.startswith("ENT-")}
        )
        refs.append(
            ScreenRef(
                id=s.id,
                name=s.name,
                system=s.system,
                role_ids=list(s.role_ids),
                feature_ids=feats,
                entity_ids=ents,
            )
        )
    return refs


# ------------------------------------------------------------------------------------------------ completion
def acceptance_for(st: StoryRec, role_name: str) -> AcceptRec:
    given = vocab.hints()["acceptance_given"].format(role=role_name)
    return AcceptRec(story=st.id, given=given, when=clip(st.want, 160), then=clip(st.so_that, 200))


def cover_stories(inp: ModuleIn, out: ModuleOut) -> list[tuple[StoryRec, AcceptRec]]:
    """An assumed story (voice usually) for every screen of this call that no story of `out` cites yet. It never invents a
    feature: it takes the screen's own, the roles' served ones, or nothing (then no story is made)."""
    role_name = _pairs(inp.role_names)
    cited = {sid for st in out.stories for sid in st.screen_ids}
    in_features = set(inp.in_feature_ids)
    by_screen = {s.id: s for s in inp.screens}
    taken = {s.id for s in out.stories}
    made: list[tuple[StoryRec, AcceptRec]] = []
    n = 0
    for sid in inp.cover_screen_ids:
        if sid in cited or sid not in by_screen:
            continue
        s = by_screen[sid]
        feats = [f for f in s.feature_ids if f in in_features] or _nearest_features(inp, s.role_ids)
        if not feats or not s.role_ids:
            continue
        n += 1
        sid_key = f"NEW-cover_{n}_{sid.lower().replace('-', '_')}"
        if sid_key in taken:
            continue
        st = StoryRec(
            id=sid_key,
            role=s.role_ids[0],
            want=f"use {s.name.lower()}",
            so_that=f"the person can do what {s.name.lower()} is for",
            feature_ids=feats[:2],
            screen_ids=[sid],
            voice="usually",
            prov=[f"assumed:no story cites {sid}"],
        )
        made.append((st, acceptance_for(st, role_name.get(st.role, st.role))))
        cited.add(sid)
    return made


def complete(inp: ModuleIn) -> ModuleOut:
    """The draft, completed in code: acceptance for every story and a story for every screen of this call that no story
    cites yet. Everything it adds is assumed (voice `usually`); a node never invents a feature."""
    out = inp.baseline.model_copy(deep=True)
    role_name = _pairs(inp.role_names)
    has_acc = {a.story for a in out.acceptance}
    for st in out.stories:
        if st.id not in has_acc:
            out.acceptance.append(acceptance_for(st, role_name.get(st.role, st.role)))
    for st, acc in cover_stories(inp, out):
        out.stories.append(st)
        out.acceptance.append(acc)
    return out


def _pairs(items: Sequence[str]) -> dict[str, str]:
    return dict(x.split("=", 1) for x in items if "=" in x)


def _nearest_features(inp: ModuleIn, role_ids: Sequence[str]) -> list[str]:
    """The in features the stories of the same roles already serve (a screen no feature names is read as part of them)."""
    served = _pairs(inp.role_features)
    in_features = set(inp.in_feature_ids)
    out: list[str] = []
    for r in role_ids:
        for f in served.get(r, "").split(","):
            if f and f in in_features and f not in out:
                out.append(f)
    return sorted(out)
