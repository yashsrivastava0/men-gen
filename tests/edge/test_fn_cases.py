"""SPC-FN-01..18 (doc 18 section 7): portable-call and static-pack rows, part one. Pure callkit and static-pack cases run on
the real functions; the cases that need a model behaviour run the real Gateway over a ScriptedProvider (see eb_run)."""

from __future__ import annotations

import json
import random

import pytest
from edge_support import case
from edge_support.eb_run import (
    Rig,
    fixture_text,
    nodes_of,
    pack_of,
    wrap_gateway,
)

from mde_core.errors import ProviderError
from mde_spec_core.callkit import (
    FailedItem,
    ReaskLedger,
    TopLevelShape,
    plan_reasks,
    split_on_truncation,
    tolerant_parse,
    weak_route_banner,
)
from mde_spec_core.callkit.ladder import FixKind
from mde_spec_core.config import default_config
from mde_spec_core.synth import make_graph

SHAPE = TopLevelShape(required_keys=("note",), array_key="items")
MODULE = "spec.prod_module"
CFG = default_config()


def items_text(n: int) -> str:
    return json.dumps({"note": "x", "items": [{"id": i, "name": f"item {i}"} for i in range(n)]})


# --------------------------------------------------------------------------------------------------- FN-01
@case("spc-fn-01")
def test_fn_01_prose_reasoning_and_fences_are_stripped_and_the_first_fitting_object_wins():
    wrapped = fixture_text("spc-fn-01", "wrapped.txt")
    got = tolerant_parse(wrapped, SHAPE)
    assert got.ok and got.value["note"] == "real" and len(got.value["items"]) == 2
    assert set(got.stripped) >= {"think", "fence"}  # both kinds of noise were removed and reported
    # a decoy object that does not fit the shape comes first: it is skipped, the fitting one is taken
    assert tolerant_parse('see {"a": 1} then ' + items_text(1), SHAPE).value["items"][0]["id"] == 0
    # none fits: ok False with a plain reason (the caller makes its ONE re-ask, then R-flags)
    none = tolerant_parse("Sorry, I cannot do that. {not json}", SHAPE)
    assert not none.ok and none.reason and none.value is None


@case("spc-fn-01")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-FN-1: tolerant_parse is not wired into the run path: the gateway parses with a strict json.loads, so a "
    "valid object wrapped in prose or a fence costs a repair call instead of being stripped for free",
)
def test_fn_01_a_wrapped_valid_answer_needs_no_repair_call_on_the_run_path(tmp_path):
    from edge_support.eb_run import mock_answer

    rig = Rig(tmp_path, "standard")
    rig.provider.set_default(
        MODULE, lambda req: "Here you go:\n```json\n" + mock_answer(req).model_dump_json() + "\n```"
    )
    assert rig.go("plan", "product").status == "awaiting_g2"
    assert len(rig.provider_calls(MODULE)) == len(
        rig.calls(MODULE)
    )  # one call per batch, no repair


# --------------------------------------------------------------------------------------------------- FN-02
@case("spc-fn-02")
def test_fn_02_truncation_keeps_finished_items_and_redoes_only_the_rest_in_smaller_batches():
    text = items_text(6)[: items_text(6).index('{"id": 4')]  # cut inside the fifth item
    got = tolerant_parse(text, SHAPE)
    assert got.ok and got.truncated and got.salvaged_count == 4 and got.unfinished
    done = {i["id"] for i in got.value["items"]}
    batches = split_on_truncation(list(range(6)), done, 2)
    assert [x for b in batches for x in b] == [4, 5]  # only the unfinished items are redone
    assert split_on_truncation(list(range(10)), (), 2) == [list(range(5)), list(range(5, 10))]
    nothing = tolerant_parse('{"note": "x", "items": [{"id": 1, "na', SHAPE)
    assert not nothing.ok and nothing.truncated and nothing.salvaged_count == 0


