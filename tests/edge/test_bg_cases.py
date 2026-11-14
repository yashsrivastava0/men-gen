"""SPC-BG (doc 19 section 6): big programs. The synthetic presets (`big`: one deep app of 60 screens; `program`: four apps and
140 screens; `erp`: 300 entities) are driven through the real packager and, where a run matters, through the real pipeline in
mock mode on a scripted gateway; Verify runs through its real port on the same runner. Offline, no live call."""

from __future__ import annotations

import asyncio
import random

import pytest
from edge_support import case
from edge_support.eb_run import (
    Rig,
    findings_of,
    load_input,
    pack_of,
    source_mentions,
    wrap_gateway,
)

from mde_core.errors import ProviderError
from mde_spec_agent.views import diff_packs, g2
from mde_spec_core.config import default_config
from mde_spec_core.graph import Entity, Integration, Lifecycle, Region
from mde_spec_core.synth import make_graph

CFG = default_config()


def changed(a, b) -> set[str]:
    return {f["path"] for f in diff_packs(b, a)["files"] if f["status"] != "same"}


# --------------------------------------------------------------------------------------------------- BG-01
@case("spc-bg-01")
def test_bg_01_shared_account_notification_and_help_screens_are_specified_once():
    from mde_spec_agent.derive import derive_graph

    d = derive_graph(load_input("thorough"))  # five apps (one phone, four web) from one Brief
    g = d.graph
    assert len(g.apps) == 5
    system = [s for s in g.screens.values() if s.system and s.status == "in"]
    names = [s.name for s in system]
    assert {"Sign in", "Notifications", "Help and support", "Settings"} <= set(names)
    assert len(names) == len(set(names))  # one screen, one id, for the whole program
    shared = next(s for s in system if s.name == "Sign in")
    served = {a for r in shared.role_ids for a in g.roles[r].app_ids}
    assert len(served) >= 2  # one id serves roles of several apps ...
    assert (
        len(shared.role_ids) > 3
    )  # ... and the role differences live in the closure's access rows, not in copies


@case("spc-bg-01")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-BG-1: a shared system screen belongs to ONE app (Screen.app_id is a single id), so the app map counts "
    "it for that app only; doc 19 says it is counted once per app that includes it, which the model cannot express",
)
def test_bg_01_a_shared_screen_is_counted_once_per_app_that_includes_it():
    from mde_spec_agent.derive import derive_graph

    d = derive_graph(load_input("thorough"))
    view = g2(d.graph, None, None, d)
    live = [s for s in d.graph.screens.values() if s.status == "in"]
    assert sum(a["screens"] for a in view["app_map"]) > len(live)


# --------------------------------------------------------------------------------------------------- BG-02
@case("spc-bg-02")
def test_bg_02_one_screen_serves_several_roles_with_one_id_and_visibility_is_part_of_the_block_match():
    from mde_spec_agent.derive import derive_graph
    from mde_spec_core.pack import detect_blocks

    d = derive_graph(load_input("thorough"))
    multi = next(s for s in d.graph.screens.values() if len(s.role_ids) > 1 and not s.system)
    p = pack_of(d.graph)
    card = p.files[f"screens/{multi.id}.md"]
    assert card.count(f"id: {multi.id} ") == 1  # one id, one file
    assert "access (this screen):" in card and len({r for r in multi.role_ids if r in card}) == len(
        multi.role_ids
    )
    assert "what each role sees and may do here differs" in card  # by role, in one place
    # role visibility is part of the block match: the same shape and binding for other roles is another block
    g = make_graph("small")
    ids = sorted(g.screens)[:6]
    for n, sid in enumerate(ids):
        s = g.screens[sid]
        s.role_ids = ["ROL-001"] if n < 3 else ["ROL-002"]
        r = s.regions[0]
        r.bind, r.shape, r.shape_kind = ["ENT-001.order_f1"], "collection", ""
    blocks, use = detect_blocks(g)
    assert len(blocks) == 2 and len(set(use.values())) == 2
    for sid in ids:
        g.screens[sid].role_ids = ["ROL-001"]
    assert len(detect_blocks(g)[0]) == 1  # same roles: one block


