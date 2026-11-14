"""SPC-IN-01..22: input from Compass (doc 15 section 12.1). Real exports, mutated copies, offline."""

from __future__ import annotations

from functools import cache
from typing import Any

import pytest
from edge_support.ea_support import (
    cached_flow,
    codes,
    derive,
    fixture_json,
    mutate,
    mutated,
    raw,
    read_export,
    real,
    validate,
)
from edge_support.registry import case

from mde_compass_intake import check_readiness, intake
from mde_spec_agent.views import g15
from mde_spec_core.pack import assemble


@cache
def derived(key: str):
    return derive(real(key))


def _blob(obj: Any) -> str:
    return str(obj).lower()


# ------------------------------------------------------------------------------------------- IN-01, IN-02


@case("spc-in-01")
def test_format_1_export_without_structure_is_derived_and_flagged(tmp_path_factory):
    for key in ("delhi", "pune"):
        si = real(key)
        assert si.compass_format == 1 and not si.structure.present and not si.structure.screens
        d = derived(key)
        assert d.inventory.structure_derived is True, key  # the flag
        assert len(d.graph.screens) >= 8, key  # screens are derived from the Brief
    f = cached_flow(tmp_path_factory, "in-delhi", lambda: raw("delhi"))
    assert f.status == "approved" and f.graph.screens and f.graph.stories  # the run reaches the end


