"""MOCK-HERO: the skeleton is minted by code, the model fills the words, shared values are derived once, nothing unsafe."""

from __future__ import annotations

import asyncio
import random

from mde_spec_agent.lanes.screens.mock_hero import mock as hero_mock
from mde_spec_agent.lanes.screens.mock_hero.schema import HeroValueOut
from mde_spec_agent.lanes.screens.mock_hero.step import hero_step, split_group
from mde_spec_agent.lanes.screens.mock_hero.validate import validate
from mde_spec_core.config import default_config
from mde_spec_core.hero import fill_plan, mint_hero_skeleton, shared_value_plan
from mde_spec_core.mock import assert_fixture_safe, find_unsafe
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context

CFG = default_config()


def in_scope(g):
    return [e for e in g.entities.values() if not e.later and e.status == "in"]


def test_skeleton_is_covered_and_every_record_is_filled(finished, key):
    g = finished(key).graph
    assert g.hero
    for e in in_scope(g):
        recs = [h for h in g.hero.values() if h.entity == e.id]
        assert recs, e.id
        assert {h.state for h in recs if h.state} >= set(
            e.lifecycle.states
        )  # every lifecycle state has a record
        lo, hi = CFG.profiles.hero_per_other_entity
        if e.klass == "primary":
            lo, hi = CFG.profiles.hero_per_primary_entity[g.meta.profile]
        assert max(lo, len(e.lifecycle.states)) <= len(recs) <= max(hi, len(e.lifecycle.states))
        assert all(h.filled and h.synthetic for h in recs)
    v41 = [f for f in run_all(make_context(g), gate=3, only={"V-41"}) if f.severity == "blocker"]
    # a primary entity that is only `later` has no records by design (doc 18 5): that is spec-core's V-41, not this lane
    assert all(
        g.entities[f.where.split(".")[0]].later or "asset need" in f.why
        for f in v41
        if f.where in g.entities or "." in f.where
    )


def test_the_model_never_mints_ids(finished, key):
    run = finished(key)
    base = run.baseline
    ids_after = set(run.graph.hero)
    skeleton = mint_hero_skeleton(
        base, random.Random(f"{run.ctx.program_id}:{run.ctx.input_hash}:hero"), config=CFG
    )
    assert ids_after == set(skeleton)
    assert {h.relations.items().__len__() for h in run.graph.hero.values()} == {
        h.relations.items().__len__() for h in skeleton.values()
    }


def test_shared_values_are_derived_from_the_parent_once(finished, key):
    g = finished(key).graph
    plan = shared_value_plan(g)
    for sv in plan:
        for rec in g.hero.values():
            if rec.entity != sv.entity:
                continue
            parent = g.hero.get(rec.relations.get(sv.via, ""))
            if parent and sv.from_field in parent.content:
                assert rec.content.get(sv.field) == parent.content[sv.from_field]


def test_no_unsafe_value_and_briefs_carry_no_url(finished, key):
    run = finished(key)
    g = run.graph
    texts = [v for h in g.hero.values() for v in h.content.values()] + [
        h.image_brief for h in g.hero.values() if h.image_brief
    ]
    allowed = [a.name for a in g.apps.values()]
    assert_fixture_safe(texts, allowed_names=allowed)
    for e in in_scope(g):
        if any(f.media for f in e.fields):
            for h in g.hero.values():
                if h.entity == e.id:
                    assert (
                        h.image_brief
                        and h.image_brief.startswith("subject:")
                        and "http" not in h.image_brief
                    )


def test_contact_fields_are_masked_or_at_the_example_domain(finished, key):
    g = finished(key).graph
    for h in g.hero.values():
        for k, v in h.content.items():
            if "email" in k:
                assert v.endswith("@example.com"), v
            if "phone" in k:
                assert "•" in v or "x" in v.lower() or not any(c.isdigit() for c in v)


def test_an_existing_skeleton_is_kept(fresh):
    run = fresh("quick")
    sk = mint_hero_skeleton(run.graph, random.Random("other-seed"), config=CFG)
    run.graph.hero = dict(sk)
    asyncio.run(hero_step(run.ctx))
    assert set(run.graph.hero) == set(sk)
    assert all(h.filled for h in run.graph.hero.values())


def test_unsafe_model_values_are_refused_then_fixed_by_the_one_reask(fresh, monkeypatch):
    run = fresh("quick")
    real = hero_mock.handle

    def handle(inp):
        out = real(inp)
        if not inp.reask and out.values:
            v = out.values[0]
            out.values[0] = HeroValueOut(
                record=v.record, field=v.field, value="see https://example-shop.test/item"
            )
        return out

    monkeypatch.setattr(hero_mock, "handle", handle)
    asyncio.run(hero_step(run.ctx))
    assert not [f for f in run.ctx.flags if f["kind"] == "degraded"]
    texts = [v for h in run.graph.hero.values() for v in h.content.values()]
    assert not find_unsafe(texts, allowed_names=[a.name for a in run.graph.apps.values()])
    assert all("http" not in t for t in texts)


def test_a_call_that_keeps_failing_falls_back_flagged_with_pool_values(fresh, monkeypatch):
    run = fresh("quick")
    real = hero_mock.handle

    def handle(inp):
        out = real(inp)
        out.values.append(HeroValueOut(record="HERO-NOPE-01", field="x", value="y"))  # mints an id
        return out

    monkeypatch.setattr(hero_mock, "handle", handle)
    asyncio.run(hero_step(run.ctx))
    flags = [f for f in run.ctx.flags if f["kind"] == "degraded"]
    assert flags and "HERO-NOPE-01" not in run.graph.hero
    assert all(
        h.filled for h in run.graph.hero.values()
    )  # never left empty: the pool fallback filled them


def test_validator_rules(finished):
    run = finished("quick")
    g = run.baseline
    sk = mint_hero_skeleton(g, random.Random(1), config=CFG)
    g.hero = dict(sk)
    from mde_spec_agent.lanes.screens.mock_hero.generate import generate
    from mde_spec_agent.lanes.screens.mock_hero.schema import HeroIn
    from mde_spec_agent.lanes.screens.mock_hero.step import entity_in

    grp = fill_plan(g, config=CFG)[0]
    inp = HeroIn(
        language="en",
        country="",
        profile="compact",
        facts="",
        glossary="",
        names=[],
        allowed_names=[],
        entities=[entity_in(g, grp, e) for e in grp.entities],
        context=[],
    )
    good = generate(inp)
    assert validate(inp, good) == []
    bad = good.model_copy(update={"values": good.values[1:]})
    assert any("no value for" in e for e in validate(inp, bad))


def test_a_truncated_group_splits_into_its_parts(finished):
    g = finished("standard").baseline
    g.hero = dict(mint_hero_skeleton(g, random.Random(2), config=CFG))
    groups = fill_plan(g, config=CFG)
    big = max(groups, key=lambda x: len(x.entities))
    parts = split_group([big])
    if len(big.entities) > 1:
        assert sum(len(p[0].entities) for p in parts) == len(big.entities)
    one = split_group([parts[0][0]]) if parts else []
    for p in one:
        assert p[0].id != big.id
