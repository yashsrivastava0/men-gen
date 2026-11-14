"""Code assembles each card's input: the frozen slice of the graph that one screen needs, plus the baseline card that code
already wrote (the model improves it). Everything here is read-only on the graph. Research text is DATA: it reaches the
model only inside the call's untrusted blocks."""

from __future__ import annotations

from typing import Any

from mde_spec_agent.lanes.screens.common import LaneRules
from mde_spec_agent.lanes.screens.scrn.build import guard_text, step_text
from mde_spec_agent.lanes.screens.scrn.schema import (
    ActionOut,
    CardOut,
    CopyOut,
    EntIn,
    InputOut,
    LibAcceptIn,
    MomentIn,
    NormIn,
    OpIn,
    PatternIn,
    RegionOut,
    SlotIn,
)
from mde_spec_core import libraries, platform_data, vocab
from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import Entity, Screen, SpecGraph
from mde_spec_core.ids import namespaced_copy_key
from mde_spec_core.mock import status_key
from mde_spec_core.validators.release import screen_features


def vocab_lines() -> list[str]:
    """The closed vocabularies, one line each (data, rendered by code; the prompt never lists them by hand)."""
    lines = [
        "shapes: " + " | ".join(sorted(vocab.shapes())) + " | other:<name>",
        "cardinality buckets: " + " | ".join(vocab.shapes_file().cardinality_buckets),
    ]
    for sh in vocab.shapes().values():
        attrs = [f"{a.name}=" + ("|".join(a.values) if a.values else a.type) for a in sh.attributes]
        if attrs:
            lines.append(f"attributes of {sh.name}: " + "; ".join(attrs))
    lines += [
        "common attributes: "
        + "; ".join(
            f"{a.name}=" + ("|".join(a.values) if a.values else a.type)
            for a in vocab.shapes_file().common_attributes
        ),
        "boolean attributes (type bool) take exactly true or false, nothing else",
        "guards (the only forms): signed_in | role_has_access | field_equals ENT-x.field value | field_in ENT-x.field a|b",
        "intents: " + " | ".join(sorted(vocab.intents())),
        "input types: " + " | ".join(sorted(vocab.input_types())),
        "region importance: " + " | ".join(vocab.region_importances()),
        "action importance: " + " | ".join(vocab.action_importances()),
        "motion intents: " + " | ".join(sorted(vocab.motion_intents())),
        "mock profiles: " + " | ".join(sorted(vocab.mock_profiles())),
    ]
    return lines


def _ent_line(e: Entity, screen: Screen) -> EntIn:
    levels = []
    for row in e.access:
        if row.role_id in screen.role_ids:
            levels.append(
                next(
                    (c for c in ("manage", "edit", "create", "view", "none") if c in row.actions),
                    "none",
                )
            )
    access = (
        max(set(levels), key=["none", "view", "create", "edit", "manage"].index)
        if levels
        else "view"
    )
    fields = []
    for f in e.fields:
        t = f"{f.key}:{f.type}"
        if f.enum_values:
            t += ":" + "|".join(f.enum_values)
        fields.append(t)
    return EntIn(
        id=e.id,
        name=e.name,
        klass=e.klass,
        status_field=status_key(e),
        states=list(e.lifecycle.states),
        fields=fields,
        access=access,
        typical=e.typical_count,
        maximum=e.max_count,
    )


def _entities_of(screen: Screen, graph: SpecGraph) -> list[str]:
    ids: list[str] = []
    for r in screen.regions:
        for b in r.bind:
            if b.startswith("OP-"):
                op = graph.operations.get(b)
                if op and op.entity and op.entity not in ids:
                    ids.append(op.entity)
            elif "." in b:
                e = b.split(".")[0]
                if e not in ids:
                    ids.append(e)
    for a in screen.actions:
        for s in a.effect:
            if s.verb == "call" and s.target in graph.operations:
                e = graph.operations[s.target].entity
                if e and e not in ids:
                    ids.append(e)
    return [e for e in ids if e in graph.entities]


def _ops_of(screen: Screen, graph: SpecGraph, eids: list[str]) -> list[OpIn]:
    """Operations the card may use: those it already uses, and every operation of the entities it binds."""
    ids: list[str] = []
    for r in screen.regions:
        ids += [b for b in r.bind if b.startswith("OP-")]
    for a in screen.actions:
        ids += [s.target or "" for s in a.effect if s.verb == "call"]
    for oid, op in sorted(graph.operations.items()):
        if op.entity in eids and (not op.roles or set(op.roles) & set(screen.role_ids)):
            ids.append(oid)
    out: list[OpIn] = []
    for oid in dict.fromkeys(ids):
        op = graph.operations.get(oid)
        if op is None:
            continue
        leaves: set[str] = set()
        ent = graph.entities.get(op.entity or "")
        if ent:
            leaves = {t.from_state for t in ent.lifecycle.transitions if t.on == oid}
        out.append(
            OpIn(
                id=oid,
                name=op.name,
                entity=op.entity,
                method=op.method,
                errors=[e.code for e in op.errors],
                idempotent=op.idempotent,
                from_states=sorted(leaves),
            )
        )
    return out


