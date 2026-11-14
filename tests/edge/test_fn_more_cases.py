"""SPC-FN-19..36 (doc 18 section 7), part two: run-time situations (single flight, provider dialects, credits, tokenizers,
brand colour, the G2 summary, integrations, hero records, copy keys, route changes, the plan, unspecified actions)."""

from __future__ import annotations

import random

import pytest
from edge_support import case
from edge_support.eb_run import (
    Rig,
    findings_of,
    pack_of,
    wrap_gateway,
)

from mde_llm_gateway.providers.scripted import ScriptedResponse
from mde_spec_core.config import default_config
from mde_spec_core.synth import make_graph

CFG = default_config()
MODULE = "spec.prod_module"


# --------------------------------------------------------------------------------------------------- FN-19
@case("spc-fn-19")
def test_fn_19_v27_stops_a_missing_fact_before_the_front_end_and_the_pack_tells_it_to_raise_a_decision():
    g = make_graph("small")
    p = pack_of(g)
    assert not findings_of(g, p, only={"V-27"})
    sid = next(s.id for s in g.screens.values() if s.status == "in" and s.regions)
    ent = next(
        b.split(".")[0]
        for r in g.screens[sid].regions
        for b in r.bind
        if b.split(".")[0] in g.entities
    )
    del g.entities[ent]  # a fact the slice needs is gone
    f = findings_of(g, None, only={"V-27"})
    assert any(x.code == "V-27" and x.severity == "blocker" and x.where == sid for x in f)
    agents = p.files["AGENTS.md"]
    assert "raise_decision" in agents  # the worker raises a decision instead of guessing
    assert CFG.scope.lookups_before_raise == 3  # and may look up at most three times first


@case("spc-fn-19")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-FN-3: nothing in Spec records a missing-fact report as a pack defect or turns it into a regression "
    "fixture (neither AGENTS.md nor the build plan mention a pack defect)",
)
def test_fn_19_a_missing_fact_found_by_a_worker_is_logged_as_a_pack_defect():
    p = pack_of(make_graph("small"))
    assert (
        "pack defect" in p.files["AGENTS.md"].lower()
        or "pack_defect" in p.files["build/build-plan.json"]
    )


# --------------------------------------------------------------------------------------------------- FN-20
@case("spc-fn-20")
def test_fn_20_one_active_run_per_program_and_the_same_input_joins_it(tmp_path):
    from mde_spec_agent.run.store import RunActive, RunStore

    store = RunStore(tmp_path / "rt")
    a = store.create_run("prog-x", "hash-1", {})
    b = store.create_run("prog-x", "hash-1", {})  # a double click: the same input hash
    assert (b.run_id, b.joined) == (a.run_id, True)
    with pytest.raises(RunActive) as exc:  # a different input is refused while one is active
        store.create_run("prog-x", "hash-2", {})
    assert exc.value.run_id == a.run_id
    assert (
        store.create_run("prog-y", "hash-1", {}).joined is False
    )  # another program is independent
    store.update_run(a.run_id, status="approved")  # finished: the marker is released
    assert store.create_run("prog-x", "hash-2", {}).joined is False
    assert len(store.list_runs()) == 3


# --------------------------------------------------------------------------------------------------- FN-21
@case("spc-fn-21")
def test_fn_21_a_prompt_carries_the_schema_in_text_and_the_word_json_for_json_mode_providers(
    tmp_path,
):
    rig = Rig(tmp_path, "standard")
    assert rig.full().status == "awaiting_g2"
    seen: dict[str, object] = {}
    for req in rig.provider.calls:
        seen.setdefault(req.spec_id, req)
    assert len(seen) >= 10
    for spec_id, req in seen.items():
        text = req.system + "\n" + req.user
        assert "SCHEMA" in req.system, spec_id  # the schema is written out in the prompt itself
        assert "JSON" in text, spec_id  # JSON mode needs the word
        assert req.user.strip().endswith("no other text") or "JSON" in req.system, spec_id
    # local validation is the contract: a schema-violating answer is not accepted just because the provider returned it
    rig2 = Rig(tmp_path, "standard", tag="bad")
    rig2.provider.set_default(MODULE, '{"entities": "not a list"}')
    assert rig2.go("plan", "product").status == "awaiting_g2"
    assert all(c.used_fallback and c.flagged for c in rig2.calls(MODULE))