@case("spc-in-01")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-7: with no Compass structure the baseline derives screens and stories but no entity, flow or operation (delhi: 0 entities, 0 flows, 0 operations after a full mock run), so the pack has no data model",
)
def test_a_derived_structure_includes_entities_and_flows(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-delhi", lambda: raw("delhi"))
    assert f.graph.entities and f.graph.flows and f.graph.operations


@case("spc-in-02")
def test_trimmed_export_derives_views_from_the_brief_and_parses_locales_from_a_sentence():
    data = raw("hospital")
    assert "consumer_views" not in data  # trimmed: no views at all
    si = real("hospital")
    assert si.locales.codes == ("mr", "gu", "hi", "en") and si.locales.parsed_from_text
    assert si.idea.summary and si.roles and si.apps  # everything came from the Brief
    # the other half of the rule: no locale in the text -> Default en plus a drift flag
    bare = mutate(data, fixture_json("spc-in-02", "mutation.json"))
    si2 = read_export(bare)
    assert si2.locales.codes == ("en",) and si2.locales.trust.assumed
    assert "locales_defaulted" in si2.drift_flags


# ------------------------------------------------------------------------------------------- IN-03..05


@case("spc-in-03")
def test_unknown_future_format_and_renamed_keys_load_with_drift_flags():
    data = mutated("spc-in-03", "standard")
    result = intake(data)
    si = result.spec_input
    assert si is not None and result.readiness.ready
    assert "unknown_format_version:3" in si.drift_flags and len(si.features) == 9
    # renamed keys: one drift flag per missing field, a plain list when idea, role or feature is gone
    brief = data["brief"]
    brief["capabilities"] = brief.pop("scope")
    brief["concept"] = brief.pop("idea")
    brief.pop("summary")
    data["consumer_views"]["research"].pop("idea")
    broken = intake(data)
    assert broken.spec_input is not None and not broken.readiness.ready
    assert {"field_missing:brief.scope", "field_missing:brief.idea"} <= set(
        broken.spec_input.drift_flags
    )
    assert broken.readiness.missing and all(len(m) > 20 for m in broken.readiness.missing)


@case("spc-in-04")
def test_degraded_input_keeps_every_default_a_default_and_flags_weak_inputs():
    si = real("standard")
    assert si.trust.degraded and si.trust.weak_inputs
    assert all(f.trust.grade == "default" for f in si.domain_facts if f.tier != "locked")
    assert si.structure.grade == "default" and si.tone.trust.grade == "default"
    assert any("weak inputs" in w for w in check_readiness(si).warnings)


@case("spc-in-04")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-1: the G2 view never says 'weak inputs': the reader sets trust.weak_inputs but no step turns it into a banner or a risk line",
)
def test_g2_leads_with_weak_inputs(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    g2 = f.g2()
    text = _blob([g2["banners"], g2["risks"], g2["summary"]["text"]])
    assert "weak input" in text


@case("spc-in-05")
def test_unverified_approval_proceeds_with_a_reader_flag():
    data = mutated("spc-in-05", "standard")
    si = read_export(data)
    assert si.input_binding == "unverified_no_baseline" and si.trust.weak_inputs
    assert check_readiness(si).ready  # proceeds


@case("spc-in-05")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-1: no G2 banner for an unverified approval (input_binding is read by the intake and dropped by the Spec flow)",
)
def test_unverified_approval_gets_a_g2_banner(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-nobaseline", lambda: mutated("spc-in-05", "standard"))
    assert f.status == "approved"
    g2 = f.g2()
    text = _blob([g2["banners"], g2["risks"]])
    assert "unverified" in text or "cannot be verified" in text or "approval" in text


# ------------------------------------------------------------------------------------------- IN-06..08


@case("spc-in-06")
def test_junk_and_duplicate_roles_and_apps_are_cleaned_with_merges_recorded():
    si = real("thorough")
    names = {r.id: r.name for r in si.roles}
    merged = {(names[m.from_id], names[m.into_id]) for m in si.merges if m.kind == "role"}
    assert merged == {("dispatcher", "dispatchers"), ("driver", "drivers")}
    assert [c.name for c in si.name_clashes] == ["Fleet maintenance screens"]
    assert "duplicate_roles:2" in si.cleaning_flags and "role_app_name_clash:1" in si.cleaning_flags
    d = derived("thorough")
    assert len(d.graph.roles) == 15 and "Fleet maintenance screens" not in {
        r.name for r in d.graph.roles.values()
    }
    assert not [a for a in d.graph.apps.values() if a.name == "Fleet maintenance screens"]
    # the merged roles stay reachable through an alias
    assert {a for r in d.graph.roles.values() for a in r.aliases} >= {"ROL-009", "ROL-011"}


@case("spc-in-06")
def test_a_role_that_is_also_an_app_is_resolved_visibly_and_not_dropped():
    si = real("standard")
    assert [c.name for c in si.name_clashes] == ["Manager side"]
    d = derived("standard")
    assert any("Manager side" in n for n in d.inventory.notes)
    assert "Manager" in {r.name for r in d.graph.roles.values()}
    assert "Manager side" in {a.name for a in d.graph.apps.values()}


@case("spc-in-06")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-2: an unresolved role/app clash is not listed in the G1.5 view (views.g15 shows only release-plan notes, not the inventory notes)",
)
def test_the_unresolved_clash_goes_to_g15():
    d = derived("standard")
    view = g15(d.graph, d)
    assert "manager side" in _blob(view["notes"])


@case("spc-in-07")
def test_duplicate_features_merge_with_an_alias_and_a_user_must_survives():
    si = real("standard")
    dup = next(f for f in si.features if f.duplicate_of)
    head = next(f for f in si.features if f.id == dup.duplicate_of)
    assert dup.id in head.alias_ids and head.user_evidenced and head.priority == "must"
    g = derived("standard").graph
    assert dup.id not in g.features and head.id in g.features
    assert dup.id in g.features[head.id].aliases
    assert g.features[head.id].user_evidenced and g.features[head.id].user_priority == "must"


@case("spc-in-08")
def test_mostly_nice_priorities_are_default_and_a_locked_must_is_never_demoted():
    si = real("thorough")
    assert "priorities_mostly_nice" in si.cleaning_flags
    g = derived("thorough").graph
    musts_in = {
        f.id
        for f in si.features
        if f.priority == "must" and f.user_evidenced and f.duplicate_of is None
    }
    assert musts_in and all(g.features[i].user_priority == "must" for i in musts_in)
    nice = [f.id for f in si.features if f.priority == "nice" and f.duplicate_of is None]
    assert nice and all(g.features[i].user_priority == "unranked" for i in nice)
    # order comes from journeys: the plan carries a criticality ordering for phase 1
    plan = derived("thorough").plan
    assert plan.order[1] and plan.journeys


