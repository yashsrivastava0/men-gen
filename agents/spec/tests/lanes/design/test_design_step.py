"""The `design` step on the baseline graphs of the three real exports (compact, standard, deep), mock mode."""

from __future__ import annotations

import dataclasses
import re

from mde_spec_agent.lanes.design import design, gloss  # noqa: F401  (gloss registered with design)
from mde_spec_agent.lanes.design.build import capability_flags, token_flags
from mde_spec_core import assets, libraries, tokens, vocab
from mde_spec_core.config import default_config
from mde_spec_core.graph import SpecGraph
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context

COUNT = {"quick": 1, "standard": 3, "thorough": 3}
CFG = default_config()


def picks(graph: SpecGraph) -> list[dict[str, object]]:
    return [
        {
            "mood_family": d.mood,
            "shape": d.shape,
            "density": d.density,
            "depth": d.depth,
            "motion_personality": d.motion_personality,
            "imagery": d.imagery,
            "type_personality": d.type_personality,
            "seed_hue": d.seed_hue,
        }
        for _, d in sorted(graph.directions.items())
    ]


async def test_directions_count_per_profile_and_the_set_rules_hold(env, key):
    ctx = env.ctx(key)
    out = await env.run_stage(ctx, "design", design)
    assert out.status == "awaiting_g2"
    g = ctx.graph
    assert len(g.directions) == COUNT[key]
    assert sorted(g.tokens) == sorted(g.directions)
    assert tokens.check_direction_set(picks(g), brand_locked=False) == []
    assert ctx.runner is not None
    assert [(c, r.used_fallback) for c, r in ctx.runner.calls] == [("spec.dsgn", False)]
    assert not [f for f in ctx.flags if f["kind"] == "degraded_node"]
    for d in g.directions.values():
        assert d.tier == "open" and not d.brand_locked and not d.degraded
        assert d.voice_line and d.avoid and len(d.emotional_goals) >= 3
        assert d.id.startswith("DIR-S")
        assert 0 <= d.delight_budget <= 3


async def test_pairwise_axis_differences_and_hue_gaps(env):
    ctx = env.ctx("standard")
    await env.run_stage(ctx, "design", design)
    ds = [d for _, d in sorted(ctx.graph.directions.items())]
    for i, a in enumerate(ds):
        for b in ds[i + 1 :]:
            diff = vocab.direction_axis_differences(
                {"mood_family": a.mood, "shape": a.shape, "density": a.density, "depth": a.depth,
                 "motion_personality": a.motion_personality, "imagery": a.imagery,
                 "type_personality": a.type_personality},
                {"mood_family": b.mood, "shape": b.shape, "density": b.density, "depth": b.depth,
                 "motion_personality": b.motion_personality, "imagery": b.imagery,
                 "type_personality": b.type_personality},
            )  # fmt: skip
            assert len(diff) >= CFG.checks.min_direction_axis_differences
            gap = abs(a.seed_hue - b.seed_hue) % 360
            assert min(gap, 360 - gap) >= CFG.checks.min_seed_hue_gap_degrees


async def test_tokens_pass_v12_light_and_dark(env, key):
    ctx = env.ctx(key)
    await env.run_stage(ctx, "design", design)
    g = ctx.graph
    findings = run_all(make_context(g), gate=3, only={"V-12"})
    assert [f for f in findings if f.severity == "blocker"] == []
    need = CFG.checks.wcag_body_text_ratio
    for tk in g.tokens.values():
        names = set(tk["names"])
        assert set(tk["refs"]) <= names
        for scheme in ("light", "dark"):
            body = [p for p in tk["contrast"] if p["scheme"] == scheme and p["body"]]
            assert body and min(p["ratio"] for p in body) >= need
        assert not [
            n for n in names if n.startswith("ext.")
        ]  # spring, blur and ext.* are the front end's
        assert "ext" not in tk["tree"]