# --------------------------------------------------------------------------------------------------- FN-22
@case("spc-fn-22")
def test_fn_22_schemas_are_flat_and_the_fusion_rule_consults_the_profile_limits():
    from mde_spec_agent.calls.registry import all_calls
    from mde_spec_core.callkit.fusion import FusionJob, can_fuse
    from mde_spec_core.callkit.schema_check import analyse_model
    from mde_spec_core.profiles import SchemaLimits, load_profiles
    from mde_verify_agent.calls.registry import ALL_SPECS

    for c in [*all_calls(), *ALL_SPECS]:
        metrics, issues = analyse_model(c.spec.output_model)
        assert not [i for i in issues if i.is_error], (c.id, issues[:2])  # flat closed records
        assert metrics.nullable_fields <= 16 and metrics.depth <= 4, c.id
    from mde_spec_agent.lanes.product.plan.schema import PlanOut
    from mde_spec_agent.lanes.product.prod_module.schema import ModuleOut

    weak = load_profiles().get("weak-default")
    jobs = [FusionJob("plan", 1000, 1000, PlanOut), FusionJob("module", 1000, 1000, ModuleOut)]
    assert can_fuse(jobs, weak, CFG).ok
    tight = weak.model_copy(update={"schema_limits": SchemaLimits(max_depth=1)})
    d = can_fuse(jobs, tight, CFG)
    assert not d.ok and any(
        r.startswith("schema:") for r in d.reasons
    )  # the limits decide the fusion


# --------------------------------------------------------------------------------------------------- FN-23
@case("spc-fn-23")
def test_fn_23_the_provider_switch_proof_uses_a_small_sequential_fixture_set_never_throughput(
    tmp_path,
):
    import inspect
    import json
    from pathlib import Path

    from evals.spec import common, packs

    src = inspect.getsource(packs) + inspect.getsource(common)
    assert (
        "Limiter" not in src and "asyncio.gather" not in src
    )  # no parallel width: nothing measures throughput
    cat = json.loads(
        (Path(common.__file__).parent / "data" / "synthetic-catalogue.json").read_text("utf-8")
    )
    assert len(cat) <= 40  # a handful of fixtures, not a corpus
    runner = common.make_proof_runner(tmp_path / "proof")
    assert (
        type(runner).__name__ == "CallRunner"
    )  # the plain runner behind the same spend guard as a run


# --------------------------------------------------------------------------------------------------- FN-24
@case("spc-fn-24")
def test_fn_24_no_runtime_flag_exists_that_sends_a_real_brief_to_a_free_route():
    from mde_core.settings import Settings
    from mde_llm_gateway.pricing import Pricing
    from mde_spec_agent.config import SpecSettings

    for model in (Settings, SpecSettings):
        assert not [f for f in model.model_fields if "free" in f.lower()], model.__name__
    pricing = Pricing.load()
    rows = getattr(pricing, "_rows", None) or getattr(pricing, "models", None) or {}
    assert "free" not in " ".join(str(k).lower() for k in rows)
    # the only mode setting is llm_mode; a free route is a proof-config matter handled outside the runtime
    assert "llm_mode" in Settings.model_fields