# ------------------------------------------------------------------------------------------- IN-09..11


@case("spc-in-09")
def test_an_implied_payment_need_is_derived_mock_first():
    for key in ("standard", "quick"):
        si, d = real(key), derived(key)
        listed = {i.name.lower() for i in si.integrations}
        if key == "standard":
            assert "payments" not in listed and "payments" in d.facts  # implied, not listed
        pay = [i for i in d.graph.integrations.values() if i.need_key == "payments"]
        assert pay and all(i.mode == "mock" for i in pay), key  # Phase 1 is mock-first
        assert pay[0].provider_side_flow


@case("spc-in-09")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-3: the mismatch (payments implied by the Brief but absent from or real_later in Compass's integrations) is not flagged at G2",
)
def test_the_payment_mismatch_is_flagged_at_g2(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    g2 = f.g2()
    flagged = _blob([g2["risks"], g2["confirm_list"], g2["differences_from_brief"], g2["flags"]])
    assert "payment" in flagged and ("mismatch" in flagged or "not listed" in flagged)


@case("spc-in-10")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-4: the standard export says farmers sign in by phone code (FCT-036) and the manager by email (FCT-037); Spec reads only email_password and records no conflict or confirm item",
)
def test_conflicting_sign_in_facts_are_recorded_and_the_user_fact_wins(tmp_path_factory):
    si = real("standard")
    assert any("phone code" in f.statement for f in si.domain_facts)
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    g2 = f.g2()
    text = _blob([g2["confirm_list"], g2["flags"], g2["risks"], g2["differences_from_brief"]])
    assert "phone" in text and ("conflict" in text or "both" in text)


@case("spc-in-11")
def test_multi_country_geography_is_parsed_to_regions_with_codes():
    si = real("site")
    assert [c.code for c in si.geography.countries] == ["mx", "gt", "cr"]
    assert "multi_country" in si.flags and si.geography.trust.grade == "locked"
    d = derived("site")
    assert d.graph.meta.languages == ["en", "es"] or set(d.graph.meta.languages) == {"es", "en"}


@case("spc-in-11")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-5: no per-region currency, language or legal list reaches the graph (meta.country stays empty; no rule per country)",
)
def test_per_region_currency_language_and_legal_list_reach_the_graph():
    g = derived("site").graph
    text = _blob([g.meta.country, [r.statement for r in g.rules.values()]])
    assert g.meta.country and ("mxn" in text or "peso" in text or "currency" in text)


# ------------------------------------------------------------------------------------------- IN-12..14


@case("spc-in-12")
def test_right_to_left_and_mixed_scripts_reach_tokens_and_behaviours(tmp_path_factory):
    import json

    si = real("standard")
    assert si.locales.non_latin == ("mr", "hi")  # mixed scripts of the real export
    data = mutated("spc-in-12", "standard")
    ar = read_export(data)
    assert ar.locales.rtl == ("ar",)
    f = cached_flow(tmp_path_factory, "in-arabic", lambda: mutated("spc-in-12", "standard"))
    assert f.status == "approved" and "right_to_left" in f.graph.meta.facts
    tokens = json.loads(f.pack_files["global/tokens.dtcg.json"])
    names = (
        " ".join(next(iter(tokens.values())).get("names", []))
        if "names" in next(iter(tokens.values()))
        else json.dumps(tokens)
    )
    assert "font.script.arabic" in names and "font.script.devanagari" in names
    assert any(
        "right-to-left" in b.text for b in f.graph.behaviors.values() if b.category == "i18n"
    )
    risks = _blob(f.g2()["risks"])
    assert "native speaker" in risks and "ar" in risks


@case("spc-in-13")
def test_no_brand_derives_a_direction_and_checks_contrast(tmp_path_factory):
    si = real("standard")
    assert not si.brand.values and si.brand.trust.assumed
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    dirs = list(f.graph.directions.values())
    assert len(dirs) == 3 and not any(d.brand_locked for d in dirs)
    assert all(d.tier == "open" for d in dirs)  # a derived direction is never evidence
    assert all([p.kind for p in d.provenance] == ["model"] for d in dirs)
    assert "V-12" not in codes(validate(f.graph), "blocker")  # contrast met in light and dark
    assert f.graph.tokens and all(t["contrast"] for t in f.graph.tokens.values())


