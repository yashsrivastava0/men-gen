"""Mock modes by code (doc 18 5; AD-87): profiles per detail profile, extreme from the allow-list, broken, offline, demo accounts."""

import random
import re

import pytest

from mde_spec_core.canon import canonical_json
from mde_spec_core.config import default_config
from mde_spec_core.graph import Action, FieldDef, Step
from mde_spec_core.hero import apply_fill, mint_hero_skeleton
from mde_spec_core.mock import (
    assert_fixture_safe,
    blns_sections,
    build_behaviours,
    build_demo_accounts,
    build_mock_bundle,
    build_mock_profiles,
    build_recipes,
    find_unsafe,
    iter_strings,
    load_allowed_strings,
    profile_names,
    script_share,
)
from mde_spec_core.synth import make_graph

CFG = default_config()
REL = re.compile(r"^now(?:[+-]\d+[dhm])?$")


def _graph(preset="medium", language=None):
    g = make_graph(preset)
    g.entities["ENT-001"].fields += [
        FieldDef(key="customer_name", type="text"),
        FieldDef(key="total", type="money"),
        FieldDef(key="placed_on", type="date"),
        FieldDef(key="due_date", type="date"),
    ]
    g.entities["ENT-002"].fields += [
        FieldDef(key="parent", type="ref", ref_entity="ENT-001"),
        FieldDef(key="amount", type="money"),
        FieldDef(key="note", type="text"),
    ]
    if language:
        g.meta.source_language = language
        g.meta.languages = [language, "en"]
    g.hero = mint_hero_skeleton(g, random.Random(1))
    return g


def _dump(x):
    return canonical_json(x)


# ------------------------------------------------------------------ which profiles ship


@pytest.mark.parametrize(
    ("preset", "detail"),
    [("small", "compact"), ("medium", "standard"), ("big", "deep")],
)
def test_the_profiles_shipped_per_detail_profile_come_from_config(preset, detail):
    g = _graph(preset)
    out = build_mock_profiles(g, detail, random.Random(1))
    assert list(out) == CFG.profiles.mock_profiles[detail]
    assert set(out) <= {"empty", "typical", "busy", "extreme", "broken", "offline"}
    assert ("busy" in out) == (detail != "compact")
    assert ("extreme" in out) == (detail == "deep")
    for p in out.values():
        assert p["synthetic"] is True and p["time"]["anchor"] == "now"


def test_extreme_can_be_added_on_request_to_a_standard_app():
    g = _graph("medium")
    assert profile_names("standard", ("extreme",))[-1] == "extreme"
    out = build_mock_profiles(g, "standard", random.Random(1), extra=("extreme",))
    assert "extreme" in out
    with pytest.raises(ValueError):
        profile_names("standard", ("nonsense",))


def test_same_seed_same_bytes_and_order_does_not_matter():
    g = _graph("medium")
    a = build_mock_profiles(g, "standard", random.Random(11))
    b = build_mock_profiles(g, "standard", random.Random(11))
    assert _dump(a) == _dump(b)
    assert _dump(a) != _dump(build_mock_profiles(g, "standard", random.Random(12)))
    # a profile is an independent draw: asking for another set of profiles leaves `busy` unchanged
    c = build_mock_profiles(g, "deep", random.Random(11))
    assert _dump(a["busy"]) == _dump(c["busy"])


# ------------------------------------------------------------------ empty, typical, busy


def test_empty_has_zero_rows_and_marks_first_run():
    g = _graph()
    p = build_mock_profiles(g, "standard", random.Random(1))["empty"]
    assert p["first_run"] is True
    assert all(e["count"] == 0 and e["rows"] == [] for e in p["entities"].values())


