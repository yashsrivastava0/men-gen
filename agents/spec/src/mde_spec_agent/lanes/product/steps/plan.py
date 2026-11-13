"""The `plan` step (doc 18 1.2): derive the baseline graph in code when the run has none, then ONE call (SPEC-PLAN, route
`deep`) that decides merges and splits, modules, roles, owners, personas and a release proposal; for a compact app the same
call also writes the product when the fusion rule says the fused schema fits the writer profile. The plan is the one step
with no safe default: if its call is still invalid after the re-ask the step raises `PlanInvalid` (the run ends partial).

`ctx.plan` after the step: `derived` (the whole derivation), `release` (ReleasePlan), `inventory`, `decision`, `seed`,
`registry` (IdRegistry with the aliases of merged screens), `proposal` (the model's release proposal, one row per feature,
`differs_from_code` marks a change), `consents` (consent requests that proposal needs), `aliases`, `merges`, `splits` and
`fused` (True when the plan call also wrote the product). The graph itself stays on the code's release plan."""

from __future__ import annotations

import functools
from typing import Any

from mde_spec_agent.calls.base import RunResult
from mde_spec_agent.derive import Derived, derive_graph
from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.batch import (
    Attempt,
    apply_patches,
    run_batch,
    versions_of,
)
from mde_spec_agent.lanes.product.kit.env import Env
from mde_spec_agent.lanes.product.kit.gate import Gate1, Minter, registry_from_graph
from mde_spec_agent.lanes.product.kit.research import as_view
from mde_spec_agent.lanes.product.kit.slices import build_maps, chunk_lines
from mde_spec_agent.lanes.product.plan import spec as plan_spec
from mde_spec_agent.lanes.product.plan.patch import build_plan_patch
from mde_spec_agent.lanes.product.plan.schema import PlanOut
from mde_spec_agent.lanes.product.plan_fused import spec as fused_spec
from mde_spec_agent.lanes.product.plan_fused.patch import build_fused_patch
from mde_spec_agent.lanes.product.plan_fused.schema import FusedIn, FusedOut
from mde_spec_agent.lanes.product.prod_integ.schema import IntegOut
from mde_spec_agent.lanes.product.prod_journeys.schema import JourneysOut
from mde_spec_agent.lanes.product.prod_module.schema import ModuleOut
from mde_spec_agent.run.checkpoint import key_for
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.orchestrator import PlanInvalid
from mde_spec_agent.run.patch import Patch
from mde_spec_core.callkit.fusion import FusionJob, can_fuse
from mde_spec_core.canon import canonical_json

from .inputs import (
    brand_names_of,
    counter_for,
    integ_input_factory,
    journeys_input_factory,
    merged_module_input,
    plan_input,
    quotes_of,
)

PLAN_BATCH = "plan:00"
FUSED_BATCH = "plan:fused"
STEP = "plan"


def env_of(ctx: RunContext) -> Env:
    graph = ctx.graph.model_copy(deep=True)
    return Env(graph, ctx.spec_input, build_maps(graph, ctx.spec_input))


def gate_of(ctx: RunContext, env: Env) -> Gate1:
    return Gate1(
        env.graph,
        research=ctx.research,
        quotes=quotes_of(env),
        brand_names=brand_names_of(ctx, env),
    )


def ensure_derived(ctx: RunContext) -> Derived | None:
    """The code derivation: run it when the run has no graph; rebuild its plan data when a resumed process lost it."""
    d = ctx.plan.get("derived")
    if d is not None:
        return d  # type: ignore[no-any-return]
    si = ctx.spec_input
    if si is None:
        return None
    d = derive_graph(
        si,
        as_view(ctx.research),
        ctx.config,
        everything_in_one_release=bool(ctx.plan.get("everything_in_one_release", False)),
        consented=tuple(ctx.plan.get("consented", ())),
    )
    if not ctx.graph.screens and not ctx.graph.features:
        ctx.graph = d.graph
        ctx.rcode.changes.extend(d.seeded.rcode_log.changes)
    if not ctx.facts:
        ctx.facts = set(d.facts)
    ctx.plan.update(
        derived=d, release=d.plan, inventory=d.inventory, decision=d.decision, seed=d.seeded
    )
    return d


