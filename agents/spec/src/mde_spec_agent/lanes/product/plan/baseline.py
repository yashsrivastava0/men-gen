"""The draft plan in the output schema (from the baseline graph and the release plan) and the deterministic completion."""

from __future__ import annotations

from collections.abc import Sequence

from mde_spec_agent.derive.release import ReleasePlan
from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import clip
from mde_spec_core.graph import Prov, SpecGraph

from .schema import (
    ModuleRec,
    OwnerRec,
    PersonaRec,
    PlanIn,
    PlanOut,
    ReleaseRec,
    RoleRec,
)


def _prov(prov: Sequence[Prov], fallback: str) -> list[str]:
    return [f"{p.kind}:{p.ref}" for p in prov if p.ref] or [fallback]


def baseline_plan_out(graph: SpecGraph, release: ReleasePlan | None) -> PlanOut:
    out = PlanOut()
    for m in sorted(graph.modules.values(), key=lambda x: x.id):
        out.modules.append(
            ModuleRec(
                id=m.id,
                purpose=m.purpose,
                shared=m.shared,
                depends_on=list(m.depends_on),
                prov=_prov(m.provenance, f"compass:{m.id}"),
            )
        )
    for r in sorted(graph.roles.values(), key=lambda x: x.id):
        out.roles.append(
            RoleRec(
                id=r.id,
                goal=r.goal,
                home_screen=r.home_screen or "",
                prov=_prov(r.provenance, f"compass:{r.id}"),
            )
        )
    for e in sorted(graph.entities.values(), key=lambda x: x.id):
        if e.owner_module:
            out.owners.append(
                OwnerRec(
                    entity=e.id,
                    module=e.owner_module,
                    reason="",
                    prov=_prov(e.provenance, f"compass:{e.id}"),
                )
            )
    for p in sorted(graph.personas.values(), key=lambda x: x.id):
        out.personas.append(
            PersonaRec(
                role=p.role_id,
                goal=p.goal,
                context=p.context,
                frequency=p.frequency,
                skill=p.skill,
                pain_points=list(p.pain_points),
                success=p.success,
                prov=_prov(p.provenance, f"assumed:{p.role_id}"),
            )
        )
    if release is not None:
        for fid, d in sorted(release.features.items()):
            if fid not in graph.features:
                continue
            out.release.append(
                ReleaseRec(
                    feature=fid,
                    status="in" if d.status == "in" else "later",
                    reason=d.reason if d.status != "in" and d.reason else "none",  # type: ignore[arg-type]
                    why=clip(d.why, 200),
                    consent_needed=any(fid in c.feature_ids for c in release.needs_consent),
                    prov=_prov(graph.features[fid].provenance, f"compass:{fid}"),
                )
            )
    return out


def complete(inp: PlanIn) -> PlanOut:
    """The draft, completed: every role has a persona; a persona's context and success are assumed from the role's goal
    (frequency, skill and pain points stay empty: nothing states them)."""
    out = inp.baseline.model_copy(deep=True)
    have = {p.role for p in out.personas}
    h = vocab.hints()
    for r in out.roles:
        if r.id not in have:
            out.personas.append(
                PersonaRec(role=r.id, goal=r.goal, prov=[f"assumed:no persona exists for {r.id}"])
            )
    for p in out.personas:
        goal = clip(p.goal.rstrip(".").lower(), 120)
        if goal:
            p.context = p.context or h["persona_context"].format(goal=goal)
            p.success = p.success or h["persona_success"].format(goal=goal)
    return out