@case("spc-fn-02")
def test_fn_02_a_fused_call_splits_back_into_its_parts_and_the_single_plan_call_ends_partial(
    tmp_path,
):
    ref = Rig(tmp_path, "quick", tag="ref")
    assert ref.go("plan", "product").status == "awaiting_g2"
    assert ref.ctx.plan["fused"] is True  # the compact app's plan call also wrote the product
    rig = Rig(tmp_path, "quick", tag="cut")
    gw = wrap_gateway(rig.ctx)
    gw.raise_on = lambda spec, inp, n: (
        ProviderError("output was truncated at the token limit")
        if spec == "spec.plan_fused"
        else None
    )
    assert rig.go("plan", "product").status == "awaiting_g2"
    assert rig.ctx.plan["fused"] is False
    assert gw.count("spec.plan_fused") == 1  # one attempt, then the parts
    assert (
        gw.count("spec.plan") == 1 and gw.count(MODULE) >= 1
    )  # the plan and the module call(s) ran alone
    # the single Plan call has no default: truncated alone it ends the run partial
    one = Rig(tmp_path, "standard", tag="plan")
    g1 = wrap_gateway(one.ctx)
    g1.raise_on = lambda spec, inp, n: (
        ProviderError("output was truncated at the token limit") if spec == "spec.plan" else None
    )
    out = one.go("plan")
    assert out.status == "partial" and out.reason == "plan_invalid"


# --------------------------------------------------------------------------------------------------- FN-03
@case("spc-fn-03")
def test_fn_03_only_the_failed_items_go_back_in_one_batch():
    failed = [
        FailedItem(
            "ENT-002", "PROD-MODULE", {"id": "ENT-002"}, "ref_entity ENT-999 does not exist"
        ),
        FailedItem("ENT-005", "PROD-MODULE", {"id": "ENT-005"}, "listed twice"),
        FailedItem("FLW-001", "PROD-JOURNEYS", {"id": "FLW-001"}, "unknown step"),
    ]
    plan = plan_reasks(failed, ReaskLedger(), CFG)
    assert plan.call_count == 2  # batched per call kind, not per item
    kinds = {b.call_kind: [i.node_id for i in b.items] for b in plan.batches}
    assert kinds == {"PROD-MODULE": ["ENT-002", "ENT-005"], "PROD-JOURNEYS": ["FLW-001"]}
    assert plan.flagged == ()  # the valid items are not in any batch: they are never asked again


@case("spc-fn-03")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-FN-2: CallRunner re-asks and replaces the WHOLE answer; when the re-ask fails the whole call takes the "
    "fallback, so the valid items of the first answer are thrown away instead of being kept",
)
def test_fn_03_the_valid_items_of_a_partly_invalid_answer_survive_in_the_graph(tmp_path):
    rig = Rig(tmp_path, "standard")
    gw = wrap_gateway(rig.ctx)

    def mixed(out):
        out = out.model_copy(deep=True)
        out.entities[0].name = "Model Chosen Name"  # a valid improvement
        out.stories[0].feature_ids = ["FTR-999"]  # one invalid item
        return out

    gw.rewrite = lambda spec, inp, n: (
        mixed if (spec == MODULE and inp.entity_ids and inp.story_ids) else None
    )
    assert rig.go("plan", "product").status == "awaiting_g2"
    assert any(e.name == "Model Chosen Name" for e in rig.ctx.graph.entities.values())


# --------------------------------------------------------------------------------------------------- FN-04
@case("spc-fn-04")
def test_fn_04_one_reask_per_node_in_the_whole_run_so_gate_3_cannot_ask_again():
    ledger = ReaskLedger()
    item = FailedItem("SCR-004", "SCRN", {"id": "SCR-004"}, "region key names a widget")
    first = plan_reasks([item], ledger, CFG)
    assert first.call_count == 1
    ledger.record(first)  # gate 1 spent it
    later = plan_reasks([item], ledger, CFG)  # gate 3 wants to ask the same node again
    assert later.call_count == 0 and later.flagged == (item,)  # refused by the same ledger: R-flag
    assert ReaskLedger.from_dict(ledger.to_dict()).has_used(
        "SCR-004"
    )  # and it survives a checkpoint