def fused_versions(ctx: RunContext):
    return versions_of(ctx, fused_spec.SPEC, fused_spec.PROMPT_VERSION, fused_spec.SCHEMA_VERSION)


def fused_succeeded(ctx: RunContext) -> bool:
    """Did the plan call also write the product? Read from the checkpoint store, so it survives a resumed process."""
    key = key_for(ctx.program_id, ctx.input_hash, STEP, FUSED_BATCH, fused_versions(ctx))
    hit = ctx.store.get_checkpoint(ctx.run_id, key)
    return bool(hit and hit["patch"].get("tables") and not hit["patch"].get("fused_failed"))


# ------------------------------------------------------------------------------------------------ the fused attempt
def _est_out(counter: Any, out: Any) -> int:
    growth = float(vocab.hints()["fused_output_growth"])
    return int(counter.count(canonical_json(out.model_dump(mode="json"))) * growth) + 1


def _est_in(counter: Any, inp: Any) -> int:
    return (
        counter.count(canonical_json(inp.model_dump(mode="json", exclude={"baseline", "reask"})))
        + 1
    )


def fused_input(ctx: RunContext, env: Env, release: Any) -> FusedIn:
    plan = plan_input(ctx, env, release, restructure=False)
    module = merged_module_input(ctx, env)
    integ_ids = sorted(env.graph.integrations)
    integ = integ_input_factory(ctx, env)(integ_ids)
    flow_ids = sorted(f.id for f in env.graph.flows.values() if f.variant == "main")
    journeys = journeys_input_factory(ctx, env)(flow_ids)
    parts = (plan, module, integ, journeys)
    union = {
        "research_ids": sorted({x for p in parts for x in p.research_ids}),
        "research_labels": sorted({x for p in parts for x in p.research_labels}),
    }
    for p in parts:
        p.research_ids = union["research_ids"]
        p.research_labels = union["research_labels"]

    def lines(*chunks: list[str]) -> list[str]:
        seen: list[str] = []
        for c in chunks:
            for ln in "\n".join(c).split("\n"):
                if ln.strip() and ln not in seen:
                    seen.append(ln)
        return chunk_lines(seen)

    notes = [n for n in (plan.research_note, module.research_note) if n]
    return FusedIn(
        ref_ids=plan.ref_ids,
        research_ids=union["research_ids"],
        research_labels=union["research_labels"],
        policy_refs=plan.policy_refs,
        language=plan.language,
        profile=plan.profile,
        plan=plan,
        module=module,
        integ=integ,
        journeys=journeys,
        brief=plan.brief,
        inventory=plan.inventory,
        release_lines=plan.release_lines,
        module_slice=module.slice,
        facts=lines(module.facts, integ.facts, journeys.facts),
        integ_slice=integ.slice,
        journey_slice=journeys.slice,
        research=lines(plan.research, module.research, integ.research, journeys.research),
        research_note=notes[0] if notes else "",
        over_cap_screen_ids=plan.over_cap_screen_ids,
        scope_cap=plan.scope_cap,
        slice_cap_tokens=plan.slice_cap_tokens,
        user_deferred=plan.user_deferred,
        user_must=plan.user_must,
        allow_restructure=False,
        integration_ids=integ.integration_ids,
        provider_names=integ.provider_names,
        flow_ids=journeys.flow_ids,
    )


def fusion_fits(ctx: RunContext, inp: FusedIn) -> bool:
    counter = counter_for(ctx)
    jobs = [
        FusionJob(
            "plan", _est_out(counter, inp.plan.baseline), _est_in(counter, inp.plan), PlanOut
        ),
        FusionJob(
            "module",
            _est_out(counter, inp.module.baseline),
            _est_in(counter, inp.module),
            ModuleOut,
        ),
        FusionJob(
            "integ", _est_out(counter, inp.integ.baseline), _est_in(counter, inp.integ), IntegOut
        ),
        FusionJob(
            "journeys",
            _est_out(counter, inp.journeys.baseline),
            _est_in(counter, inp.journeys),
            JourneysOut,
        ),
    ]
    return can_fuse(jobs, ctx.writer, ctx.config).ok


