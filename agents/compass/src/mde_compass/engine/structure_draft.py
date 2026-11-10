"""Drafting the structure of the app (ADR 0017): outline, parallel module waves, merge, checks, critic, ONE repair pass.

``compute_structure`` is the whole pipeline over a WORKING COPY of the Brief and a set of ports. It reads nothing from a
turn and writes nothing to one: it returns a ``StructureResult`` whose ``work`` copy holds the new structure, the ids it
allocated and the digests it computed. ``engine/structure_controller`` applies a result to the turn after the gather in
which it ran (``_seed`` rebuilds ``turn.brief`` meanwhile, so nothing here may hold the turn's Brief across an await).

Order of events (plan section 5):

1. the outline call (modules, entity names, journeys), unless the structural inputs are unchanged since the last draft;
2. one ``cmp.draft_module`` call per group of modules, in parallel (group size from the size band), only for modules
   whose own inputs changed;
3. merge: resolve refs, allocate ids, keep what the person pinned, assemble the cross-module flows from journey legs;
4. deterministic fixes, the checks, and (medium and large) the findings-only critic;
5. ONE repair-and-expansion pass over the modules that still have a problem a model should fix, then the fixes and
   the checks again;
6. evidence from the person's own words (Locked where a verified fact says so), the provenance guard, the digests.

A call that fails answers with its deterministic floor, flagged and counted. The result lists what is left over.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from mde_compass.catalog.fact_kinds import kind_for
from mde_compass.catalog.size_bands import SizeBand
from mde_compass.catalog.structure_patterns import load_structure_patterns
from mde_compass.domain.brief import Brief, DomainFact
from mde_compass.domain.interview import Fact
from mde_compass.engine import structure_checks as checks
from mde_compass.engine import structure_derive as derive
from mde_compass.engine import structure_merge as merge
from mde_compass.engine.ports import Ports, TurnContext
from mde_compass.engine.structure_wire import (
    FINDING_CODES,
    AppWire,
    EntityCtx,
    ExistingScreen,
    FeatureWire,
    JourneyCtx,
    LegCtx,
    ModuleCtx,
    ModuleInput,
    OutlineInput,
    PinnedWire,
    ReviewInput,
    ReviewOutput,
    RoleWire,
    band_wire,
)

OUTLINE_KEY = "structure:outline"
MODULE_KEY = "structure:module:"
MAX_FACT_LINES = (
    120  # facts handed to one module call; every detail fact is still routed to a module
)
SEED_LINES = 60
KEYS_OF_STRUCTURE = ("modules", "screens", "flows", "entities")


@dataclass
class StructureInput:
    brief: Brief  # a deep copy: the work area (it is changed)
    facts: Sequence[Fact]  # the active ledger
    language: str
    band: SizeBand
    project_card: str
    org: str
    fact_digest: str
    now: datetime
    instruction: str = ""  # the person's own words for a regeneration
    only_modules: tuple[str, ...] | None = None  # redraft just these modules (an explicit action)
    force: bool = False  # ignore the digests (the person asked for a new band or a new plan)


@dataclass
class StructureResult:
    work: Brief
    changed: bool = False
    calls: int = 0
    fallbacks: int = 0
    reasons: list[str] = field(default_factory=list)
    issues: list[checks.Issue] = field(
        default_factory=list
    )  # what is still wrong after the repair pass
    notes: list[str] = field(default_factory=list)  # plain lines about what code did
    digests: dict[str, str] = field(default_factory=dict)  # every ``structure:*`` digest, current
    drafted: list[str] = field(default_factory=list)  # module ids drafted by a call this run


def structure_dump(brief: Brief) -> str:
    data = brief.model_dump(mode="json")
    return json.dumps(
        {k: data.get(k, []) for k in KEYS_OF_STRUCTURE}, sort_keys=True, ensure_ascii=False
    )


def _digest(parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------------------------------------- input builders


def feature_wires(brief: Brief) -> list[FeatureWire]:
    return [
        FeatureWire(
            id=f.id,
            name=f.name,
            intent=f.intent,
            priority=f.priority,
            scope_status=f.scope_status,
            family=f.family or "",
            apps=list(f.apps),
        )
        for f in brief.scope
    ]


def role_wires(brief: Brief) -> list[RoleWire]:
    return [RoleWire(id=r.id, name=r.name_in_user_words, goal=r.goal) for r in brief.roles]


def app_wires(brief: Brief) -> list[AppWire]:
    return [AppWire(id=a.id, name=a.name, surface=a.surface) for a in brief.apps]


def auth_methods(brief: Brief) -> list[str]:
    item = brief.access.get("auth_methods")
    value = item.value if item is not None and item.has_value() else None
    return [str(v) for v in value] if isinstance(value, list) else []


def seed_lines(brief: Brief) -> list[str]:
    """What the person or their document said about a screen, a flow or an entity: the outline must honour it."""
    out = []
    for f in brief.domain_facts:
        kind = f.kind or kind_for(f.type, f.topics)
        if kind in merge.SEED_KINDS:
            out.append(f"{kind} {f.id}: {' '.join(f.statement.split())[:240]}")
    return out[:SEED_LINES]


def pinned_wires(brief: Brief) -> list[PinnedWire]:
    rows: list[PinnedWire] = []
    for kind, records in (
        ("module", brief.modules),
        ("entity", brief.entities),
        ("flow", brief.flows),
    ):
        for r in records:
            if merge.is_pinned(r):
                rows.append(
                    PinnedWire(
                        kind=kind,
                        id=r.id,
                        name=r.name,
                        purpose=getattr(r, "purpose", "") or getattr(r, "goal", ""),
                    )
                )
    return rows


def _feature_state(brief: Brief) -> list[dict[str, Any]]:
    return [
        {
            "id": f.id,
            "name": f.name,
            "intent": f.intent,
            "priority": f.priority,
            "status": f.scope_status,
            "family": f.family,
            "apps": f.apps,
        }
        for f in brief.scope
    ]


def outline_digest(inp: StructureInput) -> str:
    """The structural inputs of the outline and nothing else: features, roles, apps, how people sign in, what the person
    said about screens, flows and entities, the band and the language. Not the whole ledger (a closing answer would
    always change it), not pins, status or an instruction (Accept or an undo must never trigger a redraft)."""
    b = inp.brief
    return _digest(
        {
            "features": _feature_state(b),
            "roles": [(r.id, r.name_in_user_words, r.goal) for r in b.roles],
            "apps": [(a.id, a.name, a.surface) for a in b.apps],
            "auth": auth_methods(b),
            "integrations": [i.name for i in b.integrations],
            "seeds": seed_lines(b),
            "band": inp.band.id,
            "language": inp.language,
        }
    )


def module_digest(inp: StructureInput, module_id: str) -> str:
    """One module's own inputs: its outline entry, the features it delivers, the roles and entities it names, the band."""
    b = inp.brief
    m = next(x for x in b.modules if x.id == module_id)
    feats = {f.id: f for f in b.scope}
    roles = {r.id: r for r in b.roles}
    return _digest(
        {
            "module": (m.name, m.purpose, m.apps, m.features, m.roles),
            "features": [
                (f.id, f.name, f.intent, f.priority, f.scope_status)
                for fid in m.features
                if (f := feats.get(fid))
            ],
            "roles": [
                (r.id, r.name_in_user_words, r.goal) for rid in m.roles if (r := roles.get(rid))
            ],
            "entities": [(e.id, e.name, e.purpose) for e in b.entities if e.module == module_id],
            "journeys": [
                (f.name, f.role)
                for f in b.flows
                if any(
                    s.module == module_id
                    for s in b.screens
                    if s.id in {st.screen for st in f.steps}
                )
            ],
            "band": inp.band.id,
            "language": inp.language,
        }
    )


