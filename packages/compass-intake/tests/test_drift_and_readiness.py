"""Drift (SPC-IN-03, 05), the thin-input ladder and the readiness pre-check (PS-1).

Readiness is fatal ONLY for no idea statement or no feature. Everything else proceeds as ``thin_input``, with missing
roles derived and marked assumed, and the reader never raises over a format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import check_readiness, intake, read_export
from mde_compass_intake.readiness import NO_BRIEF, NO_FEATURE, NO_IDEA

HERE = Path(__file__).resolve().parent
NINE = HERE.parents[2] / "docs" / "compass" / "research-inputs"


def small() -> dict[str, Any]:
    return json.loads(
        (HERE / "fixtures" / "formats" / "format2_full.json").read_text(encoding="utf-8")
    )


def standard() -> dict[str, Any]:
    return json.loads(
        (NINE / "standard-produce-marketplace-nashik.json").read_text(encoding="utf-8")
    )


# --- SPC-IN-03: unknown future version and renamed keys ------------------------------------------------------------


def test_an_unknown_future_version_loads_with_a_drift_flag_and_is_ready() -> None:
    data = standard()
    data["export"]["format_version"] = 3
    result = intake(data)
    assert result.spec_input is not None and result.readiness.ready
    assert "unknown_format_version:3" in result.spec_input.drift_flags
    assert result.spec_input.compass_format == 3 and len(result.spec_input.features) == 9


def test_a_renamed_scope_section_is_read_through_its_alias_and_flagged() -> None:
    data = standard()
    data["brief"]["features"] = data["brief"].pop("scope")
    si = read_export(data)
    assert len(si.features) == 9 and "key_alias:features->scope" in si.drift_flags
    assert check_readiness(si).ready


def test_renamed_keys_load_with_one_drift_flag_per_missing_field_and_readiness_fails_with_a_plain_list() -> (
    None
):
    data = standard()
    brief = data["brief"]
    brief["capabilities"] = brief.pop("scope")  # renamed, no known alias
    brief["concept"] = brief.pop("idea")
    brief.pop("summary")
    data["consumer_views"]["research"].pop("idea")
    result = intake(data)
    si = result.spec_input
    assert si is not None, "loads: only a Brief with nothing at all is refused"
    assert {"field_missing:brief.scope", "field_missing:brief.idea"} <= set(si.drift_flags)
    assert result.readiness.ready is False
    assert result.readiness.missing == (NO_IDEA, NO_FEATURE)
    assert all(isinstance(line, str) and len(line) > 20 for line in result.readiness.missing)
    assert si.roles and si.apps, "everything else is still read"


def test_a_present_but_empty_section_and_a_changed_enum_are_drift_flags() -> None:
    data = small()
    data["brief"]["scope"][0]["priority"] = "urgent"
    data["brief"]["scope"][1]["scope_status"] = "someday"
    data["brief"]["scope"][2]["tier"] = "Verified"
    data["brief"]["access"] = {}
    si = read_export(data)
    assert {
        "enum_changed:scope.priority=urgent",
        "enum_changed:scope.scope_status=someday",
        "enum_changed:tier=verified",
        "field_empty:brief.access",
    } <= set(si.drift_flags)
    assert next(f.priority for f in si.features) == "urgent"  # carried as written, never rewritten


def test_a_size_band_two_steps_from_the_tier_is_a_size_jump() -> None:
    data = standard()
    data["structure"]["band"] = "small"
    data["brief"]["meta"]["plan"]["interview_budget"] = "thorough"
    si = read_export(data)
    assert "size_band_tier_mismatch:thorough/small" in si.drift_flags
    assert (si.tier, si.size_band) == ("thorough", "small")


def test_an_export_that_states_no_size_derives_it_from_the_tier_and_says_so() -> None:
    data = standard()
    data.pop("structure")
    data["consumer_views"]["research"].pop("guidance")
    si = read_export(data)
    assert (si.size_band, si.size_band_source) == ("medium", "tier")
    assert "size_band_from_tier" in si.drift_flags
    data["brief"]["meta"]["plan"] = {}
    assert {"tier_defaulted"} <= set(read_export(data).drift_flags)


# --- SPC-IN-04 and 05: weak inputs ---------------------------------------------------------------------------------


def test_degraded_and_unverified_inputs_lead_with_weak_inputs() -> None:
    assert read_export(standard()).trust.weak_inputs  # degraded: true in the real run
    data = small()
    assert not read_export(data).trust.weak_inputs
    data["export"].pop("approved_baseline")
    si = read_export(data)
    assert si.trust.weak_inputs and si.input_binding == "unverified_no_baseline"
    assert any("weak inputs" in w for w in check_readiness(si).warnings)
    assert check_readiness(si).ready, "weak is a banner, never a refusal"


# --- readiness ---------------------------------------------------------------------------------------------------------


def test_no_idea_fails_readiness_with_one_plain_line() -> None:
    data = small()
    data["brief"]["idea"] = {}
    data["brief"].pop("summary", None)
    data["consumer_views"]["research"]["idea"] = {}
    r = intake(data).readiness
    assert not r.ready and r.missing == (NO_IDEA,)


def test_no_feature_fails_readiness_with_one_plain_line() -> None:
    data = small()
    data["brief"]["scope"] = []
    r = intake(data).readiness
    assert not r.ready and r.missing == (NO_FEATURE,)


def test_features_that_are_all_out_of_scope_are_no_feature() -> None:
    data = small()
    for f in data["brief"]["scope"]:
        f["scope_status"] = "out"
    assert intake(data).readiness.missing == (NO_FEATURE,)


def test_no_idea_and_no_feature_lists_both() -> None:
    data = small()
    data["brief"]["idea"], data["brief"]["scope"] = {}, []
    data["brief"].pop("summary", None)
    data["consumer_views"]["research"]["idea"] = {}
    assert intake(data).readiness.missing == (NO_IDEA, NO_FEATURE)


def test_a_file_that_is_not_an_export_is_a_readiness_failure_not_an_exception() -> None:
    for junk in ({}, {"brief": "x"}, [], None, 5):
        result = intake(junk)  # type: ignore[arg-type]
        assert result.spec_input is None and result.refused == "not_an_export"
        assert result.readiness.missing == (NO_BRIEF,) and not result.readiness.ready
    nothing = small()
    nothing["brief"].update(idea={}, summary="", research_requests=[], domain_facts=[])
    nothing.pop("consumer_views")
    result = intake(nothing)
    assert result.refused == "unusable_brief" and result.readiness.missing == (NO_IDEA, NO_FEATURE)


def test_missing_roles_are_derived_from_the_audience_and_marked_assumed() -> None:
    data = small()
    data["brief"]["roles"] = []
    si = read_export(data)
    derived = [r for r in si.roles if r.assumed]
    assert derived and next(r.id for r in derived) == "ROL-S001"
    assert all(r.trust.grade == "default" and r.trust.assumed for r in derived)
    assert {r.name for r in derived} >= set(si.audience[:1]) or si.audience == ()
    assert si.thin_input and any(a.field == "roles" for a in si.assumptions)
    assert check_readiness(si).ready and any(
        w.startswith("roles") for w in check_readiness(si).warnings
    )


def test_with_no_audience_one_generic_role_is_derived_and_an_administrator_when_a_feature_manages() -> (
    None
):
    data = small()
    data["brief"]["roles"], data["brief"]["audience"] = [], {}
    data["consumer_views"]["research"]["audience"] = {}
    si = read_export(data)
    names = [r.name for r in si.roles if r.assumed]
    assert names[0] == "User"
    cue = any(
        w in f"{f.name} {f.intent}".lower()
        for f in si.features
        for w in ("manage", "admin", "approve", "report")
    )
    assert ("Administrator" in names) is cue


def test_roles_that_are_all_junk_or_app_names_count_as_missing() -> None:
    data = small()
    app = data["brief"]["apps"][0]["name"]
    data["brief"]["roles"] = [
        {**data["brief"]["roles"][0], "name_in_user_words": "Roles"},
        {**data["brief"]["roles"][1], "id": "ROL-099", "name_in_user_words": app},
    ]
    si = read_export(data)
    assert not [r for r in si.roles if not r.assumed and r.trust.grade == "locked"]
    assert [r for r in si.roles if r.assumed] and si.thin_input


def test_a_missing_locale_defaults_to_english_with_a_flag_and_an_assumption() -> None:
    """SPC-IN-02: locales parsed, else Default ``en`` plus a drift flag."""
    data = small()
    for place in (
        data["brief"]["experience"],
        data["brief"]["meta"],
        data["brief"]["search_hints"],
    ):
        for key in ("locales", "languages"):
            place.pop(key, None)
    data["consumer_views"]["research"].pop("languages")
    si = read_export(data)
    assert (
        si.locales.codes == ("en",) and si.locales.source == "default" and si.locales.trust.assumed
    )
    assert "locales_defaulted" in si.drift_flags and si.thin_input


def test_right_to_left_and_non_latin_locales_are_reported() -> None:
    """SPC-IN-12 (mutation: Arabic)."""
    data = small()
    data["consumer_views"]["research"]["languages"] = {"locales": ["ar", "en", "hi"]}
    si = read_export(data)
    assert si.locales.codes == ("ar", "en", "hi") and si.locales.rtl == ("ar",)
    assert si.locales.non_latin == ("ar", "hi")


def test_a_weak_or_missing_geography_is_uncertain_and_default() -> None:
    data = small()
    data["brief"]["context"] = {}
    data["consumer_views"]["research"]["geography"] = {}
    si = read_export(data)
    assert (
        si.geography.uncertain
        and si.geography.trust.grade == "default"
        and "geo_uncertain" in si.flags
    )
    assert any(a.field == "geography" for a in si.assumptions)


# --- evidence: a quote must be found in the record -----------------------------------------------------------------------


def test_a_feature_with_no_quote_or_a_quote_not_in_the_record_is_default() -> None:
    data = standard()
    scope = data["brief"]["scope"]
    scope[0]["provenance"] = []
    scope[1]["provenance"][0]["quote"] = "words the person never said in this interview"
    si = read_export(data)
    assert [f.trust.grade for f in si.features[:3]] == ["default", "default", "locked"]
    assert not si.features[0].user_evidenced and not si.features[1].user_evidenced
    assert si.features[1].tier == "locked", "Compass said Locked: the reader just does not agree"
    assert si.features[2].user_evidenced and si.features[2].quote_ids


def test_a_trimmed_fixture_without_provenance_or_interview_has_unchecked_default_features() -> None:
    si = read_export(small())
    assert "quotes_unchecked" in si.drift_flags
    assert all(f.trust.grade == "default" and not f.user_evidenced for f in si.features)


def test_a_low_confidence_locked_geography_is_default() -> None:
    data = standard()
    assert read_export(data).geography.trust.grade == "locked"
    data["brief"]["context"]["geo"]["confidence"] = 0.4
    si = read_export(data)
    assert si.geography.trust.grade == "default" and si.geography.trust.confidence == pytest.approx(
        0.4
    )
    assert si.geography.uncertain and "geo_uncertain" in si.flags