# --------------------------------------------------------------------------------------------------- BG-03
@case("spc-bg-03")
def test_bg_03_one_order_in_four_apps_is_one_hero_record_with_a_timeline_from_the_skeleton():
    from mde_spec_core import hero

    g = make_graph("program")
    apps_of: dict[str, set[str]] = {}
    for s in g.screens.values():
        for r in s.regions:
            for b in r.bind:
                if b.startswith("ENT-"):
                    apps_of.setdefault(b.split(".")[0], set()).add(s.app_id)
    shared = sorted(e for e, a in apps_of.items() if len(a) == 4)
    assert shared, "an entity shown by screens of all four apps"
    order = g.entities[shared[0]]
    g.hero = hero.mint_hero_skeleton(g, random.Random("bg03"))
    mine = [h for h in g.hero.values() if h.entity == order.id]
    assert (
        len({h.id for h in mine}) == len(mine) >= len(order.lifecycle.states)
    )  # one record per id, whatever the app
    assert {h.state for h in mine} >= set(
        order.lifecycle.states
    )  # the timeline: every status has a record

    def coherence():
        return [
            f
            for f in findings_of(g, only={"V-41"})
            if f.severity == "blocker" and "media field" not in f.why
        ]

    assert coherence() == []  # V-41 checks the skeleton
    for h in [h for h in mine if h.state == order.lifecycle.states[-1]]:
        del g.hero[h.id]
    assert [
        f for f in coherence() if f.why == f"no hero record in state {order.lifecycle.states[-1]}"
    ]
    groups = hero.fill_plan(g)
    assert (
        sum(order.id in gr.entities for gr in groups) == 1
    )  # the record set is written in ONE group


# --------------------------------------------------------------------------------------------------- BG-04
@case("spc-bg-04")
def test_bg_04_capacity_deferral_follows_criticality_and_a_must_never_moves_without_consent():
    from mde_spec_agent.derive import derive_graph

    si = load_input("thorough")
    small = CFG.model_copy(
        update={"scope": CFG.scope.model_copy(update={"scope_cap_per_app_phase": 10})}
    )
    d = derive_graph(si, None, small)
    g, plan = d.graph, d.plan
    assert plan.consent_required
    kinds = {c.kind for c in plan.needs_consent}
    assert {
        "capacity",
        "user_must",
        "over_cap",
    } <= kinds  # the conflict options are shown, not decided
    deferred = [
        f for f in g.features.values() if f.status == "later" and f.later_reason == "capacity"
    ]
    assert deferred
    assert not [
        f for f in deferred if f.user_priority == "must"
    ]  # a must is never deferred on its own
    # without the recorded G1.5 consent the validator refuses; with it the deferral stands
    assert {f.where for f in findings_of(g, only={"V-28"}) if "consent" in f.why} >= {
        f.id for f in deferred
    }
    for f in deferred:
        f.consent_id = "CON-G15"
    assert not [x for x in findings_of(g, only={"V-28"}) if "capacity deferral without" in x.why]
    over = [
        x
        for x in findings_of(g, only={"V-28"}, config=small)
        if "release screens against a cap" in x.why
    ]
    assert over  # the app is still over its (small) cap: stated, never trimmed silently


# --------------------------------------------------------------------------------------------------- BG-05
@case("spc-bg-05")
def test_bg_05_a_web_portal_is_exempt_from_the_column_limit_and_marked_web():
    g = make_graph("program")
    web = next(a for a in g.apps.values() if a.surface == "web")
    phone = next(a for a in g.apps.values() if a.surface == "phone")
    cols = CFG.checks.mobile_fit.max_phone_columns + 6
    for app in (web, phone):
        s = next(s for s in g.screens.values() if s.app_id == app.id and s.status == "in")
        s.regions.append(
            Region(key="wide_table", what="a table", shape="collection", attrs=[f"columns={cols}"])
        )
        if app is web:
            web_screen = s
        else:
            phone_screen = s
    assert web_screen.surface == "web" or web.surface == "web"
    found = [f for f in findings_of(g, only={"V-37"}) if "columns" in f.why]
    assert [f.where for f in found] == [
        phone_screen.id
    ]  # the phone breaches the limit; the web portal does not
    assert all(s.surface in ("web", "phone") for s in g.screens.values())


