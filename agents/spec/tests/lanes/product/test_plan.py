"""The plan call: merges and splits applied with aliases, modules, roles, owners, access, personas, the release proposal and
its invariants (never relabel a user deferral, never defer a user-evidenced must without a consent request)."""

from __future__ import annotations

import copy

import pytest

from mde_spec_agent.lanes.product.kit.gate import Minter, apply_ext, registry_from_graph
from mde_spec_agent.lanes.product.plan.mock import handle
from mde_spec_agent.lanes.product.plan.patch import build_plan_patch
from mde_spec_agent.lanes.product.plan.schema import MergeRec, PlanOut, ReleaseRec, SplitRec
from mde_spec_agent.lanes.product.plan.validate import validate
from mde_spec_agent.lanes.product.steps.inputs import plan_input
from mde_spec_agent.lanes.product.steps.plan import ensure_derived, env_of, gate_of
from mde_spec_core.validators.base import run_all
from mde_spec_core.validators.context import make_context

CHECKS = {"V-01", "V-02", "V-03", "V-04", "V-05", "V-06", "V-19", "V-29", "V-30", "V-31", "V-40"}


def setup(env, key):
    ctx = env.ctx(key)
    d = ensure_derived(ctx)
    e = env_of(ctx)
    return ctx, e, plan_input(ctx, e, d.plan), d


def source_copy_missing(g):
    """V-14 for the source language only: other languages are written by later steps (degraded locales)."""
    lang = g.meta.source_language
    return [
        f
        for f in run_all(make_context(g.model_copy(deep=True)), gate=3, only={"V-14"})
        if f"locale {lang}" in f.why
    ]


def findings(g):
    return {
        (f.code, f.where, f.why)
        for f in run_all(make_context(g.model_copy(deep=True)), gate=3, only=CHECKS)
        if f.severity == "blocker"
    }


def pair(g):
    """Two non-system screens of one app and module (the kind of pair a create form and an edit form are)."""
    by: dict = {}
    for s in sorted(g.screens.values(), key=lambda x: x.id):
        if not s.system and s.module_id:
            by.setdefault((s.app_id, s.module_id), []).append(s.id)
    for ids in by.values():
        if len(ids) >= 2:
            return ids[0], ids[1]
    raise AssertionError("no pair")


def test_the_mock_plan_reproduces_the_code_decisions(env, key):
    _, e, inp, d = setup(env, key)
    out = handle(inp)
    assert validate(inp, out) == []
    assert out.merges == [] and out.splits == [] and out.access == []
    assert {r.feature: (r.status, r.reason) for r in out.release} == {
        f: (("in", "none") if x.status == "in" else ("later", x.reason))
        for f, x in d.plan.features.items()
        if f in e.graph.features
    }
    assert {p.role for p in out.personas} == set(e.graph.roles)  # one persona per role
    assert all(
        p.prov and p.prov[0].startswith("assumed:") for p in out.personas
    )  # assumed unless a fact states it
    assert all(
        p.frequency == "" and p.skill == "" for p in out.personas
    )  # nothing states them: they stay empty
    assert sorted(o.entity for o in out.owners) == sorted(e.graph.entities)


def test_personas_are_completed_in_the_graph_and_marked_assumed(env, key):
    ctx = env.ctx(key)
    env.run(ctx, "plan")
    g = ctx.graph
    assert {p.role_id for p in g.personas.values()} == set(g.roles)
    for p in g.personas.values():
        assert p.assumed and p.context and p.success and p.tier != "locked"


def test_a_merge_folds_the_screen_keeps_an_alias_and_leaves_no_dangling_reference(env):
    _ctx, e, inp, _d = setup(env, "standard")
    keep, drop = pair(e.graph)
    out = handle(inp)
    out.merges = [
        MergeRec(
            keep=keep, drop=drop, reason="a trivial pair", prov=["assumed:the pair is trivial"]
        )
    ]
    assert validate(inp, out) == []
    before = findings(e.graph)
    patch = build_plan_patch(e, inp, out, Minter.for_batch(e.graph, 0), False)
    assert patch["remove"] == {"screens": [drop]}
    g = e.graph.model_copy(deep=True)
    apply_ext(g, patch)
    assert drop not in g.screens and drop in g.screens[keep].aliases
    refs = [t for s in g.screens.values() for t in [*s.leads_to, *s.reached_from]]
    assert drop not in refs and all(drop not in st.screen_ids for st in g.stories.values())
    assert all(drop not in stg.screen_ids for f in g.flows.values() for stg in f.stages)
    assert all(drop not in m.screen_ids for m in g.modules.values())
    assert all(r.home_screen != drop for r in g.roles.values())
    assert findings(g) <= before  # a merge makes nothing worse
    assert source_copy_missing(g) == source_copy_missing(
        e.graph
    )  # and every copy key of the kept screen still has text
    reg = registry_from_graph(g)
    reg.alias(drop, keep)
    assert reg.resolve(drop) == keep
    # keys stay unique (V-31) and the kept screen has everything of both
    sa, sb = e.graph.screens[keep], e.graph.screens[drop]
    assert len({r.key for r in g.screens[keep].regions}) == len(g.screens[keep].regions)
    assert len(g.screens[keep].regions) == len(sa.regions) + len(sb.regions)
    assert len(g.screens[keep].actions) == len(sa.actions) + len(sb.actions)