def build_outline_input(inp: StructureInput) -> OutlineInput:
    b = inp.brief
    return OutlineInput(
        language=inp.language,
        band=band_wire(inp.band),
        project_card=inp.project_card,
        features=feature_wires(b),
        roles=role_wires(b),
        apps=app_wires(b),
        integrations=[i.name for i in b.integrations],
        auth_methods=auth_methods(b),
        org=inp.org,
        fact_digest=inp.fact_digest,
        seeds=seed_lines(b),
        pinned=pinned_wires(b),
        instruction=inp.instruction,
    )


def _fact_line(f: DomainFact) -> str:
    kind = f.kind or kind_for(f.type, f.topics)
    return f"{f.id} [{kind}] {' '.join(f.statement.split())[:240]}"


def build_module_input(
    inp: StructureInput,
    plan: merge.Plan,
    modules: Sequence[Any],
    *,
    issues: Sequence[str] = (),
    instruction: str = "",
) -> ModuleInput:
    b = inp.brief
    ids = {m.id for m in modules}
    routing = derive.route_facts(b)
    lines: list[str] = []
    for m in modules:
        for f in routing.by_module.get(m.id, []):
            line = _fact_line(f)
            if line not in lines:
                lines.append(line)
    patterns = load_structure_patterns()
    wanted_features = {fid for m in modules for fid in m.features}
    journeys = [
        JourneyCtx(
            ref=j.ref,
            name=j.name,
            role=j.role,
            goal=j.goal,
            legs=[LegCtx(index=i, module=mid, here=mid in ids) for i, mid in enumerate(j.legs)],
            owner=j.owner in ids,
        )
        for j in plan.journeys
        if any(mid in ids for mid in j.legs)
    ]
    return ModuleInput(
        language=inp.language,
        band=band_wire(inp.band),
        project_card=inp.project_card,
        modules=[
            ModuleCtx(
                ref=m.id,
                existing_id=m.id,
                kind=m.kind,
                name=m.name,
                purpose=m.purpose,
                apps=list(m.apps),
                features=list(m.features),
                roles=list(m.roles),
                screen_target=_target(inp, plan, m),
            )
            for m in modules
        ],
        other_modules=[m.name for m in b.modules if m.id not in ids],
        entities=[
            EntityCtx(
                ref=e.id,
                name=e.name,
                purpose=e.purpose,
                owner_module=e.module,
                mine=e.module in ids,
            )
            for e in b.entities
        ],
        roles=role_wires(b),
        apps=app_wires(b),
        features=[f for f in feature_wires(b) if f.id in wanted_features],
        journeys=journeys,
        existing=[
            ExistingScreen(
                id=s.id, module=s.module, name=s.name, purpose=s.purpose, pinned=merge.is_pinned(s)
            )
            for s in b.screens
            if s.module in ids
        ],
        auth_methods=auth_methods(b),
        facts="\n".join(lines[:MAX_FACT_LINES]),
        instruction=instruction,
        issues=list(issues),
        patterns=list(patterns.patterns),
        field_types=list(patterns.field_types),
    )