@case("spc-fn-04")
def test_fn_04_a_reask_that_fixes_one_item_and_breaks_another_is_not_accepted(tmp_path):
    rig = Rig(tmp_path, "standard")
    gw = wrap_gateway(rig.ctx)

    def first_wrong(out):
        out = out.model_copy(deep=True)
        out.stories[0].feature_ids = ["FTR-999"]
        return out

    def second_worse(out):
        out = out.model_copy(deep=True)
        out.stories[0].feature_ids = []  # the first error fixed ...
        out.entities[0].prov = []  # ... another one introduced
        return out

    target: dict[str, str] = {}

    def rewrite(spec, inp, n):
        if spec != MODULE or not (inp.story_ids and inp.entity_ids):
            return None
        if target.setdefault("m", inp.module_id) != inp.module_id:
            return None
        return first_wrong if not inp.reask else second_worse

    gw.rewrite = rewrite
    assert rig.go("plan", "product").status == "awaiting_g2"
    bad = [c for c in rig.calls(MODULE) if c.used_fallback]
    assert (
        len(bad) == 1 and bad[0].flagged and bad[0].reasked
    )  # not accepted: flagged, never a third ask
    assert all(v == 1 for v in rig.ctx.runner.ledger.used.values())
    assert all(
        e.provenance for e in rig.ctx.graph.entities.values()
    )  # the worse answer never landed


# --------------------------------------------------------------------------------------------------- FN-05
@case("spc-fn-05")
def test_fn_05_every_r_code_change_is_logged_counted_and_shown_on_the_g2_page():
    from mde_spec_agent.views import g2
    from mde_spec_core.rcode import run_rcode

    g = make_graph("small")
    screen = next(s for s in g.screens.values() if s.screen_type == "list")
    dropped = [s.kind for s in screen.states][:2]
    screen.states = [s for s in screen.states if s.kind not in dropped]
    log = run_rcode(g)
    assert len(log) >= len(dropped)
    counts = log.counts()
    assert (
        sum(counts.values()) == len(log) and counts[FixKind.INSTANTIATE_LIBRARY_DEFAULT.value] >= 2
    )
    assert {c.node_id for c in log.changes} >= {screen.id}
    assert all(r["before"] is not None and r["after"] and r["what"] for r in log.rows())
    view = g2(g, None, None, None, rcode_rows=log.rows())
    assert view["code_fixes_total"] == len(log)  # nothing silent: the page carries the total ...
    assert len(view["code_fixes"]) >= 1  # ... and the grouped list of what changed


# --------------------------------------------------------------------------------------------------- FN-06
@case("spc-fn-06")
def test_fn_06_the_weak_route_banner_appears_above_15_percent_of_nodes_and_never_stops_the_run():
    assert weak_route_banner(15, 100, "screens", CFG) is None  # exactly 15%: no banner
    b = weak_route_banner(16, 100, "screens", CFG)
    assert b is not None and b.code == "weak_route" and "16 of 100" in b.message
    assert weak_route_banner({"a", "b"}, 20, "x", CFG) is None  # distinct ids are counted: 2 of 20
    assert weak_route_banner({"a", "b", "c", "d"}, 20, "x", CFG) is not None


@case("spc-fn-06")
def test_fn_06_on_a_real_run_the_banner_reaches_the_report_and_the_run_still_ends_at_g2(tmp_path):
    rig = Rig(tmp_path, "quick")
    n_screens = {"v": 0}
    gw = wrap_gateway(rig.ctx)

    def bad_card(out):
        out = out.model_copy(deep=True)
        out.regions[0].key = "open_button"  # a widget word: fails the freedom check
        return out

    gw.rewrite = lambda spec, inp, n: bad_card if (spec == "spec.scrn" and not inp.reask) else None
    out = rig.full()
    assert out.status == "awaiting_g2"  # a banner, never a stop
    reasked = rig.ctx.runner.ledger.reasked_nodes
    n_screens["v"] = len(rig.ctx.graph.screens)
    assert len(reasked) / max(1, len(rig.ctx.graph.screens)) > 0.15
    banners = [b for b in rig.ctx.banners if b["code"] == "weak_route"]
    assert banners
    from mde_spec_agent.views import g2

    view = g2(rig.ctx.graph, rig.ctx.pack, rig.ctx.check, rig.ctx.derived, banners=rig.ctx.banners)
    assert any(b["code"] == "weak_route" for b in view["banners"])


