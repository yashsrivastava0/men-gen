"""Failure paths of the `design` step: too-similar directions, the ONE re-ask per node, the fallback, Gate 1."""

from __future__ import annotations

import dataclasses

import pytest

from mde_spec_agent.lanes.design import design, step_design
from mde_spec_agent.lanes.design.dsgn import mock as mock_mod
from mde_spec_agent.lanes.design.dsgn._compose import compose
from mde_spec_agent.spend import SpendRefused
from mde_spec_core import tokens
from mde_spec_core.graph import SpecGraph
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context

IDS = ["DIR-S001", "DIR-S002", "DIR-S003"]


def too_similar(inp):
    """Every candidate a copy of the first one: same axes, same hue."""
    out = compose(inp)
    d0 = out.directions[0]
    return out.model_copy(
        update={
            "directions": [
                d0.model_copy(update={"name": f"{d0.name} {i}"}) for i in range(inp.count)
            ]
        }
    )


def picks(g: SpecGraph):
    return [
        {
            "mood_family": d.mood, "shape": d.shape, "density": d.density, "depth": d.depth,
            "motion_personality": d.motion_personality, "imagery": d.imagery,
            "type_personality": d.type_personality, "seed_hue": d.seed_hue,
        }
        for _, d in sorted(g.directions.items())
    ]  # fmt: skip


async def test_too_similar_directions_are_one_reask_then_the_fallback_restores_the_rules(
    env, monkeypatch
):
    monkeypatch.setattr(mock_mod, "handle", too_similar)
    ctx = env.ctx("standard")
    await env.run_stage(ctx, "design", design)
    gw = env.gateway(ctx)
    assert gw.count("spec.dsgn") == 2  # the call and its ONE re-ask
    g = ctx.graph
    assert len(g.directions) == 3
    assert (
        tokens.check_direction_set(picks(g), brand_locked=False) == []
    )  # pick_directions restored them
    assert all(d.degraded and d.assumed for d in g.directions.values())
    (flag,) = [f for f in ctx.flags if f["kind"] == "degraded_node"]
    assert flag["call"] == "spec.dsgn" and flag["nodes"] == IDS
    assert "differ on only" in flag["reason"]
    assert [r.used_fallback and r.flagged for _, r in ctx.runner.calls] == [True]
    # the fallback's nodes are still complete and valid
    assert run_all(make_context(g), gate=3, only={"V-12", "V-43"}) == []
    assert all(len(tk["names"]) > 100 for tk in g.tokens.values())


async def test_the_runner_ledger_allows_one_reask_per_node_in_the_whole_run(env, monkeypatch):
    monkeypatch.setattr(mock_mod, "handle", too_similar)
    first = env.ctx("standard")
    await design(first)
    assert env.gateway(first).count("spec.dsgn") == 2
    assert all(first.runner.ledger.has_used(n) for n in IDS)
    # a second pass over the same nodes (same runner, same ledger): the first call is asked, no second re-ask
    _, fresh = env.baseline("standard")
    second = dataclasses.replace(first, graph=fresh, flags=[], plan={}, input_hash="other-input")
    await design(second)
    assert env.gateway(first).count("spec.dsgn") == 3
    assert second.graph.directions and all(d.degraded for d in second.graph.directions.values())