def _target(inp: StructureInput, plan: merge.Plan, module: Any) -> int:
    """How many screens a module should have: what the outline asked for, else what it has, never below the minimum."""
    have = sum(1 for s in inp.brief.screens if s.module == module.id)
    return max(inp.band.module_screens_min, plan.targets.get(module.id, have))


# ---------------------------------------------------------------------------------------------- the critic


def review_input(inp: StructureInput) -> ReviewInput:
    b = inp.brief
    screens_by_module: dict[str, list[str]] = {}
    for s in b.screens:
        screens_by_module.setdefault(s.module, []).append(
            f"{s.id} {s.name} [{s.pattern}; {' '.join(s.roles)}; serves {' '.join(s.serves) or '-'}]"
        )
    lines: list[str] = []
    for m in b.modules:
        lines.append(f"{m.id} {m.name} ({'; '.join(screens_by_module.get(m.id, []))})")
    for f in b.flows:
        lines.append(f"{f.id} {f.name} ({f.role}): {' > '.join(st.screen for st in f.steps)}")
    for e in b.entities:
        access = ", ".join(f"{r} {lv}" for r, lv in e.access.items())
        lines.append(f"{e.id} {e.name}: {access}")
    lines.append("; ".join(f"{r.id} {r.name_in_user_words}" for r in b.roles))
    facts = [
        _fact_line(f)
        for f in b.domain_facts
        if (f.kind or kind_for(f.type, f.topics))
        in derive.DETAIL_KINDS | {"compliance", "constraint"}
    ]
    perms = [
        f"{r.id} {r.name_in_user_words}: {', '.join(r.rough_permissions)}"
        for r in b.roles
        if r.rough_permissions
    ]
    return ReviewInput(
        language=inp.language,
        band=inp.band.id,
        outline="\n".join(lines),
        facts="\n".join(facts[:MAX_FACT_LINES]),
        permissions="\n".join(perms),
    )


