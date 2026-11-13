"""The hero skeleton (doc 18 5, FN-12, FN-13, FN-32): counts by class, every state covered, stable ids, shared values."""

import itertools
import random

import pytest

from mde_spec_core.canon import canonical_json
from mde_spec_core.config import default_config
from mde_spec_core.graph import FieldDef
from mde_spec_core.hero import (
    apply_fill,
    derive_shared_values,
    fill_plan,
    hero_ref_index,
    mint_hero_skeleton,
    shared_value_plan,
)
from mde_spec_core.synth import make_graph
from mde_spec_core.validators import Context, run_all

CFG = default_config()


def _linked_graph(name="small"):
    """A graph with a parent/child pair that share an amount and a person's name (ENT-001 parent, ENT-002 child)."""
    g = make_graph(name)
    g.entities["ENT-001"].fields += [
        FieldDef(key="total", type="money"),
        FieldDef(key="customer_name", type="text"),
        FieldDef(key="media_cover", type="file", media=True),
    ]
    g.entities["ENT-002"].fields += [
        FieldDef(key="parent", type="ref", ref_entity="ENT-001"),
        FieldDef(key="amount", type="money"),
        FieldDef(key="name", type="text"),
        FieldDef(key="note", type="text"),
        FieldDef(key="contact_phone", type="phone"),
    ]
    return g


def _expected_range(entity, profile):
    states = len(entity.lifecycle.states)
    if entity.klass == "primary":
        lo, hi = CFG.profiles.hero_per_primary_entity[profile]
    else:
        lo, hi = CFG.profiles.hero_per_other_entity
    return max(lo, states), max(hi, states)


@pytest.mark.parametrize("preset", ["small", "medium", "big"])
def test_counts_per_class_and_every_state_covered(preset):
    g = make_graph(preset)
    hero = mint_hero_skeleton(g, random.Random(3))
    by_entity: dict[str, list] = {}
    for r in hero.values():
        by_entity.setdefault(r.entity, []).append(r)
    assert set(by_entity) == set(g.entities)
    for eid, e in g.entities.items():
        lo, hi = _expected_range(e, g.meta.profile)
        recs = by_entity[eid]
        assert lo <= len(recs) <= hi, (eid, e.klass, len(recs), lo, hi)
        assert len(recs) >= len(e.lifecycle.states)
        assert set(e.lifecycle.states) <= {
            r.state for r in recs
        }  # every lifecycle state has a record
        assert all(r.synthetic and not r.filled for r in recs)


def test_counts_follow_the_detail_profile():
    ranges = {}
    for preset, profile in (("small", "compact"), ("medium", "standard"), ("big", "deep")):
        g = make_graph(preset)
        assert g.meta.profile == profile
        hero = mint_hero_skeleton(g, random.Random(1))
        prim = [e for e in g.entities.values() if e.klass == "primary"]
        ranges[profile] = [sum(r.entity == e.id for r in hero.values()) for e in prim]
    assert all(8 <= n <= 12 for n in ranges["compact"])
    assert all(12 <= n <= 20 for n in ranges["standard"])
    assert all(12 <= n <= 30 for n in ranges["deep"])


def test_v41_is_satisfied_by_the_skeleton():
    g = make_graph("small")
    g.hero = mint_hero_skeleton(g, random.Random(2))
    findings = run_all(Context(g), only={"V-41"})
    assert [f.why for f in findings if f.severity == "blocker" and "hero record" in f.why] == []
    # control: a primary state with no record is caught
    primary = next(e for e in g.entities.values() if e.klass == "primary")
    state = primary.lifecycle.states[0]
    for hid in [h for h, r in g.hero.items() if r.entity == primary.id and r.state == state]:
        del g.hero[hid]
    caught = run_all(Context(g), only={"V-41"})
    assert any(f"no hero record in state {state}" in f.why for f in caught)


def test_ids_are_stable_and_the_same_in_every_app():
    """One order, one id: ids depend on the entity and the position only, never on the apps or screens."""
    a = make_graph("medium")
    b = make_graph("medium")
    for app_id in list(b.apps)[1:]:  # a program with fewer apps still names the same records
        del b.apps[app_id]
    for sid in [s for s in b.screens if b.screens[s].app_id != "APP-001"]:
        del b.screens[sid]
    ha = mint_hero_skeleton(a, random.Random(5))
    hb = mint_hero_skeleton(b, random.Random(5))
    assert set(ha) == set(hb)
    assert canonical_json({k: v.model_dump() for k, v in ha.items()}) == canonical_json(
        {k: v.model_dump() for k, v in hb.items()}
    )
    assert all(i.startswith("HERO-ENT-") for i in ha)


def test_same_seed_same_bytes_other_seed_other_records():
    g = make_graph("medium")
    one = mint_hero_skeleton(g, random.Random(7))
    two = mint_hero_skeleton(g, random.Random(7))
    other = mint_hero_skeleton(g, random.Random(8))
    dump = lambda h: canonical_json({k: v.model_dump() for k, v in h.items()})  # noqa: E731
    assert dump(one) == dump(two)
    assert dump(one) != dump(other)