def test_typical_shows_every_state_and_keeps_relations_whole():
    g = _graph()
    p = build_mock_profiles(g, "standard", random.Random(1))["typical"]
    rows = {r["id"]: r for e in p["entities"].values() for r in e["rows"]}
    for eid, e in g.entities.items():
        states = {r["state"] for r in p["entities"][eid]["rows"]}
        assert set(e.lifecycle.states) <= states
        cap = CFG.profiles.hero_per_primary_entity["standard"][1]
        assert p["entities"][eid]["count"] <= max(cap, len(e.lifecycle.states))
    for r in rows.values():
        for target in r["relations"].values():
            assert target in rows
    assert p["made_by"] == "model_with_code_fill" and p["unfilled_records"] > 0


def test_typical_uses_the_hero_content_the_model_wrote():
    g = _graph()
    parent = next(r for r in g.hero.values() if r.entity == "ENT-001")
    apply_fill(g, {parent.id: {"content": {"customer_name": "Sunita Pawar"}}})
    p = build_mock_profiles(g, "standard", random.Random(1))["typical"]
    row = next(r for r in p["entities"]["ENT-001"]["rows"] if r["id"] == parent.id)
    assert row["fields"]["customer_name"] == "Sunita Pawar"
    assert row["fields"]["status"] == parent.state


def test_busy_is_long_varied_and_keeps_relations_and_states():
    g = _graph()
    p = build_mock_profiles(g, "standard", random.Random(3))["busy"]
    prim = [e for e in g.entities.values() if e.klass == "primary"]
    assert prim
    for e in prim:
        ent = p["entities"][e.id]
        assert ent["count"] >= 60
        assert set(e.lifecycle.states) <= {r["state"] for r in ent["rows"]}
        ids = [r["id"] for r in ent["rows"]]
        assert len(set(ids)) == len(ids)
    kids = p["entities"]["ENT-002"]["rows"]
    parent_ids = {r["id"] for r in p["entities"]["ENT-001"]["rows"]}
    assert all(r["relations"]["parent"] in parent_ids for r in kids)
    placed = {r["fields"]["placed_on"] for r in p["entities"]["ENT-001"]["rows"]}
    assert len(placed) > 20 and all(REL.match(v) for v in placed)  # dates varied, relative to now
    due = {r["fields"]["due_date"] for r in p["entities"]["ENT-001"]["rows"]}
    assert all(v.startswith("now+") for v in due)


def test_busy_derives_a_child_amount_from_its_parent_row():
    g = _graph()
    p = build_mock_profiles(g, "standard", random.Random(3))["busy"]
    parents = {r["id"]: r for r in p["entities"]["ENT-001"]["rows"]}
    for kid in p["entities"]["ENT-002"]["rows"]:
        parent = parents[kid["relations"]["parent"]]
        assert kid["fields"]["amount"] == parent["fields"]["total"]


def test_dates_are_relative_to_now_everywhere():
    g = _graph()
    out = build_mock_profiles(g, "deep", random.Random(3))
    for p in out.values():
        for ent in p.get("entities", {}).values():
            for r in ent.get("rows", []):
                for key in ("placed_on", "due_date"):
                    if key in r["fields"] and p["name"] != "extreme":
                        assert REL.match(r["fields"][key]), r["fields"][key]


# ------------------------------------------------------------------ extreme (FN-10)


def test_extreme_contains_only_allow_listed_categories_and_strings():
    g = _graph("big")
    p = build_mock_profiles(g, "deep", random.Random(3))["extreme"]
    allowed_cats = set(load_allowed_strings())
    assert set(p["blns_categories_used"]) <= allowed_cats
    allowed = {s for v in load_allowed_strings().values() for s in v}
    assert set(p["blns_strings_used"]) <= allowed
    assert len(p["blns_categories_used"]) >= 6
    forbidden = {
        e
        for s in blns_sections()
        if s.title not in allowed_cats
        for e in s.entries
        if e not in allowed
    }
    texts = set(iter_strings(p))
    assert not texts & forbidden