@case("spc-bg-05")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-BG-2: V-37 skips every non-phone screen entirely (`continue`), so the other mobile-fit rules the doc keeps "
    "for a web portal (primary regions, region count, primary actions) are never checked there",
)
def test_bg_05_the_other_mobile_fit_rules_still_apply_to_a_web_portal():
    g = make_graph("program")
    web = next(a for a in g.apps.values() if a.surface == "web")
    s = next(s for s in g.screens.values() if s.app_id == web.id and s.status == "in")
    for i in range(CFG.checks.mobile_fit.max_regions + 4):
        s.regions.append(Region(key=f"extra_{i}", what="x", shape="notice", importance="primary"))
    assert [f for f in findings_of(g, only={"V-37"}) if f.where == s.id]


# --------------------------------------------------------------------------------------------------- BG-06
@case("spc-bg-06")
def test_bg_06_hundreds_of_entities_shard_by_owner_and_a_task_names_only_the_shards_it_needs():
    g = make_graph("erp")
    assert len(g.entities) == 300
    p = pack_of(g)
    shards = {path: t for path, t in p.files.items() if path.startswith("data-model/")}
    assert len(shards) >= 10
    cap = CFG.budgets.global_shard_cap
    assert all(
        p.index and info["tokens"] <= cap
        for path, info in p.manifest["files"].items()
        if path in shards
    )
    seen: dict[str, str] = {}
    for path, text in shards.items():
        for line in text.split("\n"):
            if line.startswith("## ENT-"):
                eid = line.split()[1]
                assert eid not in seen  # an entity lives in exactly one shard
                seen[eid] = path
                assert (
                    g.entities[eid].owner_module in line
                )  # and the shard line names its owner module
    assert set(seen) == set(g.entities)
    needed = [len(t["reads"]["global"]) for t in p.tasks if t["wave"] == "W3"]
    assert needed and max(needed) < len(shards) / 2  # a task card names only the clusters it needs
    assert p.budget_report["tasks_over_budget"] == []


# --------------------------------------------------------------------------------------------------- BG-07
@case("spc-bg-07")
def test_bg_07_twenty_integrations_go_in_batches_of_five_each_with_flow_interruption_and_timeout(
    tmp_path,
):
    from mde_spec_agent.lanes.product.steps.inputs import integ_batches
    from mde_spec_agent.lanes.product.steps.plan import env_of

    rig = Rig(tmp_path, "standard")
    assert rig.go("plan").status == "awaiting_g2"
    g = rig.ctx.graph
    for i in range(20 - len(g.integrations)):
        g.add(
            "integrations",
            Integration(
                id=f"INT-S{900 + i}",
                name=f"Service {i}",
                need_key=f"service{i}",
                provider_side_flow=True,
            ),
        )
    assert len(g.integrations) == 20
    rig.ctx.save_graph()
    batches = integ_batches(rig.ctx, env_of(rig.ctx))
    assert [len(ids) for _, ids in batches] == [
        5,
        5,
        5,
        5,
    ]  # one PROD-INTEG batch per five integrations
    assert rig.go("product").status == "awaiting_g2"
    integ_calls = rig.provider_calls("spec.prod_integ")
    assert len(integ_calls) == 4
    flows = [i for i in rig.ctx.graph.integrations.values() if i.provider_side_flow]
    assert len(flows) >= 20 and all(
        i.flow.strip() and i.interruption.strip() and i.pending_timeout.strip() for i in flows
    )
    assert not [
        f for f in findings_of(rig.ctx.graph, only={"V-35"}) if f.severity == "blocker"
    ]  # the record check


