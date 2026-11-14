"""SPC-SC-01..21: scale and kinds of app (doc 15 section 12.3). Synthetic presets, real exports (some mutated), offline."""

from __future__ import annotations

import asyncio
import copy
import json
from functools import cache
from typing import Any

import pytest
from edge_support.ea_support import (
    big_si,
    cached_flow,
    codes,
    derive,
    fixture_json,
    raw,
    read_export,
    real,
    tiny_si,
    validate,
)
from edge_support.registry import case

from mde_compass_intake.spec_input import Setting
from mde_spec_agent.views import g15
from mde_spec_core.pack import assemble
from mde_spec_core.synth import SynthSpec, make_graph


def _blob(obj: Any) -> str:
    return str(obj).lower()


@cache
def derived(key: str):
    return derive(real(key))


@cache
def synth_pack(name: str):
    return assemble(make_graph(name))


# ------------------------------------------------------------------------------------------- SC-01, SC-02, SC-03


@case("spc-sc-01")
def test_a_tiny_app_is_compact_with_a_trimmed_system_set_and_a_whole_pack_form():
    g = make_graph("tiny")
    assert g.meta.profile == "compact" and 3 <= len(g.screens) <= 6
    pack = synth_pack("tiny")
    assert pack.manifest["pack_form"] == "one_file_and_tree"
    assert pack.budget_report["total_tokens"] <= pack.manifest["whole_pack_tokens"]
    # a tiny input with no accounts gets only the system screens that apply
    d = derive(
        tiny_si(
            auth_methods=Setting(),
            roles=tuple(r.model_copy(update={"goal": "", "apps": ()}) for r in tiny_si().roles),
            apps=(),
        )
    )
    assert d.decision.profile == "compact"
    system = [s.name for s in d.graph.screens.values() if s.system]
    assert "Sign in" not in system and len(system) < 7


@case("spc-sc-01")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-2: pack_form says one_file_and_tree but no whole-pack file is written, and a tiny pack still has module folders (modules/MOD-001/module.md)",
)
def test_a_tiny_pack_has_one_whole_pack_file_and_no_module_folders():
    files = set(synth_pack("tiny").files)
    assert any("whole" in f or f.endswith("pack.md") for f in files)
    assert not [f for f in files if f.startswith("modules/")]


@case("spc-sc-02")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-3: there is no local-only mode: no predicate or input says 'no backend', and every pack ships Contract v0 (contract/openapi.yaml and resources) and no local-persistence section",
)
def test_no_backend_app_has_no_contract_and_local_persistence():
    si = tiny_si(
        idea=tiny_si().idea.model_copy(
            update={
                "summary": "A notes app that works fully on the phone, with no server and no accounts"
            }
        ),
        auth_methods=Setting(),
    )
    pack = assemble(derive(si).graph)
    assert not [f for f in pack.files if f.startswith("contract/")]
    assert "local" in pack.files["global/behaviors.md"].lower()


@case("spc-sc-03")
def test_a_read_only_content_app_has_no_forms_and_only_the_states_a_list_and_detail_need():
    base = tiny_si()
    ent = base.structure.entities[0].model_copy(
        update={"access": {"ROL-001": "view", "ROL-002": "view"}}
    )
    screens = tuple(s for s in base.structure.screens if s.pattern != "form")
    feats = tuple(f for f in base.features if f.id == "FTR-001")
    si = base.model_copy(
        update=dict(
            structure=base.structure.model_copy(update=dict(screens=screens, entities=(ent,))),
            features=feats,
            auth_methods=Setting(),
        )
    )
    g = derive(si).graph
    types = {s.screen_type for s in g.screens.values() if not s.system}
    assert types and types <= {"list", "detail"}
    assert not [s for s in g.screens.values() if not s.system and s.inputs]  # no forms
    assert not [
        a
        for s in g.screens.values()
        if not s.system
        for a in s.actions
        if a.effect
        and any(e.verb == "call" and "create" in (e.target or "").lower() for e in a.effect)
    ]
    assert "V-13" not in codes(validate(g), "blocker")


# ------------------------------------------------------------------------------------------- SC-04, SC-05, SC-06, SC-07


def _offline_export() -> dict[str, Any]:
    d = raw("standard")
    d["brief"]["scope"][0]["intent"] += " and it must work offline in poor network"
    return d


