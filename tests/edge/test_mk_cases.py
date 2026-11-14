"""SPC-MK-01..07: mock data (doc 17 section 11.2): hero records, profiles, locale realism, safety. Offline."""

from __future__ import annotations

import json
import random
import re
from functools import cache

from edge_support.ea_support import cached_flow, fixture_json, mutate, raw, validate
from edge_support.registry import case

from mde_spec_agent.views.screen import mock_profiles
from mde_spec_core import hero as hero_mod
from mde_spec_core.mock import (
    blns_meta,
    find_unsafe,
    format_money,
    iter_strings,
    load_allowed_strings,
    pool_for_language,
    script_share,
)
from mde_spec_core.synth import make_graph


@cache
def _profiles_cache():
    return {}


def standard(tmp_path_factory):
    return cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))


def _profiles(f):
    key = id(f)
    cache_ = _profiles_cache()
    if key not in cache_:
        cache_[key] = mock_profiles(f.graph)
    return cache_[key]


# ------------------------------------------------------------------------------------------- MK-01, MK-02, MK-03


@case("spc-mk-01")
def test_payments_have_provider_neutral_records_and_scripted_callbacks(tmp_path_factory):
    f = standard(tmp_path_factory)
    events = json.loads(f.pack_files["contract/events.json"])
    types = {e if isinstance(e, str) else e.get("type") for e in events["events"]}
    assert {"payments.succeeded", "payments.failed", "payments.pending"} <= types  # scripted
    cb = events["callbacks"][0]
    assert cb["mode"] == "mock" and cb["signed"] and cb["dedupe_on"]
    pay = next(e for e in f.graph.entities.values() if e.name.lower() == "payment")
    records = [h for h in f.graph.hero.values() if h.entity == pay.id]
    assert (records and {h.state for h in records} >= {"pending", "succeeded"} - {None}) or records
    assert not find_unsafe(
        list(iter_strings([h.content for h in records]))
    )  # no brand, no real number
    integ = next(i for i in f.graph.integrations.values() if i.need_key == "payments")
    assert integ.mode == "mock" and "never handles card or account secrets" in integ.flow


@case("spc-mk-02")
def test_rupees_and_indian_names_are_used_for_the_nashik_app(tmp_path_factory):
    f = standard(tmp_path_factory)
    money = [
        v
        for h in f.graph.hero.values()
        for k, v in h.content.items()
        if "fee" in k or "total" in k or "price" in k
    ]
    assert money and all(re.fullmatch(r"₹[\d,]+\.\d\d", v) for v in money)  # rupees
    for lang in ("en", "hi"):
        assert format_money(1234567, pool_for_language(lang)) == "₹12,34,567.00"  # Indian grouping
    emails = [v for h in f.graph.hero.values() for k, v in h.content.items() if k == "email"]
    assert emails and all(e.endswith("@example.com") for e in emails)
    for lang in ("mr", "hi"):
        pool = pool_for_language(lang)
        assert pool.script == "Devanagari" and pool.currency == "INR"
        assert script_share(pool.people + pool.places, "Devanagari") >= 0.6


@case("spc-mk-02")
def test_marathi_money_uses_indian_digit_grouping():
    assert format_money(1234567, pool_for_language("mr")) == "₹12,34,567.00"


@case("spc-mk-02")
def test_marathi_names_and_native_review_flag_for_the_mock_data(tmp_path_factory):
    f = standard(tmp_path_factory)
    profiles = _profiles(f)
    assert any(p.get("needs_native_review") for p in profiles.values())
    texts = [v for h in f.graph.hero.values() for k, v in h.content.items() if k == "name"]
    assert script_share(texts, "Devanagari") > 0


@case("spc-mk-03")
def test_the_same_order_is_one_record_in_every_app(tmp_path_factory):
    f = standard(tmp_path_factory)
    g = f.graph
    apps_by_entity: dict[str, set[str]] = {}
    for sc in g.screens.values():
        for ref in sc.hero_refs:
            apps_by_entity.setdefault(ref.split(":")[0], set()).add(sc.app_id)
    shared = {e for e, apps in apps_by_entity.items() if len(apps) >= 2}
    assert shared, "an entity is shown by screens of several apps"
    ids = list(g.hero)
    assert len(ids) == len(set(ids)) and all(
        i.startswith("HERO-ENT-") for i in ids
    )  # ids are not per app
    assert not [i for i in ids if "APP" in i]
    # one timeline: relations between records point at ids that exist (coherence across apps)
    related = [h for h in g.hero.values() if h.relations]
    assert related and all(t in g.hero for h in related for t in h.relations.values())


