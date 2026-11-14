"""Assembling the input of every call, by CODE (doc 15 3.3): a summary of the Compass view for the plan, the module's own
slice for each module call, the integration needs, the flows and their screens. Nothing is a whole export; every list is
capped or split; user and research text goes only into fields the call spec declares untrusted."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from mde_spec_agent.derive.release import ReleasePlan
from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import clip
from mde_spec_agent.lanes.product.kit.env import Env
from mde_spec_agent.lanes.product.kit.research import ResearchRead, digest, dossiers_of, read_for
from mde_spec_agent.lanes.product.kit.slices import (
    all_ref_ids,
    chunk_lines,
    facts_lines,
    feature_line,
    flow_line,
    module_lines,
    module_view,
    role_line,
    screen_line,
)
from mde_spec_agent.lanes.product.plan.baseline import baseline_plan_out
from mde_spec_agent.lanes.product.plan.schema import PlanIn
from mde_spec_agent.lanes.product.prod_integ.baseline import baseline_integ_out
from mde_spec_agent.lanes.product.prod_integ.schema import IntegIn
from mde_spec_agent.lanes.product.prod_journeys.baseline import baseline_journeys_out
from mde_spec_agent.lanes.product.prod_journeys.schema import JourneysIn, ScreenActions
from mde_spec_agent.lanes.product.prod_module.baseline import baseline_module_out, screen_refs
from mde_spec_agent.lanes.product.prod_module.schema import ModuleIn
from mde_spec_agent.run.context import RunContext
from mde_spec_core import policy as core_policy
from mde_spec_core.callkit.fusion import items_per_call
from mde_spec_core.render import TokenCounter, render_slice


# ------------------------------------------------------------------------------------------------ shared pieces
@dataclass
class Prov:
    ref_ids: list[str] = field(default_factory=list)
    research_ids: list[str] = field(default_factory=list)
    research_labels: list[str] = field(default_factory=list)
    policy_refs: list[str] = field(default_factory=list)

    def kw(self) -> dict[str, list[str]]:
        return {
            "ref_ids": self.ref_ids,
            "research_ids": self.research_ids,
            "research_labels": self.research_labels,
            "policy_refs": self.policy_refs,
        }


def policy_refs() -> list[str]:
    sheet = core_policy.default_policy_sheet()
    return sorted(core_policy.policy_ref(r.id, sheet) for r in sheet.rules)


def prov_for(env: Env, read: ResearchRead | None) -> Prov:
    return Prov(
        ref_ids=all_ref_ids(env.graph, env.spec_input),
        research_ids=list(read.ids) if read else [],
        research_labels=list(read.labels) if read else [],
        policy_refs=policy_refs(),
    )


def source_language(env: Env) -> str:
    return env.graph.meta.source_language or "en"


def n_per_call(ctx: RunContext, kind: str, cap: int) -> int:
    avg = float(vocab.hints()["avg_item_tokens"][kind])
    return items_per_call(ctx.writer, avg, cap, ctx.config)


def counter_for(ctx: RunContext) -> TokenCounter:
    from mde_spec_core.callkit.prompt import chars_per_token

    return TokenCounter(chars_per_token(ctx.writer))


# ------------------------------------------------------------------------------------------------ plan
def _brief_lines(env: Env, release: ReleasePlan | None) -> list[str]:
    g, si = env.graph, env.spec_input
    m = g.meta
    lines: list[str] = []
    if si is not None:
        lines.append(f"IDEA {clip(si.idea.summary, 300)}")
        lines.append(
            f"TIER {si.tier}; size {si.size_band}; profile {m.profile}; languages {','.join(m.languages)}; "
            f"country {m.country or '-'}; thin input {si.thin_input}"
        )
    else:
        lines.append(f"PROFILE {m.profile}; languages {','.join(m.languages)}")
    for a in sorted(g.apps.values(), key=lambda x: x.id):
        lines.append(
            f"APP {a.id} {a.name} [{a.surface}] roles {','.join(a.role_ids)} modules {','.join(a.module_ids)}"
        )
    lines += [role_line(r) for r in sorted(g.roles.values(), key=lambda x: x.id)]
    lines += [feature_line(f) for f in sorted(g.features.values(), key=lambda x: x.id)]
    for mod in sorted(g.modules.values(), key=lambda x: x.id):
        lines.append(
            f"MODULE {mod.id} {mod.name}: {clip(mod.purpose, 160)} (apps {','.join(mod.app_ids)}, shared {mod.shared}, "
            f"screens {len(mod.screen_ids)}, entities {','.join(sorted(mod.entity_ids)) or '-'})"
        )
    for e in sorted(g.entities.values(), key=lambda x: x.id):
        lines.append(
            f"ENTITY {e.id} {e.name} [{e.klass}] owner {e.owner_module or '-'} fields {len(e.fields)} "
            f"states {','.join(e.lifecycle.states) or '-'}"
        )
    for p in sorted(g.personas.values(), key=lambda x: x.id):
        lines.append(f"PERSONA {p.id} role {p.role_id}: {clip(p.goal, 120)}")
    if si is not None:
        for it in si.integrations:
            lines.append(f"COMPASS INTEGRATION {it.name} [{it.preference}] {clip(it.notes, 120)}")
        for f in si.assumptions:
            lines.append(f"ASSUMED {f.field} = {clip(f.value, 80)} ({clip(f.reason, 80)})")
    return lines


def _release_lines(release: ReleasePlan | None) -> list[str]:
    if release is None:
        return []
    lines = [
        f"CAP {release.cap} screens per app and phase; everything in one release: {release.everything_in_one_release}"
    ]
    for fid, d in sorted(release.features.items()):
        lines.append(
            f"FEATURE {fid} {d.status}{(':' + d.reason) if d.reason else ''}"
            f"{' [user-evidenced must]' if d.user_evidenced_must else ''}{' [pulled in]' if d.pulled_in else ''}: {clip(d.why, 160)}"
        )
    for c in release.needs_consent:
        lines.append(
            f"CONSENT {c.kind} app {c.app}: {','.join(c.feature_ids)} ({clip(c.why, 140)})"
        )
    for p in release.pulled_in:
        lines.append(f"PULL-IN {p.screen_id} by {p.rule}: {clip(p.because, 140)}")
    for a in sorted(release.per_app.values(), key=lambda x: x.app):
        lines.append(
            f"APPCOUNT {a.app}: release {a.release} of cap {a.cap}, deferred for capacity {a.deferred_capacity}"
        )
    return lines


def plan_input(
    ctx: RunContext, env: Env, release: ReleasePlan | None, *, restructure: bool = True
) -> PlanIn:
    g = env.graph
    read = digest(ctx.research, max_items=int(vocab.hints()["research_digest_items"]))
    counter = counter_for(ctx)
    cap = ctx.config.budgets.slice_cap
    over: list[str] = []
    keys: list[str] = []
    inv: list[str] = []
    for s in sorted(g.screens.values(), key=lambda x: x.id):
        tok = counter.count(render_slice(s, g))
        flag = " OVER-CAP" if tok > cap else ""
        inv.append(f"{screen_line(s)} tokens~{tok}{flag}")
        if tok > cap:
            over.append(s.id)
            keys.append(
                f"{s.id}|regions={','.join(sorted(r.key for r in s.regions))}|actions={','.join(sorted(a.key for a in s.actions))}"
            )
    base = baseline_plan_out(g, release)
    feats = sorted(g.features)
    deferred = sorted(f for f in feats if env.spec_input is not None and _user_deferred(env, f))
    musts = sorted(
        f for f in feats if g.features[f].user_evidenced and g.features[f].user_priority == "must"
    )
    prov = prov_for(env, read)
    return PlanIn(
        **prov.kw(),
        language=source_language(env),
        profile=g.meta.profile,
        slice_cap_tokens=cap,
        scope_cap=ctx.config.scope.scope_cap_per_app_phase,
        brief=chunk_lines(_brief_lines(env, release)),
        inventory=chunk_lines(inv),
        release_lines=chunk_lines(_release_lines(release)),
        research=chunk_lines(read.lines),
        research_note=read.note,
        module_ids=sorted(g.modules),
        role_ids=sorted(g.roles),
        entity_ids=sorted(g.entities),
        screen_ids=sorted(g.screens),
        system_screen_ids=sorted(s.id for s in g.screens.values() if s.system),
        over_cap_screen_ids=over,
        feature_ids=feats,
        user_deferred=deferred,
        user_must=musts,
        screen_apps=[f"{s.id}={s.app_id}" for s in sorted(g.screens.values(), key=lambda x: x.id)],
        screen_roles=[
            f"{s.id}={','.join(s.role_ids)}" for s in sorted(g.screens.values(), key=lambda x: x.id)
        ],
        screen_keys=keys,
        module_apps=[
            f"{m.id}={','.join(m.app_ids)}" for m in sorted(g.modules.values(), key=lambda x: x.id)
        ],
        allow_restructure=restructure,
        baseline=base,
    )


def _user_deferred(env: Env, fid: str) -> bool:
    si = env.spec_input
    return any(f.id == fid and f.deferred_by_user for f in si.features)


# ------------------------------------------------------------------------------------------------ modules
Item = tuple[str, str]  # (kind, id): E entity, S story, R rule, C cover screen


@dataclass
class ModuleBatch:
    batch_id: str
    module_id: str
    items: list[Item]


def module_batches(ctx: RunContext, env: Env) -> list[ModuleBatch]:
    g, maps = env.graph, env.maps
    cited = {sid for st in g.stories.values() for sid in st.screen_ids}
    e_cap = ctx.config.fusion.entities_per_call_max
    n_e = n_per_call(ctx, "entity", e_cap)
    n_s = n_per_call(ctx, "story", int(vocab.hints()["story_items_cap"]))
    n_r = n_per_call(ctx, "rule", int(vocab.hints()["rule_items_cap"]))
    out: list[ModuleBatch] = []
    for mid in sorted(g.modules):
        v = module_view(g, maps, mid)
        ents = [e.id for e in v.entities]
        sts = [s.id for s in v.stories]
        rules = [r.id for r in v.rules]
        cover = [s.id for s in v.screens if not s.system and s.id not in cited]
        k = max(
            1,
            math.ceil(len(ents) / n_e),
            math.ceil(len(sts) / n_s),
            math.ceil(len(rules) / n_r),
        )
        if not (ents or sts or rules or cover):
            continue
        for i in range(k):
            items: list[Item] = (
                [("E", x) for x in ents[i * n_e : (i + 1) * n_e]]
                + [("S", x) for x in sts[i * n_s : (i + 1) * n_s]]
                + [("R", x) for x in rules[i * n_r : (i + 1) * n_r]]
                + ([("C", x) for x in cover] if i == 0 else [])
            )
            if items:
                out.append(ModuleBatch(f"prod_module:{mid}:{i:02d}", mid, items))
    return out


def module_input_factory(ctx: RunContext, env: Env):
    g, maps = env.graph, env.maps
    role_names = [f"{r.id}={r.name}" for r in sorted(g.roles.values(), key=lambda x: x.id)]
    served: dict[str, set[str]] = {}
    for st in g.stories.values():
        for f in st.feature_ids:
            if f in g.features and g.features[f].status == "in":
                served.setdefault(st.role_id, set()).add(f)
    if (
        env.spec_input is not None
    ):  # Compass's own journeys: a role's flow serves the features the flow names
        for fl in env.spec_input.structure.flows:
            if fl.role in g.roles:
                served.setdefault(fl.role, set()).update(
                    f for f in fl.features if f in g.features and g.features[f].status == "in"
                )
    role_features = [f"{r}={','.join(sorted(fs))}" for r, fs in sorted(served.items())]
    base_ref_ids = all_ref_ids(g, env.spec_input)
    in_features = sorted(f.id for f in g.features.values() if f.status == "in")

    def make(module_id: str):
        v = module_view(g, maps, module_id)

        def build(items: Sequence[Item]) -> ModuleIn:
            ents = [i for k, i in items if k == "E"]
            sts = [i for k, i in items if k == "S"]
            rules = [i for k, i in items if k == "R"]
            cover = [i for k, i in items if k == "C"]
            brief_ids = [
                module_id,
                *[f.id for f in v.features],
                *ents,
                *[s.id for s in v.screens],
            ]
            read = read_for(
                ctx.research, brief_ids, lane="blueprint", profile=ctx.writer, config=ctx.config
            )
            batch_stories = [g.stories[s] for s in sts]
            cover_features = sorted(
                {f for st in batch_stories for f in st.feature_ids if f in set(in_features)}
            )
            cover_screens = sorted(
                {sc for st in batch_stories for sc in st.screen_ids} | set(cover)
            )
            lines = module_lines(v, only_entities=set(ents), only_stories=set(sts))
            lines = [
                ln
                for ln in lines
                if not ln.startswith("RULE ") or ln.split(" ", 2)[1] in set(rules)
            ]
            return ModuleIn(
                ref_ids=base_ref_ids,
                research_ids=list(read.ids),
                research_labels=list(read.labels),
                policy_refs=policy_refs(),
                language=source_language(env),
                profile=g.meta.profile,
                module_id=module_id,
                module_name=v.module.name,
                app_ids=sorted(v.module.app_ids),
                slice=chunk_lines(lines),
                facts=chunk_lines(facts_lines(v.facts)),
                research=chunk_lines(read.lines),
                research_note=read.note,
                feature_ids=sorted(g.features),
                in_feature_ids=in_features,
                module_feature_ids=[f.id for f in v.features if f.status == "in"],
                feature_names=[f"{f.id}={f.name}" for f in g.features.values() if f.status == "in"],
                role_ids=sorted(g.roles),
                role_names=role_names,
                role_features=role_features,
                screen_ids=sorted(g.screens),
                entity_ids=[e.id for e in v.entities],
                rule_ids=[r.id for r in v.rules],
                story_ids=[s.id for s in v.stories],
                cover_feature_ids=cover_features,
                cover_screen_ids=cover_screens,
                screens=screen_refs(v),
                baseline=baseline_module_out(
                    g, v, entity_ids=set(ents), story_ids=set(sts), rule_ids=set(rules)
                ),
            )

        return build

    return make


def merged_module_input(ctx: RunContext, env: Env) -> ModuleIn:
    """All modules of a compact app as ONE module input (the fused call): the union of every module's draft."""
    make = module_input_factory(ctx, env)
    batches = module_batches(ctx, env)
    parts = [make(b.module_id)(b.items) for b in batches]
    if not parts:
        first = sorted(env.graph.modules)[0]
        return make(first)([])
    head = parts[0]
    head.baseline = type(head.baseline)(
        **{
            k: [x for p in parts for x in getattr(p.baseline, k)]
            for k in type(head.baseline).model_fields
        }
    )
    head.slice = chunk_lines([ln for p in parts for ln in "\n".join(p.slice).split("\n")])
    head.facts = chunk_lines(
        sorted({ln for p in parts for ln in "\n".join(p.facts).split("\n") if ln})
    )
    head.research = chunk_lines(
        sorted({ln for p in parts for ln in "\n".join(p.research).split("\n") if ln})
    )
    head.module_feature_ids = sorted({f for p in parts for f in p.module_feature_ids})
    head.cover_feature_ids = sorted({f for p in parts for f in p.cover_feature_ids})
    head.cover_screen_ids = sorted({f for p in parts for f in p.cover_screen_ids})
    head.entity_ids = sorted({f for p in parts for f in p.entity_ids})
    head.rule_ids = sorted({f for p in parts for f in p.rule_ids})
    head.story_ids = sorted({f for p in parts for f in p.story_ids})
    head.app_ids = sorted({f for p in parts for f in p.app_ids})
    seen_screens: dict[str, Any] = {}
    for p in parts:
        for sc in p.screens:
            seen_screens.setdefault(sc.id, sc)
    head.screens = [seen_screens[k] for k in sorted(seen_screens)]
    head.module_name = ", ".join(p.module_name for p in parts)
    head.research_ids = sorted({f for p in parts for f in p.research_ids})
    head.research_labels = sorted({f for p in parts for f in p.research_labels})
    return head