# --------------------------------------------------------------------------------------------------- FN-07
@case("spc-fn-07")
def test_fn_07_a_number_not_in_its_quote_is_flagged_and_never_becomes_a_locked_fact():
    from mde_spec_core.graph import Prov, Rule
    from mde_spec_core.validators.base import run_all
    from mde_spec_core.validators.context import make_context

    g = make_graph("tiny")
    g.add(
        "rules",
        Rule(
            id="RUL-901",
            subject="cancellation window",
            statement="free cancellation up to 99 days before",
            value="99",
            unit="days",
            tier="locked",
            provenance=[Prov(kind="user", ref="FCT-001", quote_id="FCT-001")],
        ),
    )
    quotes = {"FCT-001": "you can cancel up to 14 days before"}
    found = run_all(make_context(g, quotes=quotes), gate=1, only={"V-45"})
    assert len(found) == 1 and found[0].where == "RUL-901" and "99" in found[0].why
    assert (
        found[0].severity == "warning"
    )  # reported at G2; the switch to downgrade is off until PS-7
    # a number the quote does state is clean
    ok = {"FCT-001": "free cancellation up to 99 days before"}
    assert run_all(make_context(g, quotes=ok), gate=1, only={"V-45"}) == []
    # once the switch is on the finding is a blocker (the claim is downgraded, never kept as Locked)
    cfg = CFG.model_copy(
        update={"checks": CFG.checks.model_copy(update={"v45_downgrade_enabled": True})}
    )
    hard = run_all(make_context(g, quotes=quotes, config=cfg), gate=1, only={"V-45"})
    assert [f.severity for f in hard] == ["blocker"]


@case("spc-fn-07")
def test_fn_07_a_module_answer_with_an_unsupported_number_is_rejected_by_the_lane_checks(tmp_path):
    rig = Rig(tmp_path, "standard")
    gw = wrap_gateway(rig.ctx)

    def invent(out):
        from mde_spec_agent.lanes.product.prod_module.schema import RuleRec

        out = out.model_copy(deep=True)
        out.rules.append(
            RuleRec(
                id="NEW-refund_window",
                subject="refund window",
                statement="refund within 4711 days",
                value="4711",
                unit="days",
                prov=["assumed:the model thought so"],
            )
        )
        return out

    gw.rewrite = lambda spec, inp, n: invent if (spec == MODULE and inp.entity_ids) else None
    assert rig.go("plan", "product").status == "awaiting_g2"
    assert "4711" not in rig.ctx.graph.canonical_text()
    assert not [r for r in rig.ctx.graph.rules.values() if r.tier == "locked" and r.value == "4711"]


# --------------------------------------------------------------------------------------------------- FN-08
@case("spc-fn-08")
def test_fn_08_a_refused_call_uses_the_library_default_and_degrades_the_node(tmp_path):
    from mde_llm_gateway.providers.scripted import ScriptedResponse

    rig = Rig(tmp_path, "standard")
    rig.provider.set_default("spec.dsgn", ScriptedResponse(output="", refusal="content filter"))
    assert rig.go("plan", "design").status == "awaiting_g2"
    assert rig.ctx.graph.directions and all(d.degraded for d in rig.ctx.graph.directions.values())
    assert any(f.get("call") == "spec.dsgn" for f in rig.ctx.flags)
    # no fallback route is configured in Spec (a route is a name; switching is configuration): the default stands
    assert all(c.used_fallback and c.flagged for c in rig.calls("spec.dsgn"))