async def test_locked_brand_colour_stays_exact_and_text_on_it_is_derived(env):
    si, _ = env.baseline("standard")
    brand = env.with_brand(si, "Mandi Direct", "#F4C430")  # a yellow: white text on it fails 4.5:1
    ctx = env.ctx("standard", si=brand)
    await env.run_stage(ctx, "design", design)
    g = ctx.graph
    need = CFG.checks.wcag_body_text_ratio
    assert len(g.directions) == 3
    for did, d in g.directions.items():
        assert d.brand_locked and d.seed_colour == "#f4c430" and d.seed_hue is None
        assert (
            d.tier == "open"
        )  # the direction and voice stay Open; only the brand roles are Locked
        assert any(p.kind == "user" for p in d.provenance)
        tk = g.tokens[did]
        for scheme in ("light", "dark"):
            assert tk["tree"]["color"]["scheme"][scheme]["brand"]["$value"] == "#f4c430"
            assert f"color.scheme.{scheme}.brand" in tk["locked_roles"]
        on = tk["report"]["derived_text_on_brand"]
        assert on["changed_from_white"] and on["ratio"] >= need and on["ratio_with_white"] < need
        pair = [
            p for p in tk["contrast"] if p["fg"].endswith(".on_brand") and p["scheme"] == "light"
        ]
        assert pair and pair[0]["ratio"] >= need
    assert (
        tokens.check_direction_set(picks(g), brand_locked=True) == []
    )  # axes still differ; hue is not asked
    kinds = [f["kind"] for f in ctx.flags]
    assert "brand_colour_text_derived" in kinds


async def test_a_seed_colour_from_the_model_never_overrides_a_locked_brand(env, monkeypatch):
    from mde_spec_agent.lanes.design.dsgn import mock as mock_mod

    real = mock_mod.handle

    def greedy(inp):
        out = real(inp)
        return out.model_copy(
            update={
                "directions": [
                    d.model_copy(update={"seed_colour": "#2255cc"}) for d in out.directions
                ]
            }
        )

    monkeypatch.setattr(mock_mod, "handle", greedy)
    si, _ = env.baseline("quick")
    ctx = env.ctx("quick", si=env.with_brand(si, "Sunrise Yoga", "#7A3E9D"))
    await env.run_stage(ctx, "design", design)
    (d,) = ctx.graph.directions.values()
    assert d.seed_colour == "#7a3e9d" and d.brand_locked
    assert (
        ctx.graph.tokens[d.id]["tree"]["color"]["scheme"]["light"]["brand"]["$value"] == "#7a3e9d"
    )


async def test_a_brand_setting_that_is_not_locked_is_not_a_brand_colour(env):
    si, _ = env.baseline("quick")
    ctx = env.ctx("quick", si=env.with_brand(si, "Sunrise Yoga", "#7A3E9D", grade="default"))
    await env.run_stage(ctx, "design", design)
    (d,) = ctx.graph.directions.values()
    assert not d.brand_locked and d.seed_colour != "#7a3e9d"


async def test_unbound_asset_needs_satisfy_v43_and_are_not_layout(env, key):
    ctx = env.ctx(key)
    await env.run_stage(ctx, "design", design)
    g = ctx.graph
    assert g.assets
    assert run_all(make_context(g), gate=3, only={"V-43"}) == []
    assert all(not a.bound for a in g.assets.values())
    ids = sorted(g.assets)
    assert ids == sorted(set(ids)) and all(re.fullmatch(r"ASSET-S\d{3}", i) for i in ids)
    app_ids = set(g.apps)
    kinds = {a.kind for a in g.assets.values()}
    assert "brand_mark" in kinds  # the code baseline: mark, app icon, adaptive icon, launch image
    assert kinds & {"illustration", "animation_clip"}  # the model's own needs
    for a in g.assets.values():
        assert set(a.used_by) <= app_ids and a.used_by
        assert a.kind in vocab.asset_kinds()
        assert assets.lint_brief(a.subject, *a.avoid) == []  # no lighting, placement, size or crop
        assert "http" not in a.subject.lower()


async def test_no_layout_or_colour_word_in_any_free_text(env, key):
    ctx = env.ctx(key)
    await env.run_stage(ctx, "design", design)
    for d in ctx.graph.directions.values():
        texts = [d.name, d.audience_context, d.voice_line, *d.avoid, *d.emotional_goals]
        for t in texts:
            assert libraries.form_word_findings(t) == [], (d.id, t)
    for a in ctx.graph.assets.values():
        for t in (a.subject, *a.avoid):
            assert libraries.form_word_findings(t) == [], (a.id, t)