async def run_fused(ctx: RunContext, env: Env, release: Any) -> bool:
    inp = fused_input(ctx, env, release)
    if not fusion_fits(ctx, inp):
        return False
    attempt: Attempt[Any] = Attempt(
        ctx=ctx,
        cs=fused_spec.SPEC,
        step=STEP,
        gate=gate_of(ctx, env),
        make_input=lambda items: inp,
        build=functools.partial(_build_fused, env),
        node_ids=lambda items: ["SPEC-PLAN-FUSED"],
        minter=Minter.for_batch(env.graph, 0),
        on_flagged=lambda: {"tables": {}, "replace": True, "fused_failed": True},
    )
    patch = await run_batch(ctx, STEP, FUSED_BATCH, fused_versions(ctx), attempt, [("F", "fused")])
    if patch.get("fused_failed"):
        return False
    _apply(ctx, FUSED_BATCH, patch, fused=True)
    return True


def _build_fused(env: Env, inp: FusedIn, out: FusedOut, minter: Minter, flagged: bool) -> Patch:
    return build_fused_patch(env, inp, out, minter, flagged)


# ------------------------------------------------------------------------------------------------ the plan call
def _strict(res: RunResult[Any]) -> None:
    raise PlanInvalid(
        f"the plan call is invalid after its re-ask ({res.reason or 'no usable output'}); "
        "it has no safe default, so the run ends partial and can be resumed"
    )


async def run_plan(ctx: RunContext, env: Env, release: Any) -> None:
    inp = plan_input(ctx, env, release)
    attempt: Attempt[Any] = Attempt(
        ctx=ctx,
        cs=plan_spec.SPEC,
        step=STEP,
        gate=gate_of(ctx, env),
        make_input=lambda items: inp,
        build=functools.partial(_build_plan, env),
        node_ids=lambda items: ["SPEC-PLAN"],
        minter=Minter.for_batch(env.graph, 0),
        strict=_strict,
    )
    versions = versions_of(ctx, plan_spec.SPEC, plan_spec.PROMPT_VERSION, plan_spec.SCHEMA_VERSION)
    patch = await run_batch(ctx, STEP, PLAN_BATCH, versions, attempt, [("P", "plan")])
    _apply(ctx, PLAN_BATCH, patch, fused=False)


def _build_plan(env: Env, inp: Any, out: Any, minter: Minter, flagged: bool) -> Patch:
    return build_plan_patch(env, inp, out, minter, flagged)


def _apply(ctx: RunContext, batch_id: str, patch: Patch, *, fused: bool) -> None:
    apply_patches(ctx, [(batch_id, patch)])
    facts = patch.get("plan", {})
    reg = registry_from_graph(ctx.graph)
    for old, new in sorted(facts.get("aliases", {}).items()):
        reg.alias(old, new)
    ctx.plan.update(
        registry=reg,
        proposal=facts.get("proposal", []),
        consents=facts.get("consents", []),
        aliases=facts.get("aliases", {}),
        merges=facts.get("merges", []),
        splits=facts.get("splits", {}),
        fused=fused,
    )
    ctx.emit(
        "plan.ready",
        step=STEP,
        merges=len(facts.get("merges", [])),
        splits=len(facts.get("splits", {})),
        proposal_changes=sum(1 for r in facts.get("proposal", []) if r.get("differs_from_code")),
        consent_requests=len(facts.get("consents", [])),
        fused=fused,
    )
    if patch.get("reasked"):
        ctx.emit("repair.reask", step=STEP, call="SPEC-PLAN")


async def plan(ctx: RunContext) -> None:
    derived = ensure_derived(ctx)
    release = derived.plan if derived is not None else None
    if ctx.runner is None:  # a code-only run (the zero-cost preview): the baseline is the plan
        ctx.plan["fused"] = False
        ctx.plan.setdefault("registry", registry_from_graph(ctx.graph))
        return
    env = env_of(ctx)
    if env.graph.meta.profile == "compact" and await run_fused(ctx, env, release):
        return
    await run_plan(ctx, env, release)