def test_adding_an_entity_does_not_change_the_records_of_the_others():
    g = make_graph("small")
    before = mint_hero_skeleton(g, random.Random(4))
    extra = g.entities["ENT-005"].model_copy(deep=True)
    extra.id = "ENT-099"
    g.entities["ENT-099"] = extra
    after = mint_hero_skeleton(g, random.Random(4))
    for hid, rec in before.items():
        assert after[hid].model_dump() == rec.model_dump()


def test_later_entities_get_no_hero_records():
    g = make_graph("small")
    g.entities["ENT-005"].later = True
    hero = mint_hero_skeleton(g, random.Random(4))
    assert not any(r.entity == "ENT-005" for r in hero.values())


def test_relations_point_to_real_records_of_the_referenced_entity():
    g = _linked_graph()
    hero = mint_hero_skeleton(g, random.Random(6))
    kids = [r for r in hero.values() if r.entity == "ENT-002"]
    parents = {r.id for r in hero.values() if r.entity == "ENT-001"}
    assert kids and all(r.relations["parent"] in parents for r in kids)
    # every parent gets a child before any parent gets a second one
    assert len({r.relations["parent"] for r in kids}) == min(len(kids), len(parents))


def test_a_self_reference_forms_a_tree_without_cycles():
    g = make_graph("small")
    g.entities["ENT-001"].fields.append(FieldDef(key="up", type="ref", ref_entity="ENT-001"))
    hero = mint_hero_skeleton(g, random.Random(2))
    recs = sorted((r for r in hero.values() if r.entity == "ENT-001"), key=lambda r: r.id)
    assert recs[0].relations["up"] == ""
    for i, r in enumerate(recs[1:], start=1):
        assert r.relations["up"] in {x.id for x in recs[:i]}


def test_code_owned_fields_are_set_by_the_skeleton_not_the_model():
    g = _linked_graph()
    g.entities["ENT-001"].fields.append(FieldDef(key="due_date", type="date"))
    hero = mint_hero_skeleton(g, random.Random(2))
    rec = next(r for r in hero.values() if r.entity == "ENT-001")
    assert rec.content["status"] == rec.state
    assert rec.content["due_date"].startswith("now+")  # a due date is in the future
    assert all(
        not any(c in v for c in ("http", "@")) for r in hero.values() for v in r.content.values()
    )


# ------------------------------------------------------------------ shared values (FN-32)


def test_shared_values_are_found_by_group_and_type():
    g = _linked_graph()
    plan = {(s.entity, s.field): s for s in shared_value_plan(g)}
    assert plan[("ENT-002", "amount")].from_field == "total"
    assert plan[("ENT-002", "name")].from_field == "customer_name"
    assert plan[("ENT-002", "amount")].via == "parent"
    assert ("ENT-002", "note") not in plan


def test_a_derived_value_appears_once_and_is_never_written_twice():
    g = _linked_graph()
    g.hero = mint_hero_skeleton(g, random.Random(6))
    parents = [r for r in g.hero.values() if r.entity == "ENT-001"]
    kids = [r for r in g.hero.values() if r.entity == "ENT-002"]
    filled = {}
    for i, p in enumerate(parents):
        filled[p.id] = {"content": {"total": f"₹{100 + i}.00", "customer_name": f"Customer {i}"}}
    for k in kids:
        # a filler that writes the shared values anyway (with another value) must lose
        filled[k.id] = {"content": {"note": "n", "amount": "₹999999.00", "name": "Wrong"}}
    res = apply_fill(g, filled)
    assert res.derived > 0
    by_id = {r.id: r for r in g.hero.values()}
    for k in kids:
        parent = by_id[k.relations["parent"]]
        assert k.content["amount"] == parent.content["total"]
        assert k.content["name"] == parent.content["customer_name"]
        assert set(res.dropped_code_owned[k.id]) == {"amount", "name"}
    assert not res.rejected


def test_fill_plan_never_asks_the_model_for_derived_or_code_owned_fields():
    g = _linked_graph()
    g.hero = mint_hero_skeleton(g, random.Random(6))
    group = next(x for x in fill_plan(g) if "ENT-002" in x.entities)
    keys = {f["key"] for f in group.write_fields["ENT-002"]}
    assert "note" in keys and "contact_phone" in keys
    assert not keys & {"amount", "name", "parent", "status", "id"}
    assert {d["field"] for d in group.derived["ENT-002"]} == {"amount", "name"}
    assert "parent" in group.code_set["ENT-002"] and "status" in group.code_set["ENT-002"]
    phone_rule = next(
        f["rule"] for f in group.write_fields["ENT-002"] if f["key"] == "contact_phone"
    )
    assert "masked" in phone_rule
    assert group.image_brief_for["ENT-001"] == ["media_cover"]


