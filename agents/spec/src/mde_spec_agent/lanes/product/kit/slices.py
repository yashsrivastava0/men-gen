"""Slices of the graph, the Compass input and the Research view, assembled by CODE for every call (doc 15 3.3).

Everything here is plain text or plain records built from the code-made baseline graph; the model never fetches
anything. Lines are chunked below the gateway's untrusted-block cap so nothing is cut in the middle of a line.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mde_spec_core.graph import (
    Entity,
    Feature,
    Flow,
    Module,
    Role,
    Rule,
    Screen,
    SpecGraph,
    Story,
)

from .common import clip, snake

CHUNK_CHARS = 15_000
MAX_FACTS = 60


def chunk_lines(lines: Iterable[str], limit: int = CHUNK_CHARS) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for raw in lines:
        line = raw if len(raw) <= limit else raw[: limit - 1] + "…"
        if cur and size + len(line) + 1 > limit:
            out.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def lines_of(chunks: Iterable[str]) -> list[str]:
    return [ln for c in chunks for ln in c.split("\n") if ln.strip()]


def est_tokens(text: str, chars_per_token: float = 3.0) -> int:
    return -(-len(text) // max(1, int(chars_per_token)))


# ------------------------------------------------------------------------------------------------ maps
@dataclass
class Maps:
    """Who owns what, decided once from the baseline graph (deterministic, total and exclusive)."""

    feature_module: dict[str, str] = field(default_factory=dict)
    story_module: dict[str, str] = field(default_factory=dict)
    rule_module: dict[str, str] = field(default_factory=dict)
    flow_module: dict[str, str] = field(default_factory=dict)
    module_features: dict[str, list[str]] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)  # FCT id -> compass Fact


def _facts_of(spec_input: Any) -> dict[str, Any]:
    if spec_input is None:
        return {}
    out: dict[str, Any] = {}
    for f in [*spec_input.domain_facts, *spec_input.rules_and_numbers]:
        out.setdefault(f.id, f)
    return out


def build_maps(graph: SpecGraph, spec_input: Any) -> Maps:
    m = Maps(facts=_facts_of(spec_input))
    modules = sorted(graph.modules)
    first = modules[0] if modules else ""
    # a feature belongs to the module whose (non-system) screens cite it most; ties go to the lowest module id
    votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in graph.screens.values():
        if s.module_id is None or s.system:
            continue
        for p in s.provenance:
            if p.ref.startswith("FTR-"):
                votes[p.ref][s.module_id] += 1
    if spec_input is not None:
        for mod in spec_input.modules:
            for fid in mod.features:
                if mod.id in graph.modules:
                    votes[fid][mod.id] += 1
    for fid in sorted(graph.features):
        v = votes.get(fid)
        if v:
            m.feature_module[fid] = min(v, key=lambda k, v=v: (-v[k], k))
    for fid, mid in m.feature_module.items():
        m.module_features.setdefault(mid, []).append(fid)
    for mid in m.module_features:
        m.module_features[mid].sort()
    for st in graph.stories.values():
        m.story_module[st.id] = _story_module(graph, st, m, first)
    for r in graph.rules.values():
        m.rule_module[r.id] = _rule_module(graph, r, m, first)
    for fl in graph.flows.values():
        counts: dict[str, int] = defaultdict(int)
        for stg in fl.stages:
            for sid in stg.screen_ids:
                sc = graph.screens.get(sid)
                if sc and sc.module_id and not sc.system:
                    counts[sc.module_id] += 1
        m.flow_module[fl.id] = sorted(counts, key=lambda k: (-counts[k], k))[0] if counts else first
    return m


def _story_module(graph: SpecGraph, st: Story, m: Maps, first: str) -> str:
    for sid in st.screen_ids:
        sc = graph.screens.get(sid)
        if sc and sc.module_id and not sc.system:
            return sc.module_id
    for fid in st.feature_ids:
        if fid in m.feature_module:
            return m.feature_module[fid]
    return first


def _rule_module(graph: SpecGraph, r: Rule, m: Maps, first: str) -> str:
    if r.module_id and r.module_id in graph.modules:
        return r.module_id
    for p in r.provenance:
        f = m.facts.get(p.ref)
        if f is not None:
            for ref in f.refs:
                if ref in m.feature_module:
                    return m.feature_module[ref]
                if ref in graph.entities and graph.entities[ref].owner_module:
                    return graph.entities[ref].owner_module or first
    text = f"{r.subject} {r.statement}".casefold()
    for e in sorted(graph.entities.values(), key=lambda x: x.id):
        if e.name.casefold() in text and e.owner_module:
            return e.owner_module
    return first


# ------------------------------------------------------------------------------------------------ views
@dataclass
class ModuleView:
    module: Module
    features: list[Feature]
    screens: list[Screen]
    entities: list[Entity]
    stories: list[Story]
    rules: list[Rule]
    flows: list[Flow]
    roles: list[Role]
    facts: list[Any]


def module_view(graph: SpecGraph, maps: Maps, module_id: str) -> ModuleView:
    mod = graph.modules[module_id]
    screens = [graph.screens[i] for i in sorted(mod.screen_ids) if i in graph.screens]
    role_ids = sorted({r for s in screens for r in s.role_ids} | set())
    ents = [
        e
        for e in sorted(graph.entities.values(), key=lambda x: x.id)
        if e.owner_module == module_id
    ]
    feats = [
        graph.features[i] for i in maps.module_features.get(module_id, []) if i in graph.features
    ]
    fids = {f.id for f in feats}
    eids = {e.id for e in ents}
    names = {e.name.casefold() for e in ents}
    facts: list[Any] = []
    for fid in sorted(maps.facts):
        f = maps.facts[fid]
        if set(f.refs) & (fids | eids) or any(n in f.statement.casefold() for n in names):
            facts.append(f)
    return ModuleView(
        module=mod,
        features=feats,
        screens=screens,
        entities=ents,
        stories=[
            graph.stories[i] for i in sorted(graph.stories) if maps.story_module.get(i) == module_id
        ],
        rules=[graph.rules[i] for i in sorted(graph.rules) if maps.rule_module.get(i) == module_id],
        flows=[graph.flows[i] for i in sorted(graph.flows) if maps.flow_module.get(i) == module_id],
        roles=[graph.roles[r] for r in role_ids if r in graph.roles],
        facts=facts[:MAX_FACTS],
    )


# ------------------------------------------------------------------------------------------------ lines
def feature_line(f: Feature) -> str:
    flags = [f.status, f.user_priority]
    if f.user_evidenced:
        flags.append("user-evidenced")
    if f.later_reason:
        flags.append(f"later:{f.later_reason}")
    return f"FEATURE {f.id} [{', '.join(flags)}] {f.name}: {clip(f.intent, 160)}"


def role_line(r: Role) -> str:
    return f"ROLE {r.id} {r.name}: {clip(r.goal, 120)} (apps {','.join(r.app_ids)})"


def screen_line(s: Screen) -> str:
    feats = sorted({p.ref for p in s.provenance if p.ref.startswith("FTR-")})
    ents = sorted({b.split(".", 1)[0] for r in s.regions for b in r.bind if b.startswith("ENT-")})
    keys = sorted(a.key for a in s.actions)
    sys_flag = " system" if s.system else ""
    return (
        f"SCREEN {s.id} {s.name} [{s.screen_type}{sys_flag}] app {s.app_id} roles {','.join(s.role_ids)} "
        f"features {','.join(feats) or '-'} entities {','.join(ents) or '-'} actions {','.join(keys) or '-'}"
    )


def entity_line(e: Entity) -> str:
    fields = ", ".join(
        f"{f.key}:{f.type}{'*' if f.required else ''}"
        + (f"[{'|'.join(f.enum_values)}]" if f.enum_values else "")
        + (f"->{f.ref_entity}" if f.ref_entity else "")
        for f in e.fields
    )
    access = " ".join(f"{a.role_id}={'/'.join(a.actions)}" for a in e.access)
    out = f"ENTITY {e.id} {e.name} [{e.klass}] owner {e.owner_module or '-'} fields: {fields}"
    if e.lifecycle.states:
        tr = "; ".join(
            f"{t.from_state}>{t.to_state} by {','.join(t.roles) or '-'}"
            for t in e.lifecycle.transitions
        )
        out += f" | lifecycle {' > '.join(e.lifecycle.states)} (initial {e.lifecycle.initial or '-'}); {tr}"
    return out + (f" | access {access}" if access else "")


def story_line(s: Story) -> str:
    return (
        f"STORY {s.id} ({s.role_id}) [{s.voice}] want: {clip(s.want, 100)} so that: {clip(s.so_that, 120)} "
        f"features {','.join(s.feature_ids) or '-'} screens {','.join(s.screen_ids) or '-'}"
    )


def rule_line(r: Rule) -> str:
    refs = ",".join(p.ref for p in r.provenance)
    val = f" value={r.value} {r.unit or ''}".rstrip() if r.value else ""
    return f"RULE {r.id} [{refs}] {clip(r.statement, 200)}{val}"


def flow_line(f: Flow) -> str:
    stages = " > ".join(f"{s.n}:{clip(s.goal, 50)}[{','.join(s.screen_ids)}]" for s in f.stages)
    return f"FLOW {f.id} {f.name} ({','.join(f.role_ids)}) [{f.variant}{' primary' if f.primary else ''}] {stages}"


def fact_line(f: Any) -> str:
    return f"{f.id}: {clip(f.statement, 220)}"


def module_lines(
    v: ModuleView,
    *,
    only_entities: Collection[str] | None = None,
    only_stories: Collection[str] | None = None,
) -> list[str]:
    ents = [e for e in v.entities if only_entities is None or e.id in only_entities]
    sts = [s for s in v.stories if only_stories is None or s.id in only_stories]
    lines = [
        f"MODULE {v.module.id} {v.module.name}: {clip(v.module.purpose, 200)} (apps {','.join(v.module.app_ids)}, shared {v.module.shared})"
    ]
    lines += [feature_line(f) for f in v.features]
    lines += [role_line(r) for r in v.roles]
    lines += [screen_line(s) for s in v.screens]
    lines += [entity_line(e) for e in ents]
    lines += [story_line(s) for s in sts]
    lines += [rule_line(r) for r in v.rules]
    lines += [flow_line(f) for f in v.flows]
    return lines


def facts_lines(facts: Sequence[Any]) -> list[str]:
    return [fact_line(f) for f in facts]


def screen_ref_ids(graph: SpecGraph) -> list[str]:
    return sorted(graph.screens)


def all_ref_ids(graph: SpecGraph, spec_input: Any) -> list[str]:
    ids: set[str] = set()
    for t in (
        "features",
        "roles",
        "apps",
        "modules",
        "entities",
        "screens",
        "flows",
        "integrations",
    ):
        ids |= set(getattr(graph, t))
    if spec_input is not None:
        ids |= {f.id for f in [*spec_input.domain_facts, *spec_input.rules_and_numbers]}
        ids |= {a.id for a in spec_input.compass_assumptions}
    return sorted(ids)


def slug_key(text: str, taken: set[str], fallback: str = "item") -> str:
    base = snake(text, fallback)[:40]
    key, n = base, 2
    while key in taken:
        key = f"{base}_{n}"
        n += 1
    taken.add(key)
    return key


def mapping_get(m: Mapping[str, Any], k: str, default: Any = None) -> Any:
    return m.get(k, default)