@case("spc-sc-04")
def test_offline_first_gets_queue_offline_states_and_idempotent_actions(tmp_path_factory):
    si = read_export(_offline_export())
    d = derive(si)
    assert "offline_first" in d.facts and d.decision.profile == "standard"
    assert "standard:offline_first" in d.decision.raised_by
    f = cached_flow(tmp_path_factory, "sc-offline", _offline_export)
    assert f.status == "approved"
    text = " ".join(b.text.lower() for b in f.graph.behaviors.values())
    assert "queued actions are sent in order" in text  # the sync queue
    assert any(s.kind == "offline" for sc in f.graph.screens.values() for s in sc.states)
    assert any("idempotent" in a.flags for sc in f.graph.screens.values() for a in sc.actions)


@case("spc-sc-04")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-11: an offline-first app gets a sync queue but no conflict policy: no behaviour, rule or state says what happens when the server copy changed meanwhile",
)
def test_offline_first_gets_a_conflict_policy(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "sc-offline", _offline_export)
    text = " ".join(b.text.lower() for b in f.graph.behaviors.values())
    assert "conflict" in text


@case("spc-sc-05")
def test_realtime_is_recognised_and_background_limits_are_noted(tmp_path_factory):
    d = derived("thorough")
    assert {"realtime", "background_work"} <= d.facts  # Trip tracking is in the release
    f = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    background = [b for b in f.graph.behaviors.values() if b.category == "background_work"]
    assert background and "limited" in background[0].text.lower()


@case("spc-sc-05")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-12: realtime is detected (fact) but the contract has no events or realtime channel for it (events.json holds only the payment callback) and no behaviour names a polling fallback",
)
def test_realtime_gets_a_channel_in_the_contract_and_a_polling_fallback(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    events = json.loads(f.pack_files["contract/events.json"])
    assert "realtime" in _blob(events) or "channel" in _blob(events)
    assert "poll" in " ".join(b.text.lower() for b in f.graph.behaviors.values())


@case("spc-sc-06")
def test_money_is_typed_with_a_currency_and_multi_country_is_recognised():
    si = real("site")
    assert "multi_country" in si.flags and [c.code for c in si.geography.countries] == [
        "mx",
        "gt",
        "cr",
    ]
    g = make_graph("small")
    money = [(e.id, fd.key) for e in g.entities.values() for fd in e.fields if fd.type == "money"]
    assert money
    ent, key = money[0]
    schema = json.loads(synth_pack("small").files[f"contract/schemas/{ent}.json"])
    prop = schema["properties"][key]
    assert set(prop["required"]) == {"amount", "currency"}
    assert prop["properties"]["amount"]["type"] == "integer"  # minor units, never a float
    assert prop["properties"]["currency"]["pattern"] == "^[A-Z]{3}$"


@case("spc-sc-06")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-13: no tax rule per region and no confirm item for tax is derived for a three-country export (meta.country is empty, no rule or confirm names tax)",
)
def test_tax_rules_per_region_go_to_confirm(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "sc-site", lambda: raw("site"))
    text = _blob([r.statement for r in f.graph.rules.values()]) + _blob(f.g2()["confirm_list"])
    assert "tax" in text
    assert f.graph.meta.country


@case("spc-sc-07")
def test_sensitive_data_gets_consent_age_gate_and_deletion():
    g = derived("pune").graph  # health data stated by Compass
    assert {"sensitive_data", "minors_or_health_or_identity"} <= derived("pune").facts
    names = " ".join(s.name.lower() for s in g.screens.values())
    assert "consent and age" in names and "delete account" in names
    policy = [p.ref for s in g.screens.values() for p in s.provenance if p.kind == "policy"]
    assert any("DELETION" in p for p in policy)


@case("spc-sc-07")
@pytest.mark.parametrize("key", ["hospital", "dakar"])
def test_sensitive_data_is_recognised_for_hospital_and_a_school_of_children(key):
    d = derived(key)
    assert "sensitive_data" in d.facts
    assert "consent" in " ".join(s.name.lower() for s in d.graph.screens.values())


# ------------------------------------------------------------------------------------------- SC-08, SC-09, SC-10, SC-11