def test_related_entities_go_in_the_same_call_and_unrelated_ones_do_not():
    g = _linked_graph()
    g.hero = mint_hero_skeleton(g, random.Random(6))
    groups = fill_plan(g)
    same = [x for x in groups if "ENT-001" in x.entities]
    assert len(same) == 1 and "ENT-002" in same[0].entities
    assert any("ENT-004" in x.entities and "ENT-001" not in x.entities for x in groups)
    covered = [e for x in groups for e in x.entities]
    assert sorted(covered) == sorted(set(covered)) == sorted(g.entities)


def test_a_long_chain_is_split_at_the_entity_limit_parents_first():
    g = make_graph("big")
    ids = [f"ENT-{i:03d}" for i in range(1, 8)]
    for prev, cur in itertools.pairwise(ids):
        g.entities[cur].fields.append(FieldDef(key="parent", type="ref", ref_entity=prev))
    g.hero = mint_hero_skeleton(g, random.Random(1))
    groups = [x for x in fill_plan(g) if set(x.entities) & set(ids)]
    cap = CFG.fusion.entities_per_call_max
    assert all(len(x.entities) <= cap for x in groups)
    assert groups[0].entities[0] == "ENT-001" and groups[0].split_from_larger
    later = groups[1]
    assert later.context_entities and set(later.context_entities) <= set(groups[0].entities)


# ------------------------------------------------------------------ applying a fill safely


def test_apply_fill_rejects_unsafe_values_and_keeps_the_rest_of_the_record():
    g = _linked_graph()
    g.hero = mint_hero_skeleton(g, random.Random(6))
    kid = next(r for r in g.hero.values() if r.entity == "ENT-002")
    res = apply_fill(
        g,
        {
            kid.id: {
                "content": {
                    "note": "Bought on https://shop.test/x",
                    "contact_phone": "+91 98765 43210",
                    "ENT-002.bogus": "x",
                },
                "image_brief": "see http://x.test/p.jpg",
            },
            "HERO-ENT-002-99": {"content": {"note": "x"}},
        },
    )
    assert kid.id in res.applied and kid.filled
    assert any("url" in p for p in res.rejected[kid.id])
    assert any("phone_unmasked" in p for p in res.rejected[kid.id])
    assert any("not a field" in p for p in res.rejected[kid.id])
    assert any("image_brief" in p for p in res.rejected[kid.id])
    assert "note" not in kid.content and "contact_phone" not in kid.content
    assert kid.image_brief is None
    assert "no such hero record" in res.rejected["HERO-ENT-002-99"][0]


def test_apply_fill_takes_masked_phone_example_email_and_a_brief_without_a_url():
    g = _linked_graph()
    g.entities["ENT-002"].fields.append(FieldDef(key="contact_email", type="email"))
    g.hero = mint_hero_skeleton(g, random.Random(6))
    kid = next(r for r in g.hero.values() if r.entity == "ENT-002")
    res = apply_fill(
        g,
        {
            kid.id: {
                "content": {
                    "note": "ripe produce",
                    "contact_phone": "+91 98••• ••210",
                    "contact_email": "ravi@example.com",
                },
                "image_brief": "ripe tomatoes in a crate; count: 6; avoid: brand marks",
            }
        },
    )
    assert not res.rejected
    assert kid.content["contact_phone"] == "+91 98••• ••210"
    assert kid.image_brief.startswith("ripe tomatoes")
    assert kid.synthetic


def test_a_business_name_the_user_gave_may_hold_a_deny_listed_word():
    g = _linked_graph()
    g.hero = mint_hero_skeleton(g, random.Random(6))
    kid = next(r for r in g.hero.values() if r.entity == "ENT-002")
    bad = apply_fill(g, {kid.id: {"content": {"note": "Tata Tea Stall"}}})
    assert "note" not in kid.content and bad.rejected
    ok = apply_fill(
        g, {kid.id: {"content": {"note": "Tata Tea Stall"}}}, allowed_names=["Tata Tea Stall"]
    )
    assert kid.content["note"] == "Tata Tea Stall" and not ok.rejected


def test_derive_shared_values_settles_a_chain_of_parents():
    g = _linked_graph()
    g.entities["ENT-003"].fields += [
        FieldDef(key="parent", type="ref", ref_entity="ENT-002"),
        FieldDef(key="amount", type="money"),
    ]
    g.hero = mint_hero_skeleton(g, random.Random(6))
    for r in g.hero.values():
        if r.entity == "ENT-001":
            r.content["total"] = f"₹{len(r.id)}00.00 {r.id[-2:]}"
    derive_shared_values(g)
    by_id = g.hero
    for r in g.hero.values():
        if r.entity == "ENT-003":
            mid = by_id[r.relations["parent"]]
            top = by_id[mid.relations["parent"]]
            assert r.content["amount"] == mid.content["amount"] == top.content["total"]


def test_hero_ref_index_resolves_entity_and_state():
    g = make_graph("small")
    g.hero = mint_hero_skeleton(g, random.Random(2))
    idx = hero_ref_index(g)
    e = g.entities["ENT-001"]
    for st in e.lifecycle.states:
        assert idx[f"ENT-001:{st}"]
    assert set(idx["ENT-001"]) == {r.id for r in g.hero.values() if r.entity == "ENT-001"}