def findings_to_issues(out: ReviewOutput, work: Brief, facts: Sequence[Fact]) -> list[checks.Issue]:
    """A finding is kept only when it cites a real structure id and, for a contradiction or a permission, a real fact:
    the critic can report, never decide. Everything else is dropped, one finding at a time."""
    module_of: dict[str, str] = {}
    for s in work.screens:
        module_of[s.id] = s.module
    for m in work.modules:
        module_of[m.id] = m.id
    for e in work.entities:
        module_of[e.id] = e.module
    for f in work.flows:
        module_of[f.id] = module_of.get(f.steps[0].screen, "") if f.steps else ""
    valid = set(module_of) | {r.id for r in work.roles} | {f.id for f in work.scope}
    fact_ids = {f.id for f in facts}
    issues: list[checks.Issue] = []
    for f in out.findings:
        ids = tuple(i for i in f.ids if i in valid)
        if f.code not in FINDING_CODES or not ids or not f.note.strip():
            continue
        if f.code in ("contradiction", "inconsistent_permission") and f.fact_id not in fact_ids:
            continue
        module = next((module_of[i] for i in ids if module_of.get(i)), "")
        suffix = f" (fact {f.fact_id})" if f.fact_id in fact_ids else ""
        issues.append(
            checks.make_issue(
                "critic_finding", ids, module=module, detail=f"{f.code}: {f.note.strip()}{suffix}"
            )
        )
    return issues


# ------------------------------------------------------------------------------------------- the pipeline


def _count(res: StructureResult, result: Any) -> None:
    res.calls += 1
    if result.used_fallback:
        res.fallbacks += 1
        res.reasons.append(result.reason or "a structure call used its floor")


def _target_module(work: Brief, issue: checks.Issue, routing: derive.FactRouting | None) -> str:
    """The module a problem that names none belongs to: the one that delivers the feature, owns the entity or has the
    role, else the module the fact was routed to, else the first module."""
    if issue.module:
        return issue.module
    for ident in issue.ids:
        for m in work.modules:
            if ident in m.features or ident in m.roles or ident == m.id:
                return m.id
        for e in work.entities:
            if ident == e.id and e.module:
                return e.module
        for s in work.screens:
            if ident == s.id and s.module:
                return s.module
        if routing is not None:
            for mid, facts in routing.by_module.items():
                if any(f.id == ident for f in facts):
                    return mid
    return work.modules[0].id if work.modules else ""