# --------------------------------------------------------------------------------------------------- BG-08
@case("spc-bg-08")
def test_bg_08_five_locales_with_right_to_left_translate_the_glossary_first_and_flag_native_review(
    tmp_path,
):
    from mde_compass_intake.spec_input import Locales

    si = load_input("standard")
    si = si.model_copy(
        update={
            "locales": Locales(
                codes=("en", "hi", "ar", "kn", "fr"),
                search_languages=("en",),
                source="view",
                non_latin=("hi", "ar", "kn"),
                rtl=("ar",),
            )
        }
    )
    rig = Rig(tmp_path, "standard", spec_input=si)
    assert rig.full_from_input().status == "awaiting_g2"
    g = rig.ctx.graph
    assert g.meta.languages == ["en", "hi", "ar", "kn", "fr"]
    gloss = rig.provider_calls("spec.gloss_l")
    assert len(gloss) == 1 and set(gloss[0].input.languages) == {
        "hi",
        "ar",
        "kn",
        "fr",
    }  # glossary into all, in one call
    first_copy = next(i for i, r in enumerate(rig.provider.calls) if r.spec_id == "spec.copy_l")
    assert (
        next(i for i, r in enumerate(rig.provider.calls) if r.spec_id == "spec.gloss_l")
        < first_copy
    )
    per_call = [len(r.input.strings) for r in rig.provider_calls("spec.copy_l")]
    assert (
        per_call and max(per_call) <= CFG.fusion.strings_per_locale_call
    )  # locale batches by 300 strings
    scripts = {
        row["script"]: row["rtl"] for row in g.tokens[sorted(g.tokens)[0]]["report"]["scripts"]
    }
    assert (
        scripts["Arabic"] is True and scripts["Kannada"] is False
    )  # script-fallback tokens, RTL known
    assert "right-to-left" in " ".join(
        b.text.lower() for b in g.behaviors.values() if b.category == "i18n"
    )
    view = g2(g, rig.ctx.pack, rig.ctx.check, rig.ctx.derived)
    assert any(
        "native speaker" in r["text"] and "ar" in r["text"] for r in view["risks"]
    )  # needs_native_review


# --------------------------------------------------------------------------------------------------- BG-09
def _reference(tmp_path, preset="big", tag="ref"):
    ref = Rig(tmp_path, preset, synth=preset, tag=tag)
    assert ref.full().status == "awaiting_g2"
    return ref


@case("spc-bg-09")
def test_bg_09_a_short_rate_limit_storm_in_the_screens_wave_is_absorbed_with_identical_bytes(
    tmp_path,
):
    ref = _reference(tmp_path)
    rig = Rig(tmp_path, "big", synth="big", tag="storm")
    err = ProviderError("slow down", status=429, retryable=True)
    from mde_llm_gateway.providers.base import ProviderCallError

    storm = ProviderCallError("slow down", status=429, retryable=True)
    assert isinstance(err, Exception)
    rig.provider.queue("spec.scrn", storm, storm, storm)
    assert rig.full().status == "awaiting_g2"
    assert len(rig.clock.sleeps) >= 3  # it backed off, it did not hammer
    assert rig.ctx.graph.canonical_text() == ref.ctx.graph.canonical_text()
    assert rig.ctx.pack.package_hash == ref.ctx.pack.package_hash


@case("spc-bg-09")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-MD-2: a sustained storm opens the breaker and CircuitOpen is swallowed by CallRunner, so the Screens wave "
    "does not end partial; every card takes its fallback and the run reaches G2 degraded (also: the limiter is never "
    "halved, BUG-MD-3)",
)
def test_bg_09_a_sustained_storm_ends_the_wave_partial_halves_concurrency_and_resumes_identically(
    tmp_path,
):
    from mde_llm_gateway.providers.base import ProviderCallError

    ref = _reference(tmp_path)
    rig = Rig(tmp_path, "big", synth="big", tag="storm")
    pre = ("intake", "plan", "product", "design", "join", "freeze", "gloss")
    assert rig.go(*pre).status == "awaiting_g2"
    k0 = rig.ctx.limiter.k
    rig.provider.set_default(
        "spec.scrn", ProviderCallError("slow down", status=429, retryable=True)
    )
    out = rig.go("screens")
    assert out.status == "partial" and out.reason == "rate_limit"
    assert rig.ctx.limiter.k < k0
    rig.provider.set_default("spec.scrn", rig.provider._defaults["*"])
    rig.clock.advance(120)
    assert rig.full().status == "awaiting_g2"
    assert rig.ctx.graph.canonical_text() == ref.ctx.graph.canonical_text()