def test_extreme_has_long_text_emoji_rtl_zero_one_many_and_large_numbers():
    g = _graph("big")
    p = build_mock_profiles(g, "deep", random.Random(3))["extreme"]
    rows = [r for e in p["entities"].values() for r in e["rows"]]
    classes = {r["edge_class"] for r in rows}
    assert {"long_text", "emoji", "right_to_left", "large_number", "mixed_script"} <= classes
    texts = [v for r in rows for v in r["fields"].values()]
    assert any(len(t) >= 400 for t in texts)
    assert any(re.search(r"[֐-ࣿ]", t) for t in texts)  # right-to-left script present
    ent = p["entities"]["ENT-001"]
    assert ent["list_cases"]["zero"] == 0 and ent["list_cases"]["one"] == 1
    assert (
        ent["list_cases"]["many"]["count"] == 10000 and ent["list_cases"]["many"]["virtual"] is True
    )
    assert len(ent["rows"]) == 12  # the many case is a recipe, never written out
    assert ent["rows_written"] == 12 and ent["count"] == 10000  # the declared volume of the list
    recipes = {r["entity"] for r in build_recipes(g, random.Random(3))}
    assert {eid for eid, e in p["entities"].items() if e["count"] >= 10000} <= recipes
    big = [
        v
        for r in rows
        if r["edge_class"] == "large_number"
        for v in r["fields"].values()
        if v.startswith("₹")
    ]
    assert big and any(len(v) >= 12 for v in big)  # amounts in lakhs and crores, in Indian grouping
    assert any(re.fullmatch(r"₹\d{1,2}(,\d\d)+,\d{3}(\.\d\d)?", v) for v in big)


def test_extreme_passes_the_fixture_safety_rules():
    g = _graph("big")
    p = build_mock_profiles(g, "deep", random.Random(3))["extreme"]
    assert find_unsafe(list(iter_strings(p))) == []


def test_extreme_never_uses_an_injection_string():
    g = _graph("big")
    for seed in range(5):
        p = build_mock_profiles(g, "deep", random.Random(seed))["extreme"]
        for t in iter_strings(p):
            assert not re.search(
                r"<script|DROP TABLE|\x1b|/etc/passwd|\$\{|\{\{|<!ENTITY|\.\./", t
            ), t


# ------------------------------------------------------------------ broken, offline


def test_broken_injects_failures_for_every_operation_from_the_taxonomy():
    g = _graph()
    g.operations["OP-001"].errors[0].code = "stock_too_low"
    p = build_mock_profiles(g, "standard", random.Random(1))["broken"]
    ops = {i["operation"] for i in p["injections"]}
    assert ops == {o for o, op in g.operations.items() if op.status == "in"}
    codes = {i["error"] for i in p["injections"]}
    assert {"network_down", "timeout", "session_expired"} <= codes
    assert any(
        i["error"] == "stock_too_low" and i["source"] == "operation" for i in p["injections"]
    )
    writes = {o for o, op in g.operations.items() if op.method != "GET"}
    assert {i["operation"] for i in p["injections"] if i["error"] == "server_error"} == writes
    assert all(i["trigger"] for i in p["injections"]) and p["data_from"] == "typical"


def test_offline_serves_cached_typical_rows_and_queues_actions():
    g = _graph()
    scr = g.screens["SCR-001"]
    scr.actions.append(
        Action(
            key="save_offline",
            intent="confirm",
            what="save",
            flags=["offline"],
            effect=[Step(verb="call", target="OP-003")],
        )
    )
    out = build_mock_profiles(g, "standard", random.Random(1))
    off, typ = out["offline"], out["typical"]
    for eid, c in off["cache"].items():
        assert c["row_ids"] == [r["id"] for r in typ["entities"][eid]["rows"]]
    assert off["network"] == "down"
    assert {"screen": "SCR-001", "action": "save_offline", "operation": "OP-003"}.items() <= off[
        "queued_actions"
    ][0].items()
    assert all(q["operation"] in g.operations for q in off["queued_actions"])


def test_offline_falls_back_to_idempotent_writes_when_no_action_is_flagged():
    g = _graph()
    off = build_mock_profiles(g, "standard", random.Random(1))["offline"]
    assert off["queued_actions"] and all(
        g.operations[q["operation"]].method != "GET" for q in off["queued_actions"]
    )


# ------------------------------------------------------------------ demo accounts, recipes, behaviours