# --------------------------------------------------------------------------------------------------- FN-25
@case("spc-fn-25")
def test_fn_25_when_the_money_runs_out_mid_run_the_run_ends_partial_and_resumes_to_the_same_bytes(
    tmp_path,
):
    ref = Rig(tmp_path, "standard", tag="ref")
    assert ref.go("plan", "product").status == "awaiting_g2"
    rig = Rig(tmp_path, "standard", tag="cut")
    gw = wrap_gateway(rig.ctx)
    state = {"n": 0}

    def burn(spec, inp, n):  # after the third module call the whole cap is spent
        if spec == MODULE:
            state["n"] += 1
            if state["n"] == 3:
                res = rig.record.reserve(rig.record.remaining_micros(), label="burn")
                rig.record.commit(res, res.micros)
        return None

    gw.raise_on = burn
    out = rig.go("plan", "product")
    assert out.status == "partial" and out.reason == "budget_cap"
    assert rig.store.get_run(rig.ctx.run_id)["partial_reason"] == "budget_cap"
    done = len(
        rig.store.run_dir(rig.ctx.run_id).glob("checkpoints/*.json").__class__
        and list(rig.store.run_dir(rig.ctx.run_id).glob("checkpoints/*.json"))
    )
    assert done >= 1  # what finished was checkpointed
    rig.record.restart("owner top-up")  # only the owner restarts a period
    gw.raise_on = None
    assert rig.resume("plan", "product").status == "awaiting_g2"
    assert rig.ctx.graph.canonical_text() == ref.ctx.graph.canonical_text()


# --------------------------------------------------------------------------------------------------- FN-26
@case("spc-fn-26")
def test_fn_26_an_over_cap_slice_is_reported_never_hidden_and_budgets_follow_the_profile_counter():
    from mde_spec_core.pack import PackBreach, assemble

    g = make_graph("small")
    # so large that moving its bulky text to a detail file cannot make it fit either (a card that merely runs long is
    # repaired by the detail-file move, test_fn_26_bulky_detail_moves_...): only this case is a loud failure
    g.screens["SCR-001"].purpose = "word " * 90000
    with pytest.raises(PackBreach) as e:
        assemble(g)
    err = e.value.error
    assert err["code"] in {"CARD_OVER_HARD_CAP", "SLICE_OVER_CAP", "DETAIL_OVER_BUDGET"}
    assert err["severity"] == "blocker"
    assert (
        err["where"] == "SCR-001" and err["why"] and err["fix"]
    )  # reported with where, why and how to fix
    # budgets are counted with the consumer's tokenizer factor, content is not
    g2 = make_graph("small")
    a, b = pack_of(g2), pack_of(g2, chars_per_token=2.0)
    assert b.budget_report["total_tokens"] > a.budget_report["total_tokens"]
    for path in a.files:
        if path.startswith(("screens/", "global/")) and path != "global/needs.json":
            assert a.files[path] == b.files[path]


@case("spc-fn-26")
def test_fn_26_bulky_detail_moves_to_a_detail_file_instead_of_failing_the_build():
    from mde_spec_core.pack import assemble

    g = make_graph("small")
    g.screens["SCR-001"].purpose = "word " * 9000
    p = assemble(g)
    assert "screens/SCR-001.detail.md" in p.files


# --------------------------------------------------------------------------------------------------- FN-27
@case("spc-fn-27")
def test_fn_27_a_brand_colour_that_fails_white_text_stays_and_the_text_on_it_is_derived_and_said():
    from mde_spec_agent.lanes.design.build import token_flags
    from mde_spec_core.tokens import build_tokens, contrast_ratio

    d = {"id": "DIR-S001", "seed_hue": 40}
    yellow = "#ffd400"
    assert contrast_ratio("#ffffff", yellow) < 4.5  # white text on it fails
    t = build_tokens(d, yellow)
    on = t["report"]["derived_text_on_brand"]
    assert on["changed_from_white"] and on["ratio"] >= CFG.checks.wcag_body_text_ratio
    flat = t["tree"]["color"]["scheme"]["light"]
    assert flat["brand"]["$value"].lower() == yellow  # the colour is kept exactly as given
    assert flat["on_brand"]["$value"].lower() != "#ffffff"
    assert any(
        "stays exactly as you gave it" in s for s in t["report"]["statements"]
    )  # said plainly
    from types import SimpleNamespace

    flags = token_flags([SimpleNamespace(id="DIR-S001")], {"DIR-S001": t}, CFG, "standard")
    assert "brand_colour_text_derived" in [f["kind"] for f in flags]
    ok = build_tokens(d, "#0b3d91")  # a dark brand colour keeps white text
    assert ok["report"]["derived_text_on_brand"]["changed_from_white"] is False