def baseline_records(
    s: Screen, graph: SpecGraph, copy: dict[str, str], mock_states: list[str], profiles: list[str]
) -> tuple[CardOut, list[RegionOut], list[ActionOut], list[InputOut], list[CopyOut]]:
    card = CardOut(
        screen=s.id,
        purpose=s.purpose,
        params=[f"{p.name}:{p.type}:{p.source}" for p in s.route.params],
        guards=[guard_text(g) for g in s.route.guards],
        role_conditions=s.role_conditions or None,
        reached_from=list(s.reached_from),
        leads_to=list(s.leads_to),
        accessibility=s.accessibility or None,
        analytics=list(s.analytics),
        mock_profiles=list(s.mock_profiles) or profiles,
        mock_states=list(s.hero_refs) or mock_states,
        confirm=list(s.confirm),
    )
    regions = [
        RegionOut(
            screen=s.id,
            key=r.key,
            what=r.what,
            shape=r.shape,
            shape_kind=r.shape_kind,
            attrs=list(r.attrs),
            bind=list(r.bind),
            importance=r.importance,
            freshness=r.freshness,
            cardinality=r.cardinality,
            typical=r.typical,
            maximum=r.maximum,
            live=r.live,
        )
        for r in sorted(s.regions, key=lambda x: x.key)
    ]
    actions = [
        ActionOut(
            screen=s.id,
            key=a.key,
            importance=a.importance,
            intent=a.intent,
            what=a.what,
            precondition=a.precondition or None,
            effect=[step_text(x) for x in a.effect],
            guard=[guard_text(g) for g in a.guard],
            errors=list(a.errors),
            flags=list(a.flags),
            unspecified=a.unspecified,
        )
        for a in sorted(s.actions, key=lambda x: x.key)
    ]
    inputs = [
        InputOut(
            screen=s.id,
            key=i.key,
            type=i.type,
            required=i.required,
            constraint=i.constraint or None,
            default=i.default,
            source=i.source,
        )
        for i in s.inputs
    ]
    texts = [
        CopyOut(
            screen=s.id,
            target="title",
            ref="title",
            text=copy.get(namespaced_copy_key(s.id, "title"), s.name),
        )
    ]
    for a in s.actions:
        texts.append(
            CopyOut(
                screen=s.id,
                target="action",
                ref=a.key,
                text=copy.get(namespaced_copy_key(s.id, a.key), a.what),
            )
        )
    for i in s.inputs:
        texts.append(
            CopyOut(
                screen=s.id,
                target="input",
                ref=i.key,
                text=copy.get(namespaced_copy_key(s.id, i.key), i.key),
            )
        )
    return card, regions, actions, inputs, texts


def _moments(s: Screen, graph: SpecGraph, rules: LaneRules) -> list[MomentIn]:
    out: list[MomentIn] = []
    for fid, f in sorted(graph.flows.items()):
        if f.status != "in":
            continue
        for st in sorted(f.stages, key=lambda x: x.n):
            if s.id in st.screen_ids:
                out.append(
                    MomentIn(
                        flow=fid,
                        flow_name=f.name,
                        step=st.n,
                        goal=st.goal,
                        emotion=st.emotion,
                        assumed=st.emotion_assumed,
                        primary=f.primary,
                        matters=st.n in f.moments_that_matter,
                    )
                )
    out.sort(
        key=lambda m: (not (m.primary and m.matters), not m.matters, not m.primary, m.flow, m.step)
    )
    return out[: rules.slots_moments_max]


def norms_for(
    s: Screen, graph: SpecGraph, eids: list[str], research: Any, rules: LaneRules
) -> list[NormIn]:
    """Research items that describe peer practice for this screen: by the features, entities and roles it serves. Never
    a competitor or a provider fact, never from the appendix store. Evidence is the item's own label."""
    if research is None or (getattr(research, "thin", True) and not getattr(research, "items", ())):
        return []
    feats = screen_features(graph, s)
    refs = feats | set(eids) | set(s.role_ids) | {s.app_id}
    for fid, f in graph.flows.items():
        if any(s.id in st.screen_ids for st in f.stages):
            refs.add(fid)
    out: list[tuple[int, str, NormIn]] = []
    for it in research.items:
        if it.kind not in rules.usually_item_kinds or getattr(it, "store", "main") != "main":
            continue
        if not any(a.kind in rules.usually_applies_kinds and a.id in refs for a in it.applies_to):
            continue
        label = " ".join((it.claim or it.label).split())[:240]
        out.append(
            (
                it.rank if it.rank is not None else 9999,
                it.id,
                NormIn(id=it.id, evidence=it.evidence, label=label),
            )
        )
    out.sort(key=lambda t: (t[0], t[1]))
    return [n for _, _, n in out[: rules.slots_norms_max]]