def test_demo_accounts_one_per_role_never_a_phone_number():
    g = _graph()
    profiles = build_mock_profiles(g, "standard", random.Random(1))
    accts = build_demo_accounts(g, profiles, random.Random(1))
    assert [a["role"] for a in accts] == sorted(g.roles)
    typical_ids = {r["id"] for e in profiles["typical"]["entities"].values() for r in e["rows"]}
    for a in accts:
        assert a["login"]["email"].endswith("@example.com")
        assert re.fullmatch(r"DEMO-\d+-\d{4}", a["login"]["demo_code"])
        assert not any(k for k in a if "phone" in k) and not any("phone" in k for k in a["login"])
        assert find_unsafe(list(iter_strings(a))) == []
        assert all(i in typical_ids for ids in a["records"].values() for i in ids)
        assert a["synthetic"] is True
    assert sum(a["reviewer_demo"] for a in accts) == len(g.apps)
    assert _dump(accts) == _dump(
        build_demo_accounts(g, profiles, random.Random(99))
    )  # stable per role


def test_recipes_are_seeded_by_program_and_entity_not_by_the_run():
    g = _graph()
    a = build_recipes(g, random.Random(1))
    b = build_recipes(g, random.Random(2))
    assert _dump(a) == _dump(b)
    assert {r["entity"] for r in a} >= {e.id for e in g.entities.values() if e.klass == "primary"}
    r1 = next(r for r in a if r["entity"] == "ENT-001")
    assert {"placed_on", "due_date"} <= set(r1["vary"]["dates"]) and "total" in r1["vary"][
        "numbers"
    ]
    assert r1["states"]["values"] == g.entities["ENT-001"].lifecycle.states
    g2 = _graph()
    g2.meta.program_id = "other"
    assert build_recipes(g2, random.Random(1))[0]["seed"] != a[0]["seed"]


def test_behaviours_cover_latency_paging_expiry_realtime_and_provider_callbacks():
    g = _graph()
    from mde_spec_core.graph import Region

    g.screens["SCR-001"].regions.append(
        Region(
            key="live_status",
            what="status",
            shape="value",
            bind=["ENT-001.status"],
            live="realtime",
        )
    )
    b = build_behaviours(g, random.Random(1))
    assert set(b["latency"]) == set(g.operations)
    assert all(v["min"] < v["max"] for v in b["latency"].values())
    assert b["paging"]["page_size"] == 20 and b["auth_expiry"]["after_ms"] > 0
    script = next(s for s in b["realtime_scripts"] if s["entity"] == "ENT-001")["script"]
    assert [s["to_state"] for s in script] == g.entities["ENT-001"].lifecycle.states[1:6]
    assert [s["after_ms"] for s in script] == sorted(s["after_ms"] for s in script)
    assert len(b["provider_callbacks"]) == sum(
        i.provider_side_flow for i in g.integrations.values()
    )
    assert {o["event"] for o in b["provider_callbacks"][0]["outcomes"]} == {"succeeded", "failed"}


# ------------------------------------------------------------------ locale realism and safety (FN-09, FN-11)


def test_people_and_places_follow_the_apps_script():
    for lang, script in (
        ("kn", "Kannada"),
        ("mr", "Devanagari"),
        ("hi", "Devanagari"),
        ("ar", "Arabic"),
    ):
        g = _graph("small", language=lang)
        g.entities["ENT-001"].fields.append(FieldDef(key="pickup_address", type="text"))
        g.hero = mint_hero_skeleton(g, random.Random(1))
        p = build_mock_profiles(g, "compact", random.Random(1))["typical"]
        texts = [
            r["fields"][k]
            for r in p["entities"]["ENT-001"]["rows"]
            for k in ("customer_name", "pickup_address")
            if k in r["fields"]
        ]
        assert texts
        assert script_share(texts, script) >= CFG.checks.locale_script_share_min, lang
        assert p["needs_native_review"] == (lang in ("kn", "ar"))