async def compute_structure(inp: StructureInput, ports: Ports, ctx: TurnContext) -> StructureResult:
    work = inp.brief
    res = StructureResult(work=work)
    before = structure_dump(work)
    stored = dict(work.meta.compile_digests)
    odigest = outline_digest(inp)
    existing = derive.has_structure(work)
    full = inp.only_modules is None
    seg_all: dict[tuple[str, int], list[tuple[str, str]]] = {}
    det_all: dict[str, tuple[list[str], list[str]]] = {}

    # 1. the outline
    redo_outline = full and (inp.force or not existing or stored.get(OUTLINE_KEY) != odigest)
    if redo_outline:
        out = await ports.structure_outliner.outline(ctx, build_outline_input(inp))
        _count(res, out)
        plan, _refmap = merge.merge_outline(work, out.output, inp.now)
    else:
        plan = merge.plan_from_existing(work)

    # 2. which modules need a draft
    targets: list[Any] = []
    for m in work.modules:
        if merge.is_pinned(m):
            continue
        if inp.only_modules is not None:
            if m.id in inp.only_modules:
                targets.append(m)
            continue
        has_screens = any(s.module == m.id for s in work.screens)
        if (
            inp.force
            or not has_screens
            or stored.get(MODULE_KEY + m.id) != module_digest(inp, m.id)
        ):
            targets.append(m)
    size = inp.band.group_size or max(1, len(targets))
    groups = [targets[i : i + size] for i in range(0, len(targets), size)]
    inputs = [build_module_input(inp, plan, g, instruction=inp.instruction) for g in groups]
    results = await asyncio.gather(*(ports.structure_drafter.draft(ctx, i) for i in inputs))
    for g, r in zip(groups, results, strict=True):
        _count(res, r)
        merged = merge.merge_modules(work, r.output, [m.id for m in g], patch=False, now=inp.now)
        seg_all.update(merged.segments)
        det_all.update(merged.details)
        res.drafted += [m.id for m in g]

    # 3. flows from the journey legs
    res.notes += merge.assemble_flows(
        work, plan, seg_all, det_all, inp.now, keep_unplanned=not full
    )

    # 4. fixes, checks, critic
    res.notes += checks.apply_fixes(work)
    issues = checks.run_checks(work, inp.band, facts=work.domain_facts, lang=inp.language)
    findings: list[checks.Issue] = []
    fresh = redo_outline or bool(
        res.drafted
    )  # only a run that drafted something asks a model for more
    if fresh and inp.band.critic and full and derive.has_structure(work):
        verdict = await ports.structure_reviewer.review(ctx, review_input(inp))
        _count(res, verdict)
        findings = findings_to_issues(verdict.output, work, inp.facts)

    # 5. ONE repair-and-expansion pass
    todo = (checks.needs_model(issues) + findings) if fresh else []
    if todo and work.modules:
        routing = derive.route_facts(work)
        by_module: dict[str, list[checks.Issue]] = {}
        for i in todo:
            by_module.setdefault(_target_module(work, i, routing), []).append(i)
        repair_modules = [m for m in work.modules if m.id in by_module and not merge.is_pinned(m)]
        repair_inputs = [
            build_module_input(inp, plan, [m], issues=[i.line() for i in by_module[m.id]])
            for m in repair_modules
        ]
        repaired = await asyncio.gather(
            *(ports.structure_drafter.draft(ctx, i) for i in repair_inputs)
        )
        seg_fix: dict[tuple[str, int], list[tuple[str, str]]] = {}
        det_fix: dict[str, tuple[list[str], list[str]]] = {}
        for m, r in zip(repair_modules, repaired, strict=True):
            _count(res, r)
            merged = merge.merge_modules(work, r.output, [m.id], patch=True, now=inp.now)
            seg_fix.update(merged.segments)
            det_fix.update(merged.details)
            if m.id not in res.drafted:
                res.drafted.append(m.id)
        if repair_modules:
            merge.assemble_flows(work, plan, seg_fix, det_fix, inp.now, keep_unplanned=True)
            res.notes += checks.apply_fixes(work)
            issues = checks.run_checks(work, inp.band, facts=work.domain_facts, lang=inp.language)
        else:
            issues = [*issues, *findings]

    # 6. what the person said is evidence; a record whose facts are gone is no longer supported; the digests
    by_id = {f.id: f for f in inp.facts}
    merge.seed_evidence(work, by_id, inp.now)
    checks.downgrade_unsupported(
        work,
        active={f.id for f in inp.facts if f.status == "active"},
        verified={f.id for f in inp.facts if f.supports_locked()},
    )
    res.issues = issues
    digests = {OUTLINE_KEY: odigest if full else stored.get(OUTLINE_KEY, odigest)}
    stale_inp = StructureInput(
        brief=work,
        facts=inp.facts,
        language=inp.language,
        band=inp.band,
        project_card=inp.project_card,
        org=inp.org,
        fact_digest=inp.fact_digest,
        now=inp.now,
    )
    for m in work.modules:
        digests[MODULE_KEY + m.id] = module_digest(stale_inp, m.id)
    res.digests = digests
    res.changed = structure_dump(work) != before or any(
        stored.get(k) != v for k, v in digests.items()
    )
    return res
