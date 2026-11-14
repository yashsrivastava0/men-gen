"""Mechanical repairs of DSGN's output (R-code): near misses cost no re-ask, are logged and replay identically."""

from __future__ import annotations

import dataclasses

from mde_spec_agent.lanes.design import design
from mde_spec_agent.lanes.design.brief import build_dsgn_input
from mde_spec_agent.lanes.design.dsgn import mock as mock_mod
from mde_spec_agent.lanes.design.dsgn.repair import repair
from mde_spec_agent.lanes.design.dsgn.schema import AssetOut
from mde_spec_agent.lanes.design.dsgn.validate import validate
from mde_spec_core.config import default_config


def patched(monkeypatch, change):
    real = mock_mod.handle
    monkeypatch.setattr(mock_mod, "handle", lambda inp: change(real(inp)))


def each(out, **upd):
    return out.model_copy(update={"directions": [d.model_copy(update=upd) for d in out.directions]})


async def test_a_deep_dark_stance_the_profile_does_not_accept_is_mapped_without_a_reask(
    env, monkeypatch
):
    patched(monkeypatch, lambda o: each(o, dark_stance="supported"))
    ctx = env.ctx("thorough")
    await design(ctx)
    assert env.gateway(ctx).count("spec.dsgn") == 1  # no re-ask, no fallback
    assert not any(d.degraded for d in ctx.graph.directions.values())
    assert {d.dark_stance for d in ctx.graph.directions.values()} == {"parity"}
    rows = [r for r in ctx.rcode.rows() if r["what"] == "dsgn.dark_stance"]
    assert len(rows) == 3
    assert rows[0]["kind"] == "exact_synonym"
    assert (rows[0]["node_id"], rows[0]["before"], rows[0]["after"]) == (
        "DIR-S001",
        "supported",
        "parity",
    )


async def test_the_same_stance_is_left_alone_for_standard_apps(env, monkeypatch):
    patched(monkeypatch, lambda o: each(o, dark_stance="light_first"))
    ctx = env.ctx("standard")
    await design(ctx)
    assert {d.dark_stance for d in ctx.graph.directions.values()} == {"light_first"}
    assert ctx.rcode.rows() == [] and env.gateway(ctx).count("spec.dsgn") == 1


async def test_a_missing_deep_admin_variant_is_filled_from_the_data_defaults(env, monkeypatch):
    patched(
        monkeypatch,
        lambda o: each(
            o, admin_density=None, admin_motion=None, admin_ambition=None, admin_note=""
        ),
    )
    ctx = env.ctx("thorough")
    await design(ctx)
    assert env.gateway(ctx).count("spec.dsgn") == 1
    assert all(
        "For admin and operations apps: density dense, motion minimal, ambition refined"
        in d.audience_context
        for d in ctx.graph.directions.values()
    )
    kinds = {r["kind"] for r in ctx.rcode.rows()}
    assert kinds == {"instantiate_library_default"}


async def test_numbers_and_lists_out_of_range_are_clamped_or_cut(env, monkeypatch):
    patched(
        monkeypatch,
        lambda o: each(o, delight_budget=9, avoid=[f"thing {i}" for i in range(12)]),
    )
    ctx = env.ctx("standard")
    await design(ctx)
    assert env.gateway(ctx).count("spec.dsgn") == 1
    for d in ctx.graph.directions.values():
        assert d.delight_budget == 3 and len(d.avoid) == 6
    assert {r["what"] for r in ctx.rcode.rows()} == {"dsgn.delight_budget", "dsgn.avoid"}


async def test_asset_needs_with_an_unknown_app_a_bad_count_or_a_repeat_are_repaired(
    env, monkeypatch
):
    def change(o):
        a = o.assets[0]
        bad = a.model_copy(update={"app": "APP-999", "count": 99})
        return o.model_copy(update={"assets": [bad, bad, *o.assets[1:]]})

    patched(monkeypatch, change)
    ctx = env.ctx("standard")
    await design(ctx)
    assert env.gateway(ctx).count("spec.dsgn") == 1
    mine = [a for a in ctx.graph.assets.values() if any(p.ref == "spec.dsgn" for p in a.provenance)]
    first = mine[0]
    assert first.count == 12 and set(first.used_by) == set(ctx.graph.apps)
    assert len(mine) == len(
        {(a.kind, a.purpose, a.subject) for a in mine}
    )  # the repeat was dropped
    assert {r["what"] for r in ctx.rcode.rows()} >= {
        "dsgn.assets.app",
        "dsgn.assets.count",
        "dsgn.assets.repeat",
    }


async def test_more_asset_needs_than_allowed_are_cut(env, monkeypatch):
    def change(o):
        extra = [
            AssetOut(
                kind="illustration",
                purpose="decorative",
                subject=f"Quiet picture {i}",
                count=1,
                avoid=["readable text"],
                app="",
            )
            for i in range(30)
        ]
        return o.model_copy(update={"assets": [*o.assets, *extra]})

    patched(monkeypatch, change)
    ctx = env.ctx("quick")
    await design(ctx)
    si, g = env.baseline("quick")
    inp, _ = build_dsgn_input(g, si, default_config(), "p", "h")
    mine = [a for a in ctx.graph.assets.values() if any(p.ref == "spec.dsgn" for p in a.provenance)]
    assert len(mine) == inp.max_assets and env.gateway(ctx).count("spec.dsgn") == 1


async def test_repairs_are_logged_again_on_a_resume_replay(env, monkeypatch):
    patched(monkeypatch, lambda o: each(o, dark_stance="supported"))
    ctx1 = env.ctx("thorough")
    await design(ctx1)
    _, fresh = env.baseline("thorough")
    ctx2 = dataclasses.replace(ctx1, graph=fresh, flags=[], plan={}, rcode=type(ctx1.rcode)())
    await design(ctx2)
    assert ctx2.rcode.rows() == ctx1.rcode.rows() and len(ctx2.rcode) == 3
    assert ctx2.graph.canonical_text() == ctx1.graph.canonical_text()


def test_repair_function_maps_near_words_and_falls_back_to_the_default(env):
    from mde_spec_agent.lanes.design.dsgn._compose import compose

    si, g = env.baseline("thorough")
    inp, _ = build_dsgn_input(g, si, default_config(), "p", "h")
    out = compose(inp)
    # near words are not in the closed set, so build them without validation
    d0 = out.directions[0].model_copy(update={"dark_stance": "equal"})
    d1 = out.directions[1].model_copy(update={"dark_stance": "night"})
    d2 = out.directions[2].model_copy(update={"dark_stance": "whatever"})
    fixed, fixes = repair(inp, out.model_copy(update={"directions": [d0, d1, d2]}))
    assert [d.dark_stance for d in fixed.directions] == ["parity", "dark_first", "parity"]
    assert [f.after for f in fixes] == ["parity", "dark_first", "parity"]
    assert repair(inp, out)[1] == []  # nothing to repair in a clean answer


def test_the_validator_no_longer_asks_again_for_a_repairable_miss(env):
    from mde_spec_agent.lanes.design.dsgn._compose import compose

    si, g = env.baseline("thorough")
    inp, _ = build_dsgn_input(g, si, default_config(), "p", "h")
    out = compose(inp)
    bad = out.model_copy(
        update={
            "directions": [
                d.model_copy(
                    update={"dark_stance": "supported", "delight_budget": 7, "admin_note": ""}
                )
                for d in out.directions
            ]
        }
    )
    assert validate(inp, bad) == []