@case("spc-sc-08")
def test_six_apps_share_entities_modules_and_cross_app_scenarios_once(tmp_path_factory):
    si = real("thorough")
    assert len([a for a in si.apps if a.is_real]) == 5 or len(si.apps) == 6
    f = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    g = f.graph
    assert len(g.apps) >= 5
    assert all(e.owner_module for e in g.entities.values())  # every entity is owned once
    shared = [m for m in g.modules.values() if m.shared and len(m.app_ids) > 1]
    assert shared  # a shared module is one node used by several apps
    assert len([sc for sc in g.screens.values() if sc.name == "Sign in"]) == 1  # specified once
    assert [fl for fl in g.flows.values() if fl.variant == "cross_app"]  # cross-app scenarios
    per_app: dict[str, int] = {}
    for s in g.screens.values():
        per_app[s.app_id] = per_app.get(s.app_id, 0) + 1
    assert max(per_app.values()) <= 60  # the cap applied per app, not to the program
    assert sum(per_app.values()) > 60


@case("spc-sc-09")
def test_one_purpose_for_many_roles_is_one_screen_with_role_conditions():
    si = real("standard")
    assert len(si.clean_roles()) == 8
    g = derived("standard").graph
    multi = [s for s in g.screens.values() if len(s.role_ids) >= 2 and not s.system]
    assert multi and any(s.role_conditions for s in multi)
    names = [s.name for s in g.screens.values() if not s.system]
    assert len(names) == len(set(names))  # no copy of a screen per role


@case("spc-sc-10")
def test_hundreds_of_entities_shard_the_data_model_and_stay_inside_the_budget():
    g = make_graph("erp")
    assert len(g.entities) >= 300
    pack = synth_pack("erp")
    shards = [f for f in pack.files if f.startswith("data-model/")]
    assert len(shards) >= 10
    assert pack.budget_report["tasks_over_budget"] == []
    assert pack.budget_report["max_task_read"] <= pack.budget_report["task_budget"]
    assert "contract/openapi.yaml" in pack.files
    # every shard names the entities it holds (an index by cluster)
    listed = sum(pack.files[f].count("## ENT-") for f in shards)
    assert listed == len(g.entities)


@case("spc-sc-11")
def test_twenty_integrations_each_get_an_entry_and_mock_first_in_the_plan():
    g = make_graph(SynthSpec("int20", integrations=20, screens_per_app=16, modules=4))
    assert len(g.integrations) == 20
    assert all(i.mode == "mock" for i in g.integrations.values())
    pack = assemble(g)
    text = pack.files["global/integrations.md"]
    assert all(i.id in text for i in g.integrations.values())
    plan = json.loads(pack.files["build/build-plan.json"])
    wave_text = _blob(plan["waves"]) + _blob([t.get("title") for t in plan["tasks"]])
    assert "mock" in wave_text  # mock adapters are built before any real provider


# ------------------------------------------------------------------------------------------- SC-12, SC-13


def _copy_locales(files):
    """The locales that have copy in a pack, whether it is one file per locale (a small pack) or a folder of shards."""
    out = set()
    for f in files:
        if f.startswith("global/copy/"):
            out.add(f[len("global/copy/") :].split("/")[0].removesuffix(".json"))
    return out


def _copy_of(files, locale):
    out = {}
    for f, text in files.items():
        if f == f"global/copy/{locale}.json" or f.startswith(f"global/copy/{locale}/"):
            out.update(json.loads(text))
    return out


@case("spc-sc-12")
def test_three_locales_get_one_copy_file_each_and_cards_carry_keys(tmp_path_factory):
    big = synth_pack("big")
    assert _copy_locales(big.files) == {"en", "hi", "kn"}  # every locale, small pack or sharded
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    assert _copy_locales(f.pack_files) == {"en", "hi", "mr"}
    card = f.pack_files[next(p for p in f.pack_files if p.startswith("screens/SCR-00"))]
    en = _copy_of(f.pack_files, "en")
    assert en and any(k in card for k in en)  # a card names its copy keys, not the text


@case("spc-sc-12")
def test_copy_is_sharded_by_module_and_locale():
    files = [f for f in synth_pack("big").files if f.startswith("global/copy/")]
    assert len(files) > 3