def test_a_merge_applied_by_the_step_records_aliases_and_the_run_goes_on(env):
    ctx = env.ctx("standard")
    ensure_derived(ctx)
    keep, drop = pair(ctx.graph)

    def add_merge(out):
        out = out.model_copy(deep=True)
        out.merges = [
            MergeRec(keep=keep, drop=drop, reason="a trivial pair", prov=["assumed:trivial"])
        ]
        return out

    env.gateway(ctx).rewrite = lambda spec, inp, n: add_merge if spec == "spec.plan" else None
    assert env.run(ctx).status == "awaiting_g2"
    assert drop not in ctx.graph.screens and ctx.plan["aliases"] == {drop: keep}
    assert ctx.plan["registry"].resolve(drop) == keep
    assert ctx.plan["merges"][0]["keep"] == keep
    g = ctx.graph
    assert [f for f in findings(g) if f[0] in ("V-03", "V-05", "V-31")] == []


def test_a_split_moves_regions_and_actions_to_a_new_screen_with_links_both_ways(env):
    _ctx, e, inp, _d = setup(env, "standard")
    sid = max(
        (s for s in e.graph.screens.values() if not s.system and len(s.regions) >= 2 and s.actions),
        key=lambda s: (len(s.regions), len(s.actions), s.id),
    ).id
    s = e.graph.screens[sid]
    regions = sorted(r.key for r in s.regions)
    actions = sorted(a.key for a in s.actions)
    inp = inp.model_copy(
        update={
            "over_cap_screen_ids": [sid],
            "screen_keys": [f"{sid}|regions={','.join(regions)}|actions={','.join(actions)}"],
        }
    )
    out = handle(inp)
    out.splits = [
        SplitRec(
            screen=sid,
            part=1,
            name=s.name,
            purpose=s.purpose,
            prov=["assumed:the slice is over the cap"],
        ),
        SplitRec(
            screen=sid,
            part=2,
            name=f"{s.name} details",
            purpose="the second half of what this screen shows",
            region_keys=regions[-1:],
            action_keys=actions[-1:],
            reason="over the cap",
            prov=["assumed:the slice is over the cap"],
        ),
    ]
    assert validate(inp, out) == []
    before = findings(e.graph)
    minter = Minter.for_batch(e.graph, 0)
    patch = build_plan_patch(e, inp, out, minter, False)
    g = e.graph.model_copy(deep=True)
    apply_ext(g, patch)
    new = [i for i in g.screens if i not in e.graph.screens]
    assert len(new) == 1
    n = g.screens[new[0]]
    assert [r.key for r in n.regions] == regions[-1:] and [a.key for a in n.actions] == actions[-1:]
    assert regions[-1] not in {r.key for r in g.screens[sid].regions}
    assert new[0] in g.screens[sid].leads_to and sid in n.leads_to
    assert new[0] in g.modules[s.module_id].screen_ids
    assert all(new[0] in st.screen_ids for st in g.stories.values() if sid in st.screen_ids)
    assert findings(g) <= before
    assert patch["locale_copy"]  # its title and action words exist in the source language
    assert source_copy_missing(g) == source_copy_missing(e.graph)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda out, inp: out.release.__setitem__(
                0,
                ReleaseRec(
                    feature=out.release[0].feature,
                    status="later",
                    reason="capacity",
                    why="x",
                    consent_needed=False,
                    prov=["assumed:x"],
                ),
            ),
            "capacity deferral needs consent",
        ),
        (lambda out, inp: out.owners.append(out.owners[0]), "exactly ONE owner"),
        (lambda out, inp: out.personas.pop(), "no persona"),
        (lambda out, inp: setattr(out.roles[0], "id", "ROL-999"), "never invent a role"),
        (
            lambda out, inp: setattr(out.personas[0], "success", "answered within 37 minutes"),
            "never invent a number",
        ),
        (lambda out, inp: setattr(out.personas[0], "prov", []), "provenance is empty"),
        (
            lambda out, inp: setattr(out.personas[0], "prov", ["research:RI-404"]),
            "research item that is not in this call",
        ),
        (lambda out, inp: setattr(out.modules[0], "depends_on", [out.modules[0].id]), "depends_on"),
        (lambda out, inp: out.modules.pop(), "no record"),
        (
            lambda out, inp: setattr(out.personas[0], "context", "a bright red button on the left"),
            "form word",
        ),
    ],
)
def test_the_plan_validator_says_no(env, mutate, message):
    _ctx, _e, inp, _d = setup(env, "standard")
    out = handle(inp)
    mutate(out, inp)
    errors = validate(inp, out)
    assert any(message in m for m in errors), errors