# --------------------------------------------------------------------------------------------------- FN-28
@case("spc-fn-28")
def test_fn_28_without_a_brand_colour_the_seed_comes_from_the_direction_and_the_user_can_change_it():
    from mde_spec_core.tokens import build_tokens, resolve_seed

    d1 = {"id": "DIR-S001", "name": "Calm"}
    d2 = {"id": "DIR-S002", "name": "Bold"}
    s1, s2 = resolve_seed(d1), resolve_seed(d2)
    assert s1.source == s2.source == "derived" and s1.hex != s2.hex  # seeded from the direction
    assert resolve_seed(d1) == s1  # and stable
    t = build_tokens(d1)
    assert any("you can change it at Gate 2" in s for s in t["report"]["statements"])
    assert resolve_seed({**d1, "seed_hue": 200}).source == "direction_hue"
    assert resolve_seed(d1, "#336699").source == "brand"  # the user's choice wins when given
    assert t["report"]["brand_locked"] is False and t["locked_roles"] == []


# --------------------------------------------------------------------------------------------------- FN-29
@case("spc-fn-29")
def test_fn_29_when_the_g2_summary_call_fails_a_template_summary_stands_and_no_paragraph_is_invented(
    tmp_path,
):
    from mde_spec_agent.views import g2

    rig = Rig(tmp_path, "quick")
    rig.provider.set_default("spec.g2_summary", ScriptedResponse(output="", refusal="no"))
    assert rig.full().status == "awaiting_g2"
    summary = rig.ctx.plan["g2_summary"]
    assert summary["paragraph"] is None and summary["paragraph_source"] == "omitted"
    view = g2(rig.ctx.graph, rig.ctx.pack, rig.ctx.check, rig.ctx.derived, summary=summary)
    assert view["summary"]["source"] == "template" and view["summary"]["text"].strip()
    # nothing in the template is invented: every number it states is a count of the graph
    import re

    nums = {int(x) for x in re.findall(r"\d+", view["summary"]["text"])}
    live = [s for s in rig.ctx.graph.screens.values() if s.status == "in"]
    assert len(live) in nums or len(rig.ctx.graph.apps) in nums
    assert view["content_hash"] == rig.ctx.pack.content_hash  # and the summary never moves the hash


# --------------------------------------------------------------------------------------------------- FN-30
@case("spc-fn-30")
def test_fn_30_a_small_pack_fits_one_file_for_the_weak_consumer_and_the_tree_is_always_there():
    g = make_graph("tiny")
    p = pack_of(g)
    assert p.manifest["pack_form"] == "one_file_and_tree"
    assert "prefix.bundle.md" in p.files and "INDEX.md" in p.files  # the tree is always produced
    assert p.budget_report["total_tokens"] <= p.manifest["whole_pack_tokens"]


@case("spc-fn-30")
def test_fn_30_a_strong_consumer_gets_the_one_file_form_of_a_pack_the_weak_one_cannot_hold():
    g = make_graph("small")
    weak = pack_of(g)
    strong = pack_of(g, consumer_id="large-context-default", task_read_budget=400_000)
    assert weak.manifest["pack_form"] == "tree"
    assert strong.manifest["pack_form"] == "one_file_and_tree"
    assert strong.content_hash == weak.content_hash


# --------------------------------------------------------------------------------------------------- FN-31
@case("spc-fn-31")
def test_fn_31_an_integration_with_no_interrupted_flow_gets_the_library_template_by_code_without_a_reask(
    tmp_path,
):
    rig = Rig(tmp_path, "standard")
    gw = wrap_gateway(rig.ctx)

    def strip(out):
        out = out.model_copy(deep=True)
        for r in out.integrations:
            r.interruption, r.pending_timeout = "", ""
        return out

    gw.rewrite = lambda spec, inp, n: strip if spec == "spec.prod_integ" else None
    assert rig.go("plan", "product").status == "awaiting_g2"
    # D-189: the repair fills the library's interrupted-flow rule and pending timeout BY CODE (logged as R-code), so
    # the call is NOT re-asked any more; the doc's "one re-ask then a library template" became "a library template".
    assert gw.count("spec.prod_integ") == len(rig.calls("spec.prod_integ"))
    flows = [i for i in rig.ctx.graph.integrations.values() if i.provider_side_flow]
    assert flows and all(i.interruption.strip() and i.pending_timeout.strip() for i in flows)
    assert rig.ctx.rcode.rows  # and the repair is on the record
    assert [x for x in findings_of(rig.ctx.graph, only={"V-35"}) if x.severity == "blocker"] == []