# --------------------------------------------------------------------------------------------------- FN-09
@case("spc-fn-09")
def test_fn_09_a_provider_that_fell_back_to_english_fails_the_script_check_and_the_curated_pool_is_used(
    monkeypatch,
):
    from mde_spec_core import mock

    pool = mock.pool_for_language(
        "kn"
    )  # Faker has no Kannada provider: a curated file pool serves it
    assert pool.source == "file" and pool.script == "Kannada"
    assert (
        mock.script_share(pool.people + pool.places, "Kannada")
        >= CFG.checks.locale_script_share_min
    )
    english = [f"Asha Rao {i}" for i in range(20)]
    assert mock.script_share(english, "Kannada") == 0.0
    with pytest.raises(mock.LocaleFallbackError):
        mock.assert_script_share(english, "Kannada")
    # a pool whose provider silently answered in English is refused before any fixture is made
    monkeypatch.setattr(mock, "_faker_lists", lambda locale: (english, english))
    row = dict(mock.locale_table()["languages"]["kn"], source="faker", locale="kn_IN")
    monkeypatch.setattr(mock, "_lang_row", lambda language: row)
    with pytest.raises(mock.LocaleFallbackError):
        mock.pool_for_language.__wrapped__("kn")


# --------------------------------------------------------------------------------------------------- FN-10
@case("spc-fn-10")
def test_fn_10_only_allow_listed_categories_enter_extreme_and_a_heading_change_fails():
    from mde_spec_core import mock

    meta = mock.blns_meta()
    allowed = mock.load_allowed_strings()
    assert set(allowed) == set(meta["allowed_categories"])
    denied = {
        "Script Injection",
        "SQL Injection",
        "Trick Unicode",
        "Special Unicode Characters Union",
    }
    assert not denied & set(
        allowed
    )  # injection, exploit, crash and terminal-escape sections never enter
    assert all(allowed[c] for c in allowed)
    assert not any(
        mock._has_control_chars(s) or mock.looks_like_url(s) for v in allowed.values() for s in v
    )
    # a changed heading is detected: parsing text whose heading was renamed no longer matches the pinned list
    raw = (mock.BLNS_DIR / meta["file"]).read_text(encoding="utf-8")
    renamed = raw.replace("#\tScript Injection", "#\tScript Injections", 1)
    titles = [s.title for s in mock.parse_blns(renamed)]
    assert titles != list(meta["headings"]) and "Script Injections" in titles
    assert [s.title for s in mock.parse_blns(raw)] == list(meta["headings"])


# --------------------------------------------------------------------------------------------------- FN-11
@case("spc-fn-11")
def test_fn_11_ids_and_phone_numbers_are_only_ever_masked():
    import phonenumbers
    from stdnum import verhoeff

    from mde_spec_core import mock

    base = "2341" + "2341" + "123"
    aadhaar = base + verhoeff.calc_check_digit(
        base
    )  # a value that passes validate()  # secret-scan: allow
    phone = phonenumbers.format_number(
        phonenumbers.example_number("IN"), phonenumbers.PhoneNumberFormat.INTERNATIONAL
    )
    cases = {
        "id_validates": f"customer id {aadhaar}",
        "phone_unmasked": f"call {phone}",
        "digit_run": "reference 123456789012345",
        "plate_like": "van MH 15 AB 4821",
        "upi_like_id": "pay to rahul@okbank",
        "email_outside_example": "write to ravi@gmail.com",
    }
    for kind, text in cases.items():
        assert kind in {v.kind for v in mock.find_unsafe([text])}, kind
    # masked forms and the demo-account forms pass
    safe = [
        f"id {mock.mask_id(aadhaar)}",
        f"call {mock.mask_phone(phone)}",
        "demo.owner@example.com",
    ]
    assert mock.find_unsafe(safe) == []
    assert "•" in mock.mask_phone(phone) and aadhaar not in mock.mask_phone(phone)
    assert mock.mask_id(aadhaar).endswith(aadhaar[-4:]) and aadhaar not in mock.mask_id(aadhaar)
    # the violation samples are themselves masked: a failing fixture never prints the value
    for v in mock.find_unsafe([cases["id_validates"]]):
        assert aadhaar not in v.sample and aadhaar not in v.detail
    # demo accounts use a demo code or an example.com email and never a phone number
    g = make_graph("small")
    accounts = mock.build_demo_accounts(g, {"typical": {"entities": {}}}, random.Random(1))
    assert accounts and all(a["login"]["email"].endswith("@example.com") for a in accounts)
    assert all("demo_code" in a["login"] and "phone" not in a["login"] for a in accounts)