# --------------------------------------------------------------------------------------------------- BG-10
def _verify_port(rig):
    from mde_spec_agent.service import _RunnerCall
    from mde_verify_agent.port import build_verify_port

    return build_verify_port(_RunnerCall(rig.ctx.runner), CFG)


@case("spc-bg-10")
def test_bg_10_two_apps_disagreeing_on_statuses_become_a_class_b_patch_to_the_owning_module(
    tmp_path,
):
    from mde_spec_core.verify_port import StructureInput
    from mde_verify_agent.calls.registry import ALL_SPECS

    rig = Rig(tmp_path, "standard", extra_specs=[s.spec for s in ALL_SPECS])
    g = make_graph("big")
    canon = g.entities["ENT-001"]
    g.add(
        "entities",
        Entity(
            id="ENT-900",
            name=canon.name,
            owner_module="MOD-002",
            lifecycle=Lifecycle(states=["new", "paid", "shipped"], initial="new"),
        ),
    )
    report = asyncio.run(_verify_port(rig).structure_critic(StructureInput(graph=g)))
    hits = [d for d in report.findings if d.code == "S-XAPP"]
    assert any(d.cls == "B" and d.node == canon.owner_module for d in hits)  # to the OWNING module
    patch = next(p for p in report.patches if p.node == canon.owner_module)
    assert patch.cls == "B" and "ENT-900" in patch.field
    assert not [
        d for d in report.findings if d.code == "V-11" and d.node == "ENT-900"
    ]  # folded into the specific one
    from mde_spec_agent.run.verify_apply import apply_patches

    applied = apply_patches(g, list(report.patches))
    assert (
        not applied.applied and canon.owner_module in applied.degraded_nodes
    )  # Spec marks it and lists it at G2


@case("spc-bg-10")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-BG-3: Spec does not apply a class B patch: apply_patches only applies class A forms and marks the owning "
    "module `degraded` for B and C, so the two apps keep disagreeing and nothing re-verifies the touched node",
)
def test_bg_10_spec_applies_the_class_b_patch_and_the_touched_nodes_are_re_verified(tmp_path):
    from mde_spec_agent.run.verify_apply import apply_patches
    from mde_spec_core.verify_port import StructureInput
    from mde_verify_agent.calls.registry import ALL_SPECS

    rig = Rig(tmp_path, "standard", extra_specs=[s.spec for s in ALL_SPECS])
    g = make_graph("big")
    canon = g.entities["ENT-001"]
    g.add(
        "entities",
        Entity(
            id="ENT-900",
            name=canon.name,
            owner_module="MOD-002",
            lifecycle=Lifecycle(states=["new", "paid", "shipped"], initial="new"),
        ),
    )
    port = _verify_port(rig)
    report = asyncio.run(port.structure_critic(StructureInput(graph=g)))
    applied = apply_patches(g, list(report.patches))
    assert applied.applied
    again = asyncio.run(port.structure_critic(StructureInput(graph=g)))
    assert not [d for d in again.findings if d.code == "S-XAPP"]