def test_a_user_deferral_is_never_relabelled_and_a_user_must_needs_consent(env):
    _ctx, _e, inp, _d = setup(env, "standard")
    assert inp.user_deferred  # the produce export: live tracking was deferred by the person
    out = handle(inp)
    deferred = inp.user_deferred[0]
    row = next(r for r in out.release if r.feature == deferred)
    row.reason = "capacity"
    row.consent_needed = True
    assert any("never relabel a user deferral" in m for m in validate(inp, out))
    row.status, row.reason = "in", "none"
    assert any("the user deferred it" in m for m in validate(inp, out))
    out = handle(inp)
    must = inp.user_must[0]
    row = next(r for r in out.release if r.feature == must)
    row.status, row.reason, row.consent_needed = "later", "first_release", False
    assert any("deferring it needs consent_needed true" in m for m in validate(inp, out))
    row.consent_needed = True
    assert validate(inp, out) == []


def test_a_release_proposal_that_differs_is_a_proposal_for_g15_not_a_change_of_the_graph(env):
    ctx = env.ctx("standard")
    ensure_derived(ctx)
    target = next(
        f
        for f in sorted(ctx.graph.features.values(), key=lambda x: x.id)
        if f.status == "in" and not (f.user_evidenced and f.user_priority == "must")
    )

    def propose(out):
        out = out.model_copy(deep=True)
        row = next(r for r in out.release if r.feature == target.id)
        row.status, row.reason, row.consent_needed, row.why = (
            "later",
            "capacity",
            True,
            "the first release would be too big",
        )
        return out

    env.gateway(ctx).rewrite = lambda spec, inp, n: propose if spec == "spec.plan" else None
    assert env.run(ctx, "plan").status == "awaiting_g2"
    assert (
        ctx.graph.features[target.id].status == "in"
    )  # the graph stays on the code's release plan
    changed = [r for r in ctx.plan["proposal"] if r["differs_from_code"]]
    assert [r["feature"] for r in changed] == [target.id]
    assert [c["feature"] for c in ctx.plan["consents"]] == [
        target.id
    ]  # the consent request G1.5 must show


def test_the_fused_answer_may_not_restructure(env):
    _ctx, e, inp, _d = setup(env, "quick")
    inp = inp.model_copy(update={"allow_restructure": False})
    keep, drop = pair(e.graph)
    out = handle(inp)
    out.merges = [MergeRec(keep=keep, drop=drop, reason="x", prov=["assumed:x"])]
    assert any("must be empty" in m for m in validate(inp, out))


def test_gate_1_accepts_the_mock_plan(env, key):
    ctx, e, inp, _d = setup(env, key)
    out = handle(inp)
    patch = build_plan_patch(e, inp, copy.deepcopy(out), Minter.for_batch(e.graph, 0), False)
    assert gate_of(ctx, e).errors(patch) == []
    assert PlanOut.model_validate(out.model_dump()) == out


def test_code_made_provenance_is_kept_when_echoed_and_refused_when_the_model_writes_it(env):
    """A module the derivation minted (no Compass modules) carries `code:MOD-S001`: the plan echoes it and stays valid; a
    model that writes `code:` itself is refused (it may not claim to be code)."""
    _, e, inp, _ = setup(env, "delhi")
    minted = [m for m in e.graph.modules.values() if any(p.kind == "code" for p in m.provenance)]
    assert minted  # the older export has no modules of its own
    out = handle(inp)
    assert validate(inp, out) == []
    row = next(m for m in out.modules if m.id == minted[0].id)
    row.prov = [*row.prov, "code:MOD-S999"]
    assert any("must be kind:ref" in m for m in validate(inp, out))
    patch = build_plan_patch(e, inp, handle(inp), Minter.for_batch(e.graph, 0), False)
    assert any(p["kind"] == "code" for p in patch["tables"]["modules"][minted[0].id]["provenance"])