async def test_deep_adds_the_admin_variant_and_dark_mode_parity(env):
    deep = env.ctx("thorough")
    await env.run_stage(deep, "design", design)
    for d in deep.graph.directions.values():
        assert "For admin and operations apps" in d.audience_context
        assert d.dark_stance in ("parity", "dark_first", "follows_time_of_day")
    std = env.ctx("standard")
    await env.run_stage(std, "design", design)
    assert all(
        "admin and operations" not in d.audience_context for d in std.graph.directions.values()
    )
    assert not [
        f for f in deep.flags if f["kind"] == "dark_mode_parity"
    ]  # contrast holds in both schemes


async def test_dsgn_does_not_write_signature_moments_or_screens(env):
    ctx = env.ctx("standard")
    before = SpecGraph.model_validate(ctx.graph.model_dump())
    await env.run_stage(ctx, "design", design)
    g = ctx.graph
    assert g.screens == before.screens and g.flows == before.flows and g.features == before.features
    assert all(not s.could for s in g.screens.values())


async def test_risky_capabilities_the_brief_implies_are_flagged_for_g2(env):
    ctx = env.ctx("standard")
    some = sorted(ctx.graph.features.values(), key=lambda f: f.id)[0]
    some.capability, some.capability_tier = "nfc", "B"
    await env.run_stage(ctx, "design", design)
    risk = {f["capability"]: f for f in ctx.flags if f["kind"] == "capability_risk"}
    assert risk["nfc"]["tier"] == "B" and risk["nfc"]["implied_by"] == [f"feature {some.id}"]
    # a tier A capability is not a risk
    assert "haptics" not in risk and "vector_animation_clips" not in risk


def test_capability_flags_skip_tier_a_and_report_unknown_ids(env):
    _, g = env.baseline("standard")
    f = sorted(g.features.values(), key=lambda x: x.id)[0]
    f.capability = "not_in_the_sheet"
    out = capability_flags(g, [], [])
    assert out == [
        {
            "kind": "capability_unknown",
            "capability": "not_in_the_sheet",
            "implied_by": [f"feature {f.id}"],
        }
    ]


def test_token_flags_report_missing_script_fonts_and_weak_dark_mode(env):
    from mde_spec_core.graph import Direction

    d = Direction(id="DIR-S001", name="x", mood="calm_trust")
    toks = {
        "DIR-S001": {
            "report": {
                "statements": [],
                "flags": ["script_unverified:Ge'ez"],
                "scripts": [{"script": "Ge'ez", "source": "missing"}],
            },
            "contrast": [
                {"scheme": "light", "body": True, "ratio": 7.0},
                {"scheme": "dark", "body": True, "ratio": 3.9},
            ],
        }
    }
    out = token_flags([d], toks, CFG, "deep")
    kinds = {f["kind"]: f for f in out}
    assert kinds["dark_mode_parity"]["schemes_below_target"] == ["dark"]
    assert "script_without_font:Ge'ez" in kinds["token_notice"]["notes"]
    assert [f["kind"] for f in token_flags([d], toks, CFG, "standard")] == ["token_notice"]


async def test_resume_replays_the_checkpoint_with_identical_bytes_and_no_new_call(env, key):
    ctx1 = env.ctx(key)
    await design(ctx1)
    text1 = ctx1.graph.canonical_text()
    assert ctx1.runner is not None
    n_calls = len(ctx1.runner.calls)
    _, fresh = env.baseline(key)
    ctx2 = dataclasses.replace(ctx1, graph=fresh, flags=[], plan={})
    await design(ctx2)
    assert ctx2.graph.canonical_text() == text1
    assert (
        len(ctx1.runner.calls) == n_calls
    )  # replayed from the store: the model was not asked again
    assert ctx2.flags == ctx1.flags


async def test_the_design_step_is_deterministic_across_runs(env):
    a, b = env.ctx("standard"), env.ctx("standard")
    await design(a)
    await design(b)
    assert a.graph.canonical_text() == b.graph.canonical_text()


async def test_a_code_only_run_without_a_runner_uses_the_deterministic_directions_and_says_so(env):
    ctx = env.ctx("standard")
    ctx.runner = None
    await design(ctx)
    assert len(ctx.graph.directions) == 3
    assert all(d.degraded and d.assumed for d in ctx.graph.directions.values())
    assert [f for f in ctx.flags if f["kind"] == "degraded_node"]