# --------------------------------------------------------------------------------------------------- BG-11
@case("spc-bg-11")
def test_bg_11_a_card_over_the_read_budget_splits_inside_one_module_and_is_never_truncated():
    from mde_spec_core.pack import PackBreach

    g = make_graph("big")
    base = pack_of(g)
    biggest = max(
        (t for t in base.tasks if t["wave"] == "W3"), key=lambda t: t["read_tokens"]["total"]
    )
    tight = biggest["read_tokens"]["total"] - 800
    split = pack_of(g, task_read_budget=tight)
    assert split.budget_report["tasks_over_budget"] == [] and len(split.tasks) > len(base.tasks)
    modules_of = {}
    for t in split.tasks:
        if t["wave"] == "W3":
            mods = {g.screens[x[8:-3]].module_id for x in t["reads"]["screens"]}
            assert (
                len(mods) == 1 and t["module"] in mods
            )  # a split is at a module boundary, never across one
            modules_of.setdefault(t["module"], []).append(t["id"])
    for sid in g.screens:  # nothing was cut: every screen is still read by exactly one task
        if f"screens/{sid}.md" in base.files:
            assert sum(f"screens/{sid}.md" in t["reads"]["screens"] for t in split.tasks) == 1
    assert base.files == pack_of(g).files  # the card text itself is untouched by the budget
    with pytest.raises(PackBreach) as e:
        pack_of(g, task_read_budget=1500)
    assert (
        e.value.error["code"] == "TASK_OVER_BUDGET"
    )  # and what cannot be split is reported, not cut


@case("spc-bg-11")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-BG-5: a heavy card does not move its detail to screens/<id>.detail.md: an over-cap card fails the build "
    "with PackBreach (same gap as GAP-FN-4)",
)
def test_bg_11_a_heavy_card_moves_its_detail_to_a_detail_file():
    g = make_graph("big")
    g.screens["SCR-001"].purpose = "word " * 9000
    assert "screens/SCR-001.detail.md" in pack_of(g).files


# --------------------------------------------------------------------------------------------------- BG-12
@case("spc-bg-12")
def test_bg_12_a_kill_in_the_screens_wave_resumes_from_the_last_batch_to_the_same_bytes(tmp_path):
    ref = _reference(tmp_path)
    ref_calls = len(ref.provider.calls)
    rig = Rig(tmp_path, "big", synth="big", tag="kill")
    gw = wrap_gateway(rig.ctx)
    count = {"n": 0}

    def kill(spec, inp, n):
        if spec == "spec.scrn":
            count["n"] += 1
            if count["n"] == 4:
                return RuntimeError("process killed")
        return None

    gw.raise_on = kill
    out = rig.full()
    assert out.status == "failed" and out.stage == "screens+hero+behav"  # the process died mid-wave
    rig.store.update_run(rig.ctx.run_id, status="running")
    assert rig.store.recover_interrupted() == [rig.ctx.run_id]
    gw.raise_on = None
    before = len(rig.provider.calls)
    assert rig.resume_full().status == "awaiting_g2"
    assert rig.ctx.graph.canonical_text() == ref.ctx.graph.canonical_text()
    assert rig.ctx.pack.package_hash == ref.ctx.pack.package_hash
    assert len(rig.provider.calls) - before < ref_calls  # the finished batches were not asked again


# --------------------------------------------------------------------------------------------------- BG-13
@case("spc-bg-13")
def test_bg_13_phase_2_regenerates_only_what_is_new_and_phase_1_stays_immutable():
    g = make_graph("program")
    later = [
        s
        for s in sorted(g.screens)
        if g.screens[s].module_id in ("MOD-005", "MOD-006") and not g.screens[s].system
    ][:6]
    assert len(later) >= 3
    phase1_graph = g.model_copy(deep=True)
    for sid in later:
        phase1_graph.screens[sid].status, phase1_graph.screens[sid].later_reason = (
            "later",
            "first_release",
        )
    phase1 = pack_of(phase1_graph)
    frozen = dict(phase1.files)
    approved_hash = phase1.content_hash
    phase2 = pack_of(g)
    diff = {f["path"]: f["status"] for f in diff_packs(phase2, phase1)["files"]}
    assert {p for p, s in diff.items() if s == "added" and p.startswith("screens/")} >= {
        f"screens/{s}.md" for s in later
    }
    assert not [p for p, s in diff.items() if s == "changed" and p.startswith("screens/")]
    assert (
        phase1.files == frozen and phase1.content_hash == approved_hash
    )  # the approved baseline never moved
    assert phase2.content_hash != approved_hash  # Phase 2 gets its own