# ------------------------------------------------------------------------------------------------ integrations
def _provider_rows(ctx: RunContext, env: Env) -> list[str]:
    rows: list[str] = []
    for d in dossiers_of(ctx.research):
        needs = (
            {str(d.get("capability") or "")}
            | {str(x) for x in d.get("needs", [])}
            | {str(x) for x in d.get("integrations", [])}
        )
        for need in sorted(n for n in needs if n):
            rows.append(f"{d.get('id')}|{d.get('provider') or '-'}|{need}")
    si = env.spec_input
    if si is not None:
        for it in si.integrations:
            rows.append(f"-|{it.name}|{it.name.casefold()}")
        for ex in si.existing_systems:
            rows.append(f"-|{ex.name}|{ex.name.casefold()}")
    return sorted(set(rows))


def integ_batches(ctx: RunContext, env: Env) -> list[tuple[str, list[str]]]:
    ids = sorted(env.graph.integrations)
    n = n_per_call(ctx, "integration", ctx.config.fusion.integrations_per_call)
    return [(f"prod_integ:{i // n:02d}", ids[i : i + n]) for i in range(0, len(ids), n)]


def integ_input_factory(ctx: RunContext, env: Env):
    g = env.graph
    rows = _provider_rows(ctx, env)
    refs = all_ref_ids(g, env.spec_input)

    def build(ids: Sequence[str]) -> IntegIn:
        needs = [g.integrations[i] for i in ids]
        brief_ids = [f"INT:{n.need_key}" for n in needs if n.need_key] + list(ids)
        read = read_for(
            ctx.research, brief_ids, lane="integrations", profile=ctx.writer, config=ctx.config
        )
        lines: list[str] = []
        for n in needs:
            lines.append(
                f"NEED {n.id} {n.name} [need {n.need_key}, mode {n.mode}, provider-side {n.provider_side_flow}] "
                f"flow: {clip(n.flow, 200)} | interruption: {clip(n.interruption, 160)} | pending: {clip(n.pending_timeout, 160)} "
                f"| events {','.join(n.events) or '-'} | mock: {clip(n.mock_behaviour, 120)}"
            )
        lines += [
            f"PROVIDER {row}" for row in rows
        ]  # the closed list, shown inside the untrusted data block
        si = env.spec_input
        facts: list[str] = []
        if si is not None:
            for it in si.integrations:
                lines.append(
                    f"COMPASS INTEGRATION {it.name} [{it.preference}] {clip(it.notes, 160)}"
                )
            keys = {n.need_key.casefold() for n in needs} | {n.name.casefold() for n in needs}
            for f in sorted({*si.domain_facts, *si.rules_and_numbers}, key=lambda x: x.id):
                if any(k and k in f.statement.casefold() for k in keys):
                    facts.append(f"{f.id}: {clip(f.statement, 220)}")
        return IntegIn(
            ref_ids=refs,
            research_ids=list(read.ids),
            research_labels=list(read.labels),
            policy_refs=policy_refs(),
            language=source_language(env),
            profile=g.meta.profile,
            slice=chunk_lines(lines),
            facts=chunk_lines(facts[:60]),
            research=chunk_lines(read.lines),
            research_note=read.note,
            integration_ids=list(ids),
            provider_names=rows,
            baseline=baseline_integ_out(g, ids),
        )

    return build