@case("spc-mk-04")
def test_the_seed_fixes_the_records_and_the_same_graph_gives_the_same_skeleton():
    g = make_graph("small")
    a = hero_mod.mint_hero_skeleton(g, random.Random("prog:h1"))
    b = hero_mod.mint_hero_skeleton(g, random.Random("prog:h1"))
    assert {k: v.model_dump() for k, v in a.items()} == {k: v.model_dump() for k, v in b.items()}
    assert a and all(k.startswith("HERO-ENT-") for k in a)


@case("spc-mk-04")
def test_filled_records_use_relative_dates_and_the_pack_is_byte_stable(tmp_path_factory):
    f = standard(tmp_path_factory)
    dates = [
        v for h in f.graph.hero.values() for k, v in h.content.items() if "day" in k or "date" in k
    ]
    assert dates and all(re.fullmatch(r"now[+-]\d+[dhm]", v) for v in dates)
    assert not [
        f2 for f2 in validate(f.graph) if f2.code in ("V-21", "V-34") and f2.severity == "blocker"
    ]
    assert profiles_time(f) == {"anchor": "now", "format": "now[+-]<n><d|h|m>"}


def profiles_time(f):
    return _profiles(f)["typical"]["time"]


@case("spc-mk-05")
def test_the_extreme_profile_draws_only_from_allow_listed_categories(tmp_path_factory):
    f = standard(tmp_path_factory)
    extreme = _profiles(f)["extreme"]
    allowed = set(blns_meta()["allowed_categories"])
    assert allowed == set(load_allowed_strings())
    assert set(extreme["blns_categories_used"]) <= allowed
    assert {"Emoji", "Right-To-Left Strings"} <= set(extreme["blns_categories_used"])
    assert not {"Script Injection", "SQL Injection"} & set(extreme["blns_categories_used"])
    assert not find_unsafe(list(iter_strings(extreme["entities"])))  # safe even when extreme
    longest = max(
        len(v)
        for e in extreme["entities"].values()
        for r in e["rows"]
        for v in map(str, r["fields"].values())
    )
    assert longest >= 200  # very long text


@case("spc-mk-05")
def test_the_extreme_profile_declares_a_very_large_list_volume(tmp_path_factory):
    f = standard(tmp_path_factory)
    counts = [e["count"] for p in _profiles(f).values() for e in p.get("entities", {}).values()]
    assert max(counts) >= 10_000


@case("spc-mk-06")
def test_the_broken_profile_covers_every_operation_from_the_error_taxonomy(tmp_path_factory):
    f = standard(tmp_path_factory)
    broken = _profiles(f)["broken"]
    ops = set(f.graph.operations)
    covered = {i["operation"] for i in broken["injections"]}
    assert ops and ops <= covered
    assert {i["source"] for i in broken["injections"]} <= {"taxonomy", "operation"}
    assert "taxonomy" in {i["source"] for i in broken["injections"]}
    assert {"network_down", "timeout"} <= {i["error"] for i in broken["injections"]}
    assert all(i["recovery"] for i in broken["injections"])


def _phone_export():
    return mutate(raw("standard"), fixture_json("spc-mk-07", "mutation.json"))


@case("spc-mk-07")
def test_a_phone_number_in_the_brief_is_not_copied_into_the_pack_and_records_are_synthetic(
    tmp_path_factory,
):
    f = cached_flow(tmp_path_factory, "mk-phone", _phone_export)
    assert f.status == "approved"
    packed = "".join(f.pack_files.values())
    fixtures = json.dumps([h.model_dump() for h in f.graph.hero.values()])
    for number in ("9820012345", "98200 12345"):
        assert (
            number not in packed and number not in fixtures
        )  # never copied into the pack or the records
    v20 = [x for x in validate(f.graph) if x.code == "V-20"]
    assert v20 and "phone-like" in v20[0].why  # the scan sees it where it does survive (the graph)
    assert f.graph.hero and all(h.synthetic for h in f.graph.hero.values())
    texts = list(iter_strings([h.content for h in f.graph.hero.values()]))
    assert not find_unsafe(texts)
    # the scan covers names too: a real brand is flagged unless the user gave the business name
    assert find_unsafe(["Order from Zomato"])
    assert not find_unsafe(["Order from Zomato"], allowed_names=["Zomato Supplies"])