async def test_a_model_that_fixes_itself_on_the_reask_is_accepted_unflagged(env, monkeypatch):
    state = {"n": 0}

    def once_bad(inp):
        state["n"] += 1
        return too_similar(inp) if state["n"] == 1 else compose(inp)

    monkeypatch.setattr(mock_mod, "handle", once_bad)
    ctx = env.ctx("standard")
    await design(ctx)
    assert env.gateway(ctx).count("spec.dsgn") == 2
    assert not any(d.degraded for d in ctx.graph.directions.values())
    assert not [f for f in ctx.flags if f["kind"] == "degraded_node"]
    assert ctx.runner.calls[-1][1].reasked


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(
            lambda out: out.model_copy(update={"directions": out.directions[:2]}), id="wrong_count"
        ),
        pytest.param(
            lambda out: out.model_copy(
                update={
                    "directions": [
                        d.model_copy(update={"seed_colour": "#ffffff"}) for d in out.directions
                    ]
                }
            ),
            id="no_accessible_tokens_from_white",
        ),
        pytest.param(
            lambda out: out.model_copy(
                update={
                    "directions": [
                        out.directions[0].model_copy(
                            update={"voice_line": "Put the green button at the top."}
                        ),
                        *out.directions[1:],
                    ]
                }
            ),
            id="form_words",
        ),
        pytest.param(
            lambda out: out.model_copy(
                update={
                    "assets": [
                        out.assets[0].model_copy(
                            update={"subject": "A photo, soft light, cropped, https://x.test/a.png"}
                        )
                    ]
                }
            ),
            id="asset_lighting_crop_url",
        ),
        pytest.param(
            lambda out: out.model_copy(
                update={"assets": [out.assets[0].model_copy(update={"app": "APP-999"})]}
            ),
            id="unknown_app",
        ),
    ],
)
async def test_each_validator_error_costs_exactly_one_reask_then_the_fallback(
    env, monkeypatch, bad
):
    real = mock_mod.handle
    monkeypatch.setattr(mock_mod, "handle", lambda inp: bad(real(inp)))
    ctx = env.ctx("standard")
    await design(ctx)
    assert env.gateway(ctx).count("spec.dsgn") == 2
    assert len(ctx.graph.directions) == 3 and all(d.degraded for d in ctx.graph.directions.values())
    assert [f for f in ctx.flags if f["kind"] == "degraded_node"]


async def test_gate1_findings_about_written_nodes_get_one_targeted_reask(env, monkeypatch):
    real = step_design.gate1_messages
    state = {"n": 0}

    def flaky(graph, patch, config, **kw):
        state["n"] += 1
        return (
            ["V-12 DIR-S001: a scripted gate finding"]
            if state["n"] == 1
            else real(graph, patch, config, **kw)
        )

    monkeypatch.setattr(step_design, "gate1_messages", flaky)
    ctx = env.ctx("standard")
    await design(ctx)
    assert env.gateway(ctx).count("spec.dsgn") == 2
    assert not any(d.degraded for d in ctx.graph.directions.values())


async def test_gate1_findings_that_persist_end_in_the_fallback_after_one_reask(env, monkeypatch):
    monkeypatch.setattr(
        step_design,
        "gate1_messages",
        lambda graph, patch, config, **kw: (
            ["V-12 DIR-S001: always failing"]
            if not patch.get("flags") or all(f["kind"] != "degraded_node" for f in patch["flags"])
            else []
        ),
    )
    ctx = env.ctx("standard")
    await design(ctx)
    assert env.gateway(ctx).count("spec.dsgn") == 2
    assert all(d.degraded for d in ctx.graph.directions.values())
    assert "gate 1" in next(f for f in ctx.flags if f["kind"] == "degraded_node")["reason"]


async def test_a_provider_failure_is_one_reask_then_the_fallback_and_the_lane_never_blocks(env):
    ctx = env.ctx("quick")
    env.gateway(ctx).fail.add("spec.dsgn")
    out = await env.run_stage(ctx, "design", design)
    assert out.status == "awaiting_g2"
    assert env.gateway(ctx).count("spec.dsgn") == 2
    (d,) = ctx.graph.directions.values()
    assert d.degraded and ctx.graph.tokens[d.id]["names"]


async def test_truncation_falls_back_without_a_second_call(env):
    ctx = env.ctx("standard")
    env.gateway(ctx).truncate_over["spec.dsgn"] = -1  # the provider says "truncated" for every call
    await design(ctx)
    assert env.gateway(ctx).count("spec.dsgn") == 1
    assert all(d.degraded for d in ctx.graph.directions.values())


async def test_a_budget_refusal_propagates_and_is_never_hidden_behind_a_fallback(env):
    ctx = env.ctx("standard")

    class Refusing:
        async def run(self, spec, input, c):
            raise SpendRefused("budget", "cap")

        async def budget_status(self):
            raise NotImplementedError

    ctx.runner._gateway = Refusing()
    out = await env.run_stage(ctx, "design", design)
    assert out.status == "partial" and out.reason == "budget"
    assert not ctx.graph.directions