# ------------------------------------------------------------------------------------------------ journeys
def journey_batches(ctx: RunContext, env: Env) -> list[tuple[str, list[str]]]:
    # variants are written with the draft flow they vary, so only draft flows (variant main) are batch items
    ids = sorted(f.id for f in env.graph.flows.values() if f.variant == "main")
    n = n_per_call(ctx, "flow", ctx.config.fusion.flows_per_journey_call)
    return [(f"prod_journeys:{i // n:02d}", ids[i : i + n]) for i in range(0, len(ids), n)]


def variants_wanted(env: Env, flow_ids: Sequence[str]) -> list[str]:
    """Deep apps: first run, returning and recovery for every primary journey, cross app for one that spans apps."""
    g = env.graph
    if g.meta.profile != "deep":
        return []
    rows: list[str] = []
    for fid in flow_ids:
        f = g.flows[fid]
        if not f.primary:
            continue
        rows += [f"first_run:{fid}", f"returning:{fid}", f"recovery:{fid}"]
        apps = {g.screens[s].app_id for st in f.stages for s in st.screen_ids if s in g.screens}
        if len(apps) > 1:
            rows.append(f"cross_app:{fid}")
    return rows


def journeys_input_factory(ctx: RunContext, env: Env):
    g = env.graph
    refs = all_ref_ids(g, env.spec_input)
    screens = [
        ScreenActions(
            id=s.id,
            app_id=s.app_id,
            role_ids=list(s.role_ids),
            action_keys=sorted(a.key for a in s.actions),
        )
        for s in sorted(g.screens.values(), key=lambda x: x.id)
    ]

    def build(ids: Sequence[str]) -> JourneysIn:
        flows = [g.flows[i] for i in ids]
        used = sorted(
            {sid for f in flows for st in f.stages for sid in st.screen_ids if sid in g.screens}
        )
        brief_ids = [*ids, *[x for f in flows for st in f.stages for x in st.screen_ids]]
        read = read_for(
            ctx.research, brief_ids, lane="blueprint", profile=ctx.writer, config=ctx.config
        )
        lines = [flow_line(f) for f in flows]
        for f in flows:
            lines.append(
                f"FLOW {f.id} alternates: {' | '.join(clip(u, 140) for u in f.unhappy_paths) or '-'}"
            )
        lines += [
            f"SCREEN {s.id} {s.name} [app {s.app_id}] roles {','.join(s.role_ids)} actions {','.join(sorted(a.key for a in s.actions)) or '-'}"
            for s in (g.screens[i] for i in used)
        ]
        si = env.spec_input
        facts: list[str] = []
        if si is not None:
            comp = {f.id: f for f in si.structure.flows}
            for f in flows:
                cf = comp.get(f.id)
                if cf is not None:
                    facts += [f"{f.id} acceptance: {clip(a, 200)}" for a in cf.acceptance]
        return JourneysIn(
            ref_ids=refs,
            research_ids=list(read.ids),
            research_labels=list(read.labels),
            policy_refs=policy_refs(),
            language=source_language(env),
            profile=g.meta.profile,
            slice=chunk_lines(lines),
            facts=chunk_lines(facts),
            research=chunk_lines(read.lines),
            research_note=read.note,
            flow_ids=list(ids),
            variants_wanted=variants_wanted(env, ids),
            primary_only_emotion=g.meta.profile == "compact",
            role_ids=sorted(g.roles),
            app_ids=sorted(g.apps),
            screens=screens,
            baseline=baseline_journeys_out(g, ids),
        )

    return build


# ------------------------------------------------------------------------------------------------ quotes and names for gate 1
def quotes_of(env: Env) -> dict[str, str]:
    return {fid: f.statement for fid, f in env.maps.facts.items()}


def brand_names_of(ctx: RunContext, env: Env) -> set[str]:
    out: set[str] = set()
    for row in _provider_rows(ctx, env):
        out |= {p for p in row.split("|")[:2] if p and p != "-"}
    return out