# --------------------------------------------------------------------------------------------------- FN-32
@case("spc-fn-32")
def test_fn_32_ids_counts_and_relations_come_from_the_skeleton_and_shared_values_from_the_parent():
    from mde_spec_core import hero
    from mde_spec_core.graph import FieldDef

    g = make_graph(
        "small"
    )  # a parent and a child that would both show an amount and a person's name
    g.entities["ENT-001"].fields += [
        FieldDef(key="total", type="money"),
        FieldDef(key="customer_name", type="text"),
    ]
    g.entities["ENT-002"].fields += [
        FieldDef(key="parent", type="ref", ref_entity="ENT-001"),
        FieldDef(key="amount", type="money"),
        FieldDef(key="name", type="text"),
    ]
    a = hero.mint_hero_skeleton(g, random.Random("same"))
    b = hero.mint_hero_skeleton(g, random.Random("same"))
    assert {k: v.model_dump() for k, v in a.items()} == {k: v.model_dump() for k, v in b.items()}
    g.hero = a  # ids, counts and relations: all minted by code before any model call
    assert all(not v or v in g.hero for rec in g.hero.values() for v in rec.relations.values())
    plan = {(sv.entity, sv.field): sv for sv in hero.shared_value_plan(g)}
    assert {("ENT-002", "amount"), ("ENT-002", "name")} <= set(plan)
    child = next(r for r in g.hero.values() if r.entity == "ENT-002" and r.relations.get("parent"))
    parent = g.hero[child.relations["parent"]]
    parent.content["total"] = "Rs 1,20,000"
    child.content["amount"] = "A WRONG VALUE THE MODEL INVENTED"
    derived = hero.derive_shared_values(g)
    assert child.content["amount"] == "Rs 1,20,000"  # derived by code, never kept twice
    assert any(d["record"] == child.id and d["from"] == parent.id for d in derived)
    where = {e: gr.id for gr in hero.fill_plan(g) for e in gr.entities}
    assert where["ENT-001"] == where["ENT-002"]  # related entities are filled in ONE call


# --------------------------------------------------------------------------------------------------- FN-33
@case("spc-fn-33")
def test_fn_33_copy_keys_are_namespaced_by_screen_by_code_and_a_failed_glossary_degrades_the_locales(
    tmp_path,
):
    rig = Rig(tmp_path, "standard", tag="ok")
    assert rig.full().status == "awaiting_g2"
    g = rig.ctx.graph
    src = g.locale_copy[g.meta.source_language]
    for s in g.screens.values():
        prefix = s.id.lower().replace("-", "_") + "."
        assert all(k.startswith((prefix, "lib.")) for k in s.copy_keys), s.id
    own = [k for s in g.screens.values() for k in s.copy_keys if not k.startswith("lib.")]
    assert own and len(set(own)) == len(own)  # no two screens mint the same key
    assert src
    assert len(g.meta.languages) > 1
    reqs = [r for r in rig.provider.calls if r.spec_id == "spec.gloss_l"]
    first_gloss = next(i for i, r in enumerate(rig.provider.calls) if r.spec_id == "spec.gloss_l")
    first_scrn = next(i for i, r in enumerate(rig.provider.calls) if r.spec_id == "spec.scrn")
    assert (
        reqs and first_gloss < first_scrn
    )  # the glossary leads: it exists before any Screens call
    # GLOSS-L fails: the Brief's own terms are used and the locale calls are degraded
    bad = Rig(tmp_path, "standard", tag="bad")
    bad.provider.set_default("spec.gloss_l", ScriptedResponse(output="", refusal="no"))
    assert bad.full().status == "awaiting_g2"
    assert any(f.get("kind") == "degraded_locales" for f in bad.ctx.flags)
    assert (
        getattr(bad.ctx.graph.meta, "degraded_locales", None)
        or "glossary_degraded" in bad.ctx.graph.meta.facts
    )