@case("spc-in-14")
def test_default_tone_is_assumed_and_never_user_evidence():
    for key in ("quick", "standard", "thorough"):
        tone = real(key).tone
        assert tone.trust.grade == "default" and tone.trust.assumed
        assert tone.trust.confidence == pytest.approx(0.4) and not tone.trust.verified_quote
    g = derived("standard").graph
    assert all(d.tier != "locked" for d in g.directions.values()) or not g.directions


# ------------------------------------------------------------------------------------------- IN-15..17


@case("spc-in-15")
def test_a_very_large_export_travels_in_slices_never_whole():
    d = derived("thorough")
    pack = assemble(d.graph)
    br = pack.budget_report
    assert br["tasks_over_budget"] == [] and br["max_task_read"] <= br["task_budget"]
    totals = [t["read_tokens"]["total"] for t in pack.tasks]
    assert (
        len(pack.tasks) > 20 and max(totals) < br["total_tokens"] / 5
    )  # no task reads the whole pack
    assert any(
        p.startswith("apps/") for p in pack.files
    )  # per-app and per-module files, not one blob
    assert sum(1 for p in pack.files if p.startswith("screens/")) >= 60


@case("spc-in-16")
def test_locked_facts_with_low_confidence_are_graded_default():
    si = real("thorough")
    low = [f for f in si.domain_facts if f.tier == "locked" and (f.trust.confidence or 1) < 0.7]
    assert len(low) == 33 and all(f.trust.grade == "default" for f in low)
    high = [f for f in si.domain_facts if f.tier == "locked" and (f.trust.confidence or 0) >= 0.7]
    assert high and all(f.trust.grade == "locked" for f in high if f.trust.verified_quote)


@case("spc-in-17")
def test_a_structure_with_errors_is_a_proposal_repaired_by_derivation():
    for key in ("standard", "thorough"):
        si = real(key)
        assert si.structure.grade == "default" and si.structure.issues
    errs = [i for i in real("standard").structure.issues if i.severity == "error"]
    assert {i.code for i in errs} == {"screen_entity_access_conflict"}
    d = derived("standard")
    assert len(d.seeded.access_repairs) >= 2 and "may not see" in " ".join(d.seeded.access_repairs)
    blk = codes(validate(d.graph, gate=2), "blocker")
    assert not blk & {"V-06", "V-07", "V-08", "V-03"}, (
        blk
    )  # the access conflicts are repaired in the graph


@case("spc-in-17")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-2: the Compass structure issue list is not carried to the G1.5 view (views.g15 reads only release-plan notes)",
)
def test_the_structure_issue_list_goes_to_g15():
    d = derived("standard")
    view = g15(d.graph, d)
    text = _blob(view["notes"])
    assert "access" in text and ("conflict" in text or "may not see" in text)


# ------------------------------------------------------------------------------------------- IN-18..20


@case("spc-in-18")
def test_hinglish_quotes_are_kept_and_technical_fields_stay_english(tmp_path_factory):
    data = mutated("spc-in-18", "standard")
    si = read_export(data)
    hing = fixture_json("spc-in-18", "mutation.json")[0]["value"]
    assert hing in {f.name for f in si.features} or any(hing in f.intent for f in si.features)
    f = cached_flow(tmp_path_factory, "in-hinglish", lambda: mutated("spc-in-18", "standard"))
    assert f.status == "approved"
    keys = list(f.graph.locale_copy.get("en", {}))
    assert keys and all(k.isascii() for k in keys)  # technical fields (ids, keys) stay English
    assert all(k.isascii() for k in f.graph.features)