def delight_for(s: Screen, rules: LaneRules) -> list[PatternIn]:
    ids = rules.delight_by_screen_type.get(s.screen_type, [])
    lib = platform_data.default_delight_patterns()
    out: list[PatternIn] = []
    for pid in ids[: rules.slots_delight_max]:
        try:
            p = lib.get(pid)
        except KeyError:
            continue
        out.append(
            PatternIn(
                id=p.id,
                moment=p.moment,
                effect=p.effect,
                tier=p.tier,
                reduced_motion_alternative=p.reduced_motion_alternative,
                motion=rules.motion_by_pattern.get(p.id),
            )
        )
    return out


def build_slot(
    s: Screen,
    graph: SpecGraph,
    cfg: SpecConfig,
    rules: LaneRules,
    *,
    research: Any = None,
    copy: dict[str, str],
    incoming: dict[str, list[str]],
) -> SlotIn:
    profile = graph.meta.profile
    eids = _entities_of(s, graph)
    ents = [_ent_line(graph.entities[e], s) for e in eids]
    ops = _ops_of(s, graph, eids)
    mock_states = [f"{e.id}:{st}" for e in ents for st in e.states[:3]]
    profiles = list(cfg.profiles.mock_profiles[profile][:2])
    card, regions, actions, inputs, texts = baseline_records(
        s, graph, copy, mock_states[:2], profiles
    )
    if incoming.get(s.id) and not card.reached_from:
        card = card.model_copy(update={"reached_from": incoming[s.id]})
    lib = libraries.state_library_for(s.screen_type, profile)
    feats = []
    for fid in sorted(screen_features(graph, s)):
        f = graph.features.get(fid)
        if f:
            feats.append(f"{fid} {f.name}: {f.intent} [{f.user_priority}]".strip())
    known: list[str] = []
    for tid in sorted(
        {t for a in s.actions for st in a.effect if st.verb == "go" and (t := st.target)}
        | set(s.leads_to)
    ):
        t = graph.screens.get(tid)
        if t:
            known.append(f"{tid} {t.name} ({t.screen_type})")
    integ = [
        f"{i.id} {i.name} ({i.mode})" + ("; provider-side flow" if i.provider_side_flow else "")
        for i in sorted(graph.integrations.values(), key=lambda x: x.id)
        if any(
            st.verb == "external_flow" and st.target in (i.need_key, i.id, i.name)
            for a in s.actions
            for st in a.effect
        )
    ]
    confirm = [
        f"{r.id} {r.subject}: {r.statement}"
        for r in sorted(graph.rules.values(), key=lambda x: x.id)
        if r.confirm and (r.module_id is None or r.module_id == s.module_id)
    ][:3]
    return SlotIn(
        id=s.id,
        name=s.name,
        screen_type=s.screen_type,
        app=f"{s.app_id} {graph.apps[s.app_id].name}" if s.app_id in graph.apps else s.app_id,
        module=f"{s.module_id} {graph.modules[s.module_id].name}"
        if s.module_id in graph.modules
        else (s.module_id or ""),
        surface=s.surface,
        roles=[f"{r} {graph.roles[r].name}" if r in graph.roles else r for r in s.role_ids],
        features=feats,
        route_path=s.route.path,
        entities=ents,
        operations=ops,
        known_screens=known,
        flows=_moments(s, graph, rules),
        integrations=integ,
        confirm_rules=confirm,
        norms=norms_for(s, graph, eids, research, rules),
        delight=delight_for(s, rules),
        library_states=[f"{t.kind}: {t.message}" for t in lib.states],
        library_acceptance=[
            LibAcceptIn(given=a.given, when=a.when, then=a.then)
            for a in (x.to_acceptance(s.name) for x in lib.acceptance)
        ],
        extra_acceptance_max=rules.acceptance_extra_max.get(profile, 1),
        could_max=min(cfg.profiles.could_cap_per_screen, rules.could_per_screen_default),
        usually_max=rules.usually_max,
        mock_state_options=mock_states,
        mock_profile_options=list(cfg.profiles.mock_profiles[profile]),
        card=card,
        regions=regions,
        actions=actions,
        inputs=inputs,
        texts=texts,
    )


def incoming_edges(graph: SpecGraph) -> dict[str, list[str]]:
    """Which screens lead to each screen, as 'SCR-x.action_key' (code reads the baseline navigation)."""
    inc: dict[str, list[str]] = {}
    for sid, s in sorted(graph.screens.items()):
        if s.status != "in":
            continue
        for a in s.actions:
            for st in a.effect:
                if st.verb == "go" and st.target:
                    inc.setdefault(st.target, []).append(f"{sid}.{a.key}")
    return {k: sorted(set(v)) for k, v in inc.items()}