# --------------------------------------------------------------------------------------------------- BG-14
@case("spc-bg-14")
def test_bg_14_a_feature_change_after_g2_is_found_by_node_hash_and_only_its_task_cards_change():
    from mde_spec_core.verify_port import node_hash

    g = make_graph("big")
    before = pack_of(g)
    sid = "SCR-009"
    g2_ = g.model_copy(deep=True)
    g2_.screens[sid].purpose = "A changed purpose after approval"
    after = pack_of(g2_)
    touched = {n.id for n in g.all_nodes() if node_hash(g, n.id) != node_hash(g2_, n.id)}
    assert touched == {sid}  # Verify re-checks exactly the touched node
    changed_files = changed(before, after) - {
        "MANIFEST.json",
        "index.json",
        "build/build-plan.json",
    }
    assert changed_files <= {f"screens/{sid}.md"} | {
        p for p in changed_files if p.startswith("modules/")
    }
    reread = [t["id"] for t in after.tasks if f"screens/{sid}.md" in t["reads"]["screens"]]
    assert len(reread) == 1  # one task card has to be re-built and re-checked
    assert (
        after.content_hash != before.content_hash
        and before.files["screens/SCR-009.md"] != after.files["screens/SCR-009.md"]
    )


@case("spc-bg-14")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-BG-4: there is no change controller or change set in Spec or Verify (no source mentions one), so an L2 "
    "change after G2 is not filed, regenerated or re-verified by anything but a full new run",
)
def test_bg_14_the_change_controller_files_a_change_set():
    assert source_mentions("change set", "change_set", "changeset")


# --------------------------------------------------------------------------------------------------- BG-15
@case("spc-bg-15")
def test_bg_15_two_canary_runs_that_disagree_give_the_union_of_questions_and_the_stricter_result():
    from mde_spec_core.verify_port import AcceptanceCover, CanaryResult, merge_canaries

    lenient = CanaryResult(
        task_id="T-0007",
        run_index=0,
        blocking_questions=["Which currency does the total use?"],
        acceptance_coverage=[
            AcceptanceCover(criterion="shows the total", satisfiable=True, how="from the card")
        ],
    )
    strict = CanaryResult(
        task_id="T-0007",
        run_index=1,
        blocking_questions=[
            "Which currency does the total use?",
            "What happens when the list is empty?",
        ],
        acceptance_coverage=[
            AcceptanceCover(
                criterion="shows the total", satisfiable=False, how="no field holds it"
            ),
        ],
    )
    merged = merge_canaries([lenient, strict])
    assert merged.disagreement and merged.stricter_run == 1
    assert sorted(merged.blocking_questions) == sorted(
        {"Which currency does the total use?", "What happens when the list is empty?"}
    )  # the union, once each
    assert merged.unsatisfied() == ["shows the total"]  # the card is judged by the stricter result
    assert [r.run_index for r in merged.runs] == [0, 1]  # both runs are recorded, none dropped


# --------------------------------------------------------------------------------------------------- BG-16
@case("spc-bg-16")
def test_bg_16_when_the_cap_is_reached_mid_run_the_call_is_refused_and_the_pack_is_never_delivered(
    tmp_path,
):
    from mde_spec_agent.spend import Prices

    rig = Rig(tmp_path, "big", synth="big", cap_micros=40_000)
    rig.ctx.guard.prices = Prices(
        "1.00", "1.00"
    )  # priced like a live run: the worst case of one call is above the cap
    out = rig.full()
    assert out.status == "partial" and out.reason == "budget_cap"  # refused, checkpointed, partial
    rec = rig.store.get_run(rig.ctx.run_id)
    assert rec["status"] == "partial" and rec["partial_reason"] == "budget_cap"
    assert "pack was not delivered half-checked" in rec["note"]  # a plain message
    assert rig.ctx.pack is None and not (rig.store.run_dir(rig.ctx.run_id) / "pack").exists()
    assert rec.get("content_hash") is None and rec.get("approved_hash") is None
    snap = rig.record.snapshot()
    assert snap["spent_micros"] <= snap["cap_micros"]  # the cap held: nothing was spent past it
    assert (
        rig.ctx.runner.calls is not None
    )  # some small calls ran inside the cap; the one that would pass it was refused