@case("spc-in-19")
def test_a_changed_brief_gives_a_new_input_hash_and_a_new_content_hash(tmp_path_factory):
    base = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    new = cached_flow(tmp_path_factory, "in-plus-feature", lambda: mutated("spc-in-19", "standard"))
    assert base.run["input_hash"] != new.run["input_hash"]
    assert base.result["content_hash"] != new.result["content_hash"]
    # the approval of the old hash does not bind the new run: G2 had to reopen
    assert new.run["gates"]["g2"]["content_hash"] == new.result["content_hash"]
    assert base.run["approved_hash"] != new.run["approved_hash"]
    assert len(new.graph.features) == len(base.graph.features) + 1


@case("spc-in-19")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-6: no incremental regeneration exists: a run with a changed Brief starts from scratch (no input-hash diff, no reuse of the previous run's batches or list of touched sections)",
)
def test_only_the_touched_sections_are_regenerated_and_g2_names_them(tmp_path_factory):
    new = cached_flow(tmp_path_factory, "in-plus-feature", lambda: mutated("spc-in-19", "standard"))
    events = " ".join(str(e.get("type")) for e in new.events)
    assert "input.diff" in events or "reused" in events


@case("spc-in-20")
def test_a_locked_must_without_a_screen_gets_one_by_the_pull_in_rule():
    data = mutated("spc-in-20", "standard")
    si = read_export(data)
    assert not any("FTR-004" in s.serves for s in si.structure.screens)
    assert next(f for f in si.features if f.id == "FTR-004").user_evidenced
    d = derive(si)
    assert any("FTR-004" in p.features for p in d.plan.pulled_in)  # Product adds a screen
    assert d.plan.features["FTR-004"].status == "in"
    # (SCR-003 of the real export is a known V-19 finding of its own; the feature is not named)
    assert "FTR-004" not in {f.where for f in validate(d.graph, only={"V-19"})}
    # and the validator blocks when the screen is not there (the doc names V-29; the code's check is V-19)
    g = d.graph.model_copy(deep=True)
    for sid in [s.id for s in g.screens.values() if "FTR-004" in _screen_feats(g, s)]:
        del g.screens[sid]
    assert "FTR-004" in {f.where for f in validate(g, only={"V-19"}) if f.severity == "blocker"}


def _screen_feats(g, s):
    from mde_spec_core.validators.release import screen_features

    return screen_features(g, s)


# ------------------------------------------------------------------------------------------- IN-21, IN-22


@case("spc-in-21")
def test_a_user_deferral_and_a_capacity_deferral_keep_their_own_reasons(tmp_path_factory):
    si = real("standard")
    fct = next(f for f in si.features if f.deferred_by_user)
    d = derived("standard")
    dec = d.plan.features[fct.id]
    assert (dec.status, dec.reason) == ("later", "user") and dec.outline is not None
    g = d.graph
    assert g.features[fct.id].later_reason == "user" and g.features[fct.id].status == "later"
    assert g.features[fct.id].outline  # the outline lives in the ledger
    assert not [
        s for s in g.screens.values() if fct.id in _screen_feats(g, s)
    ]  # no screen nodes (V-19)
    assert "V-19" not in codes(validate(g, gate=2), "blocker")
    # a capacity deferral is a different reason and never relabelled to user
    t = derived("thorough").plan.later_by_reason()
    assert "user" not in t.get("first_release", []) and set(t) <= {
        "first_release",
        "user",
        "capacity",
    }
    assert fct.id not in {x for ids in t.values() for x in ids}


@case("spc-in-22")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-IN-8: existing systems (Tally, Spreadsheets) are only offered to the integrations call as provider names; no adapter integration need or import/sync flow is created, and nothing goes to confirm",
)
def test_existing_systems_become_adapter_integrations_with_an_import_flow(tmp_path_factory):
    si = real("thorough")
    assert "Tally" in {s.name for s in si.existing_systems}
    f = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    g = f.graph
    tally = next(
        (i for i in g.integrations.values() if "tally" in (i.name + i.need_key).lower()), None
    )
    assert tally is not None and tally.mode == "adapter"
    assert "import" in tally.flow.lower() or "sync" in tally.flow.lower()
    assert tally.assumed or tally.tier != "locked"  # goes to confirm