@case("spc-sc-13")
def test_a_double_start_joins_one_run_and_a_finished_input_is_a_cache_hit(tmp_path, monkeypatch):
    from mde_spec_agent.service import build_service

    monkeypatch.setenv("SPEC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MDE_ENV_FILE", "")
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path / "none"))
    path = tmp_path / "quick.json"
    path.write_text(json.dumps(raw("quick")), encoding="utf-8")

    async def go():
        svc = build_service()
        try:
            a, b = await asyncio.gather(
                svc.start_run(compass_path=path, offline=True),
                svc.start_run(compass_path=path, offline=True),
            )
            await asyncio.shield(svc._tasks[a.run_id])
            third = await svc.start_run(compass_path=path, offline=True)
            return a, b, third, svc.store.get_run(a.run_id), len(svc.list_runs()["runs"])
        finally:
            await svc.aclose()

    a, b, third, rec, n_runs = asyncio.run(go())
    assert a.run_id == b.run_id and (a.joined, b.joined).count(True) == 1  # one run, one joiner
    assert third.run_id == a.run_id and third.joined  # the result cache
    assert n_runs == 1 and rec["status"] == "awaiting_g2"


# ------------------------------------------------------------------------------------------- SC-14, SC-15


def _quick_plus_screens() -> dict[str, Any]:
    q = raw("quick")
    names = fixture_json("spc-sc-14", "added-screens.json")
    tpl = copy.deepcopy(q["brief"]["screens"][4])
    for i, name in enumerate(names):
        row = copy.deepcopy(tpl)
        row.update(
            id=f"SCR-{20 + i:03d}",
            name=name,
            purpose=f"Staff open the {name.lower()} and act on it",
        )
        q["brief"]["screens"].append(row)
        q["structure"]["slices"][2]["screens"].append({**row, "phase": "core"})
    return q


@case("spc-sc-14")
def test_a_quick_app_deriving_forty_screens_gets_the_v29_range_warning_and_is_never_trimmed():
    si = read_export(_quick_plus_screens())
    assert si.tier == "quick"
    d = derive(si)
    assert d.decision.profile == "compact" and len(d.graph.screens) >= 40
    assert len(d.graph.screens) == len(d.inventory.screens)  # nothing trimmed silently
    warn = [f for f in validate(d.graph, only={"V-29"}) if f.severity == "warning"]
    assert warn and "outside the usual range" in warn[0].why and "G1.5" in warn[0].fix


@case("spc-sc-14")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-4: the G1.5 view carries no 'the idea may be bigger than its tier' question; the V-29 range warning is only a validator finding",
)
def test_g15_asks_whether_the_idea_is_bigger_than_the_tier():
    d = derive(read_export(_quick_plus_screens()))
    text = _blob(g15(d.graph, d))
    assert "tier" in text or "usual range" in text


@case("spc-sc-15")
def test_ninety_core_screens_show_the_conflict_and_allow_everything_in_one_release():
    si = big_si(90)
    d = derive(si)
    assert d.plan.cap == 60 and d.plan.per_app["APP-001"].over_cap
    assert d.plan.consent_required and "user_must" in {c.kind for c in d.plan.needs_consent}
    view = g15(d.graph, d)
    assert view["consent_required"] and view["apps_over_limit"] == ["The app"]
    assert (
        view["everything_in_one_release"]["available"]
        and view["everything_in_one_release"]["warning"]
    )
    one = derive(si, everything_in_one_release=True)
    assert len(one.graph.screens) == len(d.graph.screens) >= 90  # nothing deferred
    assert one.plan.everything_in_one_release and not one.plan.needs_consent
    assert any("everything in one release" in n for n in one.plan.notes)  # the recorded warning
    assert g15(one.graph, one)["everything_in_one_release"]["chosen"] is True


# ------------------------------------------------------------------------------------------- SC-16, SC-17, SC-18, SC-19