# --------------------------------------------------------------------------------------------------- FN-12
@case("spc-fn-12")
def test_fn_12_a_real_brand_in_a_hero_record_fails_unless_the_user_gave_the_name():
    from mde_spec_core import mock
    from mde_spec_core.graph import HeroRecord
    from mde_spec_core.validators.base import run_all
    from mde_spec_core.validators.context import make_context

    text = "Order from Zomato at the kitchen"
    kinds = [v for v in mock.find_unsafe([text, "A Swiggy rider"]) if v.kind == "real_brand"]
    assert {v.sample for v in kinds} == {"Zomato", "Swiggy"}
    assert not mock.find_unsafe(
        [text], allowed_names=["Zomato Express"]
    )  # the user's own business name
    with pytest.raises(mock.FixtureSafetyError):
        mock.assert_fixture_safe([text])
    # hero records are synthetic by marker, and a record that loses the marker is a V-15 blocker
    g = make_graph("small")
    eid = sorted(g.entities)[0]
    g.hero["HRO-001"] = HeroRecord(id="HRO-001", entity=eid, content={}, synthetic=False)
    f = run_all(make_context(g), gate=3, only={"V-15"})
    assert [x.why for x in f] == ["fixture not marked synthetic"]
    # the asset brief's avoid-list carries the brand/real-person rule by default
    from mde_spec_core.assets import asset_rules

    assert asset_rules()["avoid_default"]


# --------------------------------------------------------------------------------------------------- FN-13
@case("spc-fn-13")
def test_fn_13_hero_image_requests_are_briefs_never_urls():
    from mde_spec_core.assets import lint_brief
    from mde_spec_core.graph import HeroRecord
    from mde_spec_core.validators.base import run_all
    from mde_spec_core.validators.context import make_context

    g = make_graph("small")
    eid = sorted(g.entities)[0]
    g.hero["HRO-001"] = HeroRecord(
        id="HRO-001", entity=eid, image_brief="https://images.example/photo.jpg"
    )
    g.hero["HRO-002"] = HeroRecord(
        id="HRO-002", entity=eid, image_brief="a bowl of lentil soup; count: 1; avoid: faces, logos"
    )
    f = run_all(make_context(g), gate=3, only={"V-15"})
    assert [(x.where, x.why) for x in f] == [("HRO-001", "image brief holds a URL")]
    assert lint_brief("a bowl of soup; count: 1; avoid: faces") == []
    assert lint_brief(
        "soft light, centred, 400x400 crop"
    )  # lighting, placement and size words are refused


# --------------------------------------------------------------------------------------------------- FN-14
@case("spc-fn-14")
def test_fn_14_numbers_and_money_follow_the_locale_with_lakh_grouping():
    from mde_spec_core import mock

    en = mock.pool_for_language("en")
    assert mock.format_number(1234567, en) == "12,34,567"  # lakh grouping, not 1,234,567
    assert "12,34,567" in mock.format_money(1234567, en)
    assert mock.format_money(1234567, en).startswith("₹")
    ar = mock.pool_for_language("ar")
    assert mock.format_number(1234567, ar) != mock.format_number(
        1234567, en
    )  # another locale, another form
    assert "12,34,567" not in mock.format_number(1234567, ar)


# --------------------------------------------------------------------------------------------------- FN-15
@case("spc-fn-15")
def test_fn_15_a_screen_built_before_its_target_carries_the_targets_route_in_its_closure():
    g = make_graph("small")
    p = pack_of(g)
    src = next(
        s
        for s in g.screens.values()
        if s.status == "in" and s.leads_to and s.id in p.index["screens"]
    )
    target_id = sorted(src.leads_to)[0]
    tgt = g.screens[target_id]
    text = p.files[f"screens/{src.id}.md"]
    line = next(x for x in text.split("\n") if x.startswith(f"route {target_id}:"))
    assert tgt.route.path in line and (tgt.route.name or tgt.id.lower()) in line
    for prm in tgt.route.params:
        assert f"{prm.name}: {prm.type}" in line  # typed parameters travel with the card
    # building B (the target) first or last changes nothing: the card never mentions an order
    assert (
        "build order" not in text.lower()
        and "build first" not in text.lower()
        and "after " + target_id not in text
    )
    assert p.files[f"screens/{src.id}.md"] == pack_of(g).files[f"screens/{src.id}.md"]


