import re
from decimal import Decimal

import pytest

from mde_spec_core.policy import (
    PolicySheet,
    default_policy_sheet,
    load_policy_sheet,
    parse_policy_ref,
    policy_ref,
    policy_ref_is_current,
)

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_TOPICS = [
    "POL-APPLE-DELETION",
    "POL-APPLE-LOGIN-EQUIVALENT",
    "POL-APPLE-IAP",
    "POL-APPLE-PURPOSE-STRINGS",
    "POL-APPLE-PRIVACY-LABELS",
    "POL-APPLE-PRIVACY-MANIFEST-REASONS",
    "POL-APPLE-PUSH",
    "POL-APPLE-UGC",
    "POL-APPLE-DEMO-ACCOUNT",
    "POL-APPLE-AGE-RATING",
    "POL-GOOGLE-DELETION",
    "POL-GOOGLE-DATA-SAFETY",
    "POL-GOOGLE-PAYMENTS",
    "POL-GOOGLE-BACKGROUND-LOCATION",
    "POL-GOOGLE-UGC",
    "POL-GOOGLE-REVIEW-ACCESS",
    "POL-GOOGLE-CONTENT-RATING",
    "POL-ANDROID-NOTIFICATION-PERMISSION",
    "POL-A11Y-CONTRAST-BODY",
    "POL-A11Y-CONTRAST-LARGE",
    "POL-A11Y-TARGET-IOS",
    "POL-A11Y-TARGET-ANDROID",
    "POL-A11Y-TEXT-SCALE-200",
]


def test_sheet_loads_and_is_dated():
    sheet = default_policy_sheet()
    assert sheet.version == 1
    assert ISO_DATE.match(sheet.checked_on)
    assert sheet.rules


def test_every_rule_has_source_and_date():
    for r in default_policy_sheet().rules:
        assert r.source_url.startswith("https://"), r.id
        assert ISO_DATE.match(r.checked_on), r.id
        assert r.statement.strip() and r.effect.detail.strip(), r.id
        assert r.id.startswith("POL-"), r.id


def test_policy_ids_are_unique():
    ids = [r.id for r in default_policy_sheet().rules]
    assert len(ids) == len(set(ids))


def test_required_topics_present():
    have = {r.id for r in default_policy_sheet().rules}
    assert not set(REQUIRED_TOPICS) - have


def test_predicates_use_only_declared_facts():
    sheet = default_policy_sheet()
    declared = {f.id for f in sheet.facts}
    assert "always" in declared
    for r in sheet.rules:
        assert r.applies_when.facts_used() <= declared, r.id


def test_duplicate_id_or_undeclared_fact_is_refused(tmp_path):
    import yaml

    from mde_spec_core.policy import POLICY_PATH

    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    dup = dict(raw)
    dup["rules"] = [*raw["rules"], raw["rules"][0]]
    with pytest.raises(ValueError):
        PolicySheet.model_validate(dup)
    bad = dict(raw)
    rule = dict(raw["rules"][0])
    rule["applies_when"] = {"fact": "no_such_fact"}
    bad["rules"] = [rule]
    with pytest.raises(ValueError):
        PolicySheet.model_validate(bad)
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_policy_sheet(p).rules


def test_predicate_must_have_exactly_one_form():
    from mde_spec_core.policy import Predicate

    with pytest.raises(ValueError):
        Predicate.model_validate({"fact": "always", "not": {"fact": "always"}})
    with pytest.raises(ValueError):
        Predicate.model_validate({})


def test_applicability_follows_facts():
    sheet = default_policy_sheet()
    plain = {r.id for r in sheet.applicable({})}
    assert "POL-APPLE-DELETION" not in plain
    assert "POL-A11Y-CONTRAST-BODY" in plain  # `always`
    with_accounts = {r.id for r in sheet.applicable({"accounts_created_in_app": True})}
    assert {"POL-APPLE-DELETION", "POL-GOOGLE-DELETION"} <= with_accounts
    apple_only = {r.platform for r in sheet.applicable({"accounts_created_in_app": True}, "apple")}
    assert apple_only <= {"apple", "standard"}
    login = {
        "uses_third_party_login": True,
        "login_exempt_from_equivalent_option": True,
    }
    assert "POL-APPLE-LOGIN-EQUIVALENT" not in {r.id for r in sheet.applicable(login)}
    login["login_exempt_from_equivalent_option"] = False
    assert "POL-APPLE-LOGIN-EQUIVALENT" in {r.id for r in sheet.applicable(login)}


def test_accessibility_floors_have_the_numbers_the_standards_state():
    floors = {r.id: r.floor for r in default_policy_sheet().floors()}
    assert Decimal(floors["POL-A11Y-CONTRAST-BODY"].value) == Decimal("4.5")
    assert Decimal(floors["POL-A11Y-CONTRAST-LARGE"].value) == Decimal("3.0")
    assert Decimal(floors["POL-A11Y-CONTRAST-NONTEXT"].value) == Decimal("3.0")
    assert Decimal(floors["POL-A11Y-TEXT-SCALE-200"].value) == 200
    assert Decimal(floors["POL-A11Y-TARGET-IOS"].value) == 44
    assert Decimal(floors["POL-A11Y-TARGET-IOS"].hard_minimum) == 28  # Apple's own stated minimum
    assert Decimal(floors["POL-A11Y-TARGET-ANDROID"].value) == 48
    assert Decimal(floors["POL-A11Y-TARGET-ANDROID"].spacing) == 8
    for f in floors.values():
        assert isinstance(f.value, str)  # exact decimals, never floats


def test_every_unverifiable_topic_is_listed_with_a_reason():
    sheet = default_policy_sheet()
    assert sheet.omitted_unverified
    for o in sheet.omitted_unverified:
        assert o.topic.strip() and len(o.reason) > 20


def test_policy_ref_round_trip():
    ref = policy_ref("POL-APPLE-DELETION")
    assert (
        ref
        == f"policy:POL-APPLE-DELETION@{default_policy_sheet().get('POL-APPLE-DELETION').checked_on}"
    )
    assert parse_policy_ref(ref) == ("POL-APPLE-DELETION", "2026-11-14")
    assert policy_ref_is_current(ref)
    assert not policy_ref_is_current("policy:POL-APPLE-DELETION@2020-01-01")
    assert not policy_ref_is_current("policy:POL-NOPE@2026-11-14")
    with pytest.raises(KeyError):
        policy_ref("POL-NOPE")
    for bad in ("POL-APPLE-DELETION", "policy:POL-X", "policy:@2026-11-14"):
        with pytest.raises(ValueError):
            parse_policy_ref(bad)