@case("spc-sc-16")
def test_web_portals_are_a_surface_with_needs_and_no_layout(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    web = [a for a in f.graph.apps.values() if a.surface == "web"]
    assert len(web) >= 3 and {a.name for a in web} >= {"Manager portal", "Customer portal"}
    assert all(
        s.surface == "web" for s in f.graph.screens.values() if s.app_id in {a.id for a in web}
    )
    layout_words = {"grid", "column", "sidebar", "px", "pixel", "flex"}
    app_md = f.pack_files[f"apps/{web[0].id}/app.md"].lower()
    assert not [w for w in layout_words if f" {w} " in f" {app_md} "]


@case("spc-sc-16")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-8: nothing states the web-portal needs (dense tables, keyboard use): surface is only a tag and no behaviour, rule or app card mentions them",
)
def test_web_portal_needs_are_stated(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    blob = " ".join(f.pack_files[p].lower() for p in f.pack_files if p.endswith(".md"))
    assert "keyboard" in blob and ("dense" in blob or "table" in blob)


@case("spc-sc-17")
def test_marketplace_commission_is_a_locked_rule_and_the_payment_has_a_lifecycle(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    commission = next(r for r in f.graph.rules.values() if "4%" in r.statement)
    assert commission.tier == "locked"
    pay = next(e for e in f.graph.entities.values() if e.name.lower() == "payment")
    assert {"pending", "succeeded", "failed"} <= set(pay.lifecycle.states)


@case("spc-sc-17")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-5: no payout lifecycle or reconciliation states exist for a marketplace: the Payment entity stops at succeeded/failed and no payout entity or provider-on-confirm item is derived",
)
def test_marketplace_payouts_get_a_lifecycle_and_reconciliation_states(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    states = {s for e in f.graph.entities.values() for s in e.lifecycle.states}
    assert {"paid_out", "reconciled"} & states or any(
        "payout" in e.name.lower() for e in f.graph.entities.values()
    )


@case("spc-sc-18")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-6: no time-zone rule per entity, recurrence rule or calendar behaviour is derived for the scheduling app (quick yoga timetable): the behaviour categories hold none and no rule names a time zone",
)
def test_scheduling_gets_time_zone_recurrence_and_calendar_rules(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "sc-quick", lambda: raw("quick"))
    text = _blob([r.statement for r in f.graph.rules.values()]) + " ".join(
        b.text.lower() for b in f.graph.behaviors.values()
    )
    assert "time zone" in text or "timezone" in text
    assert "recurr" in text or "repeat" in text


@case("spc-sc-19")
def test_media_upload_gets_a_permission_flow_failure_and_resume(tmp_path_factory):
    d = derived("thorough")
    assert "media_upload" in d.facts and "sensitive_permission" in d.facts
    f = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    media = [b for b in f.graph.behaviors.values() if b.category == "media"]
    assert media and "resum" in media[0].text.lower() and "retr" in media[0].text.lower()
    assert any(b.category == "permissions" for b in f.graph.behaviors.values())
    files = [i for s in f.graph.screens.values() for i in s.inputs if i.type == "file"]
    assert files  # the driver documents are file inputs


@case("spc-sc-19")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-14: file inputs and file fields carry no size or type limit (constraint is empty for every file input of the thorough export) and the media behaviour states none",
)
def test_media_upload_has_size_and_type_limits(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    files = [i for s in f.graph.screens.values() for i in s.inputs if i.type == "file"]
    assert files and all(i.constraint for i in files)


@case("spc-sc-20")
def test_account_deletion_is_a_policy_backed_flow_whenever_accounts_exist():
    for key in (
        "quick",
        "standard",
        "thorough",
        "hospital",
        "pune",
        "dakar",
        "delhi",
        "bike",
        "site",
    ):
        d = derived(key)
        assert "accounts" in d.facts, key
        screens = [s for s in d.graph.screens.values() if s.name.lower() == "delete account"]
        assert len(screens) == 1, key
        refs = [p.ref for p in screens[0].provenance if p.kind == "policy"]
        assert refs and all("@" in r for r in refs), key  # a sheet id and a date (V-33)
    noaccounts = derive(tiny_si(auth_methods=Setting(), roles=(), apps=()))
    assert not [s for s in noaccounts.graph.screens.values() if "delete account" in s.name.lower()]


@case("spc-sc-20")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-9: the deletion screen is tier default, not policy-Locked, and no data-export flow exists for any export with accounts (only the site export has an unrelated regulator export)",
)
def test_deletion_and_export_are_policy_locked_flows():
    g = derived("quick").graph
    delete = next(s for s in g.screens.values() if s.name.lower() == "delete account")
    assert delete.tier == "locked"
    assert any("export" in s.name.lower() for s in g.screens.values())


@case("spc-sc-21")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-SC-10: hierarchy is not modelled: Role.level stays None for every role of the hospital and thorough exports, approvals are not lifecycle transitions gated by an approver role, and G1.5 lists no 'who approves what'",
)
@pytest.mark.parametrize("key", ["hospital", "thorough"])
def test_organisation_hierarchy_gives_levels_and_approver_gated_transitions(key):
    d = derived(key)
    assert any(r.level is not None for r in d.graph.roles.values())
    assert "approv" in _blob(g15(d.graph, d))