def _multi_language_graph(source="en", languages=("en", "mr", "hi")):
    g = _graph("small")
    g.meta.source_language = source
    g.meta.languages = list(languages)
    g.entities["ENT-001"].fields.append(FieldDef(key="pickup_address", type="text"))
    g.hero = mint_hero_skeleton(g, random.Random(1))
    for rec in g.hero.values():  # what a filler writes: English names
        rec.content["customer_name"] = "A. Customer"
        rec.content["pickup_address"] = "Some Road"
    return g


def test_an_app_with_several_languages_has_people_and_places_in_each_script():
    from mde_spec_core.hero import localise_hero
    from mde_spec_core.mock import mix_pools

    g = _multi_language_graph()
    assert [p.language for p in mix_pools(g)] == ["en", "mr", "hi"]
    assert localise_hero(g) > 0
    recs = sorted((r for r in g.hero.values() if r.entity == "ENT-001"), key=lambda r: r.id)
    names = [r.content["customer_name"] for r in recs]
    assert script_share(names[0::3], "Latin") == 1.0  # the source language keeps its records
    assert script_share(names[1::3], "Devanagari") >= CFG.checks.locale_script_share_min
    assert script_share(names[2::3], "Devanagari") >= CFG.checks.locale_script_share_min
    before = [r.content["customer_name"] for r in recs]
    assert localise_hero(g) == 0  # the same graph gives the same names: nothing more to write
    assert [r.content["customer_name"] for r in recs] == before


def test_the_profiles_of_a_multi_language_app_are_flagged_for_native_review():
    g = _multi_language_graph()
    out = build_mock_profiles(g, "standard", random.Random(4))
    assert all(
        p["needs_native_review"] and p["record_languages"] == ["en", "mr", "hi"]
        for p in out.values()
    )
    one = build_mock_profiles(
        _multi_language_graph("hi", ("hi", "en")), "standard", random.Random(4)
    )
    assert all(
        not p["needs_native_review"] and p["record_languages"] == ["hi"] for p in one.values()
    )


def test_a_language_with_no_pool_of_its_own_is_not_given_english_names():
    from mde_spec_core.mock import mix_pools

    g = _multi_language_graph("en", ("en", "ta"))
    assert [p.language for p in mix_pools(g)] == ["en"]


def test_money_uses_the_locale_format():
    g = _graph("small", language="en")
    p = build_mock_profiles(g, "compact", random.Random(1))["typical"]
    totals = [
        r["fields"]["total"] for r in p["entities"]["ENT-001"]["rows"] if "total" in r["fields"]
    ]
    assert totals and all(t.startswith("₹") for t in totals)


@pytest.mark.parametrize("preset", ["small", "medium", "big", "program"])
def test_the_whole_bundle_passes_the_safety_rules(preset):
    g = _graph(preset)
    detail = g.meta.profile
    bundle = build_mock_bundle(g, detail, random.Random(5), extra=("extreme",))
    assert set(bundle) == {"profiles", "demo_accounts", "recipes", "behaviours"}
    assert_fixture_safe(bundle)


def test_phone_and_email_fields_are_masked_and_at_example_com():
    g = _graph("small")
    g.entities["ENT-001"].fields += [
        FieldDef(key="contact_phone", type="phone"),
        FieldDef(key="contact_email", type="email"),
    ]
    g.hero = mint_hero_skeleton(g, random.Random(1))
    out = build_mock_profiles(g, "standard", random.Random(2), extra=("extreme",))
    for p in out.values():
        for ent in p.get("entities", {}).values():
            for r in ent.get("rows", []):
                if "contact_phone" in r["fields"]:
                    assert "•" in r["fields"]["contact_phone"]
                if "contact_email" in r["fields"]:
                    assert r["fields"]["contact_email"].endswith("@example.com")
    assert find_unsafe(list(iter_strings(out))) == []


def test_a_graph_without_a_skeleton_still_gets_profiles():
    g = make_graph("small")
    assert not g.hero
    out = build_mock_profiles(g, "compact", random.Random(1))
    assert out["typical"]["entities"]["ENT-001"]["count"] > 0