# --------------------------------------------------------------------------------------------------- FN-16
@case("spc-fn-16")
def test_fn_16_every_slice_carries_the_block_body_and_a_task_bundle_lists_it_once():
    from mde_spec_core.render import TokenCounter

    g = make_graph("small")
    first = next(
        t for t in pack_of(g).tasks if t["wave"] == "W3" and len(t["reads"]["screens"]) >= 3
    )
    ids = [x[8:-3] for x in first["reads"]["screens"]][:3]
    for (
        sid
    ) in ids:  # three screens of one task show the same data to the same roles: one shared block
        s = g.screens[sid]
        s.role_ids = list(g.screens[ids[0]].role_ids)
        r = s.regions[0]
        r.bind, r.shape, r.shape_kind = ["ENT-001.order_f1"], "collection", ""
    p = pack_of(g)
    assert list(p.blocks) == ["BLK-S001"]
    body = p.blocks["BLK-S001"]
    for sid in ids:
        assert (
            f"block BLK-S001@full: {body}" in p.files[f"screens/{sid}.md"]
        )  # the BODY is in every slice
    assert [x for x in p.files["global/blocks.md"].split("\n") if x.startswith("BLK-S001:")] == [
        f"BLK-S001: {body}"
    ]  # one definition, one owner
    counter = TokenCounter(p.manifest["tokenizer_chars_per_token"])
    seen = False
    for t in p.tasks:
        users = [x for x in t["reads"]["screens"] if x[8:-3] in ids]
        if len(users) >= 2:
            seen = True
            total = sum(p.index["screens"][x[8:-3]]["tokens"] for x in t["reads"]["screens"])
            assert t["read_tokens"]["slices"] == total - (len(users) - 1) * counter.count(body)
    assert seen  # the bundle lists the body once per task, whatever number of screens use it


# --------------------------------------------------------------------------------------------------- FN-17
@case("spc-fn-17")
def test_fn_17_the_weakest_consumer_never_gets_the_one_file_form():
    assert (
        pack_of(make_graph("tiny")).manifest["pack_form"] == "one_file_and_tree"
    )  # fits the weak window
    for name in ("small", "big", "program"):
        weak = pack_of(make_graph(name))
        assert weak.manifest["pack_form"] == "tree"  # too big for the weak window: the tree only
        assert weak.budget_report["total_tokens"] > weak.manifest["whole_pack_tokens"]
    agents = pack_of(make_graph("big")).files["AGENTS.md"]
    assert "all at once (only if your window reads about 80k tokens or more)" in agents
    assert "one by one" in agents and "in parallel" in agents  # AGENTS.md says which form to use
    assert "prefix.bundle.md" in agents
    assert CFG.budgets.one_file_min_consumer_window == 80_000


# --------------------------------------------------------------------------------------------------- FN-18
@case("spc-fn-18")
def test_fn_18_a_front_end_model_change_re_packages_only_and_the_content_hash_stays():
    g = make_graph("big")
    a = pack_of(g, consumer_id="weak-default")
    b = pack_of(
        g, consumer_id="large-context-default", task_read_budget=60_000, chars_per_token=2.0
    )
    assert (
        a.content_hash == b.content_hash
        and a.manifest["content_hash"] == b.manifest["content_hash"]
    )
    assert a.package_hash != b.package_hash
    changed = {p for p in a.files if a.files[p] != b.files.get(p)}
    assert all(
        p in {"MANIFEST.json", "index.json", "build/build-plan.json"} or p.startswith("contract/")
        for p in changed
    ), sorted(changed)[:5]  # screen cards, glossary, tokens, rules are byte-identical
    assert nodes_of(g) == nodes_of(g)