# --------------------------------------------------------------------------------------------------- FN-34
@case("spc-fn-34")
def test_fn_34_the_cache_and_checkpoint_keys_carry_the_route_fingerprint():
    from mde_core.settings import load_settings
    from mde_llm_gateway.factory import route_fingerprint
    from mde_spec_core.callkit import BypassCache, InMemoryResultCache, result_cache_key

    a = route_fingerprint(
        load_settings(
            llm_mode="mock",
            model_routes={"fast": "gpt-6-luna", "standard": "gpt-6-luna", "deep": "gpt-6-luna"},
        )
    )
    b = route_fingerprint(
        load_settings(
            llm_mode="mock",
            model_routes={
                "fast": "claude-haiku-5-5",
                "standard": "claude-sonnet-5-5",
                "deep": "claude-haiku-5-5",
            },
        )
    )
    assert a != b and a and b
    ka = result_cache_key("t", "plan", "h", a, "1", "1", "L")
    kb = result_cache_key("t", "plan", "h", b, "1", "1", "L")
    assert ka != kb
    cache = InMemoryResultCache()
    cache.put("t", ka, {"x": 1})
    assert (
        cache.get("t", kb) is None
    )  # after a route change nothing is served from the old route's cache
    bypass = BypassCache()
    bypass.put("t", ka, {"x": 1})
    assert bypass.get("t", ka) is None  # PS-9 bypasses the cache
    # the run's checkpoints are keyed the same way
    from mde_spec_agent.run.checkpoint import Versions, key_for

    va, vb = Versions("s@1", "1", "1", "L", a), Versions("s@1", "1", "1", "L", b)
    assert key_for("p", "h", "plan", "b", va) != key_for("p", "h", "plan", "b", vb)


# --------------------------------------------------------------------------------------------------- FN-35
@case("spc-fn-35")
def test_fn_35_an_invalid_plan_after_its_reask_ends_the_run_partial_and_a_resume_retries_it(
    tmp_path,
):
    rig = Rig(tmp_path, "standard")
    gw = wrap_gateway(rig.ctx)

    def drop_owner(out):
        out = out.model_copy(deep=True)
        out.owners = out.owners[1:]
        return out

    gw.rewrite = lambda spec, inp, n: drop_owner if spec == "spec.plan" else None
    out = rig.go("plan")
    assert out.status == "partial" and out.reason == "plan_invalid"
    assert gw.count("spec.plan") == 2  # the call and its ONE re-ask; no default exists
    assert not rig.ctx.plan.get("proposal") and rig.ctx.flags == []
    gw.rewrite = None  # (a fallback route first, if one were configured: none is)
    assert rig.resume("plan").status == "awaiting_g2"
    assert rig.ctx.plan["proposal"]


# --------------------------------------------------------------------------------------------------- FN-36
@case("spc-fn-36")
def test_fn_36_a_card_that_needs_an_operation_product_never_wrote_is_unspecified_and_needs_a_decision(
    tmp_path,
):
    from mde_spec_agent.views import g2

    rig = Rig(tmp_path, "quick")
    gw = wrap_gateway(rig.ctx)
    done = {"n": 0}

    def unspecified(out):
        out = out.model_copy(deep=True)
        if out.actions and not done["n"]:
            done["n"] = 1
            out.actions[0] = out.actions[0].model_copy(update={"unspecified": True, "effect": []})
        return out

    gw.rewrite = lambda spec, inp, n: unspecified if spec == "spec.scrn" else None
    assert rig.full().status == "awaiting_g2"
    marked = [(s.id, a) for s in rig.ctx.graph.screens.values() for a in s.actions if a.unspecified]
    assert marked and all(
        a.effect == [] for _, a in marked
    )  # the action is kept, marked unspecified
    needs = [f for f in rig.ctx.flags if f.get("kind") == "needs_decision"]
    assert needs and needs[0]["owner"] == "product"
    view = g2(rig.ctx.graph, rig.ctx.pack, rig.ctx.check, rig.ctx.derived, flags=rig.ctx.flags)
    assert view["flags"]  # the open item reaches the owner's page
