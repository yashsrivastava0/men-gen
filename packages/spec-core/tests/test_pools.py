"""Naughty-strings allow-list (FN-10), name and place pools (FN-09), masking and the fixture deny rules (FN-11, FN-12)."""

import random
import re

import pytest
import yaml
from stdnum import verhoeff
from stdnum.in_ import aadhaar, gstin, pan

from mde_spec_core import mock
from mde_spec_core.config import default_config
from mde_spec_core.mock import (
    BLNS_DIR,
    POOLS_DIR,
    BlnsError,
    FixtureSafetyError,
    LocaleFallbackError,
    assert_fixture_safe,
    assert_script_share,
    blns_meta,
    blns_sections,
    find_unsafe,
    format_money,
    format_number,
    load_allowed_strings,
    mask_id,
    mask_phone,
    parse_blns,
    pool_for_language,
    script_for_language,
    script_share,
)

ALLOWED = [
    "Reserved Strings",
    "Numeric Strings",
    "Special Characters",
    "Unicode Symbols",
    "Quotation Marks",
    "Two-Byte Characters",
    "Emoji",
    "Right-To-Left Strings",
    "Zalgo Text",
    "Scunthorpe Problem",
]


# ------------------------------------------------------------------ the pinned naughty-strings file (FN-10)


def test_blns_is_pinned_with_commit_and_licence():
    meta = blns_meta()
    assert re.fullmatch(r"[0-9a-f]{40}", meta["pinned_commit"])
    assert meta["licence"] == "MIT"
    assert meta["pinned_commit"] in meta["raw_url"]
    assert (BLNS_DIR / meta["file"]).exists()


def test_blns_headings_are_what_the_meta_file_records():
    """FN-10: a test FAILS if the pinned file's headings change."""
    titles = [s.title for s in blns_sections()]
    assert titles == blns_meta()["headings"]
    assert len(titles) == 35


def test_a_changed_heading_is_detected():
    text = (BLNS_DIR / blns_meta()["file"]).read_text(encoding="utf-8")
    changed = parse_blns(text.replace("#\tEmoji\n", "#\tEmoji Strings\n", 1))
    assert [s.title for s in changed] != blns_meta()["headings"]
    assert "Emoji Strings" in [s.title for s in changed]


def test_a_file_that_is_not_the_pinned_one_is_refused(monkeypatch):
    mock.blns_sections.cache_clear()
    mock.load_allowed_strings.cache_clear()
    meta = dict(blns_meta(), sha256="0" * 64)
    monkeypatch.setattr(mock, "blns_meta", lambda: meta)
    with pytest.raises(BlnsError):
        mock.blns_sections()
    mock.blns_sections.cache_clear()
    mock.load_allowed_strings.cache_clear()


def test_the_allow_list_is_exactly_the_ten_categories():
    assert blns_meta()["allowed_categories"] == ALLOWED
    allowed = load_allowed_strings()
    assert list(allowed) == ALLOWED
    assert all(len(v) >= 1 for v in allowed.values())


def test_no_excluded_category_enters_the_allowed_strings():
    allowed = {s for v in load_allowed_strings().values() for s in v}
    excluded = [s for s in blns_sections() if s.title not in ALLOWED]
    injection = {
        "Script Injection",
        "SQL Injection",
        "Server Code Injection",
        "Command Injection (Ruby)",
        "XXE Injection (XML)",
        "Unwanted Interpolation",
        "File Inclusion",
        "jinja2 injection",
        "Known CVEs and Vulnerabilities",
        "Terminal escape codes",
        "iOS Vulnerabilities",
        "MSDOS/Windows Special Filenames",
        "Trick Unicode",
        "Special Unicode Characters Union",
        "Human injection",
        "IRC specific strings",
    }
    assert injection <= {s.title for s in excluded}
    leaked = [
        (s.title, e) for s in excluded if s.title in injection for e in s.entries if e in allowed
    ]
    # a string that is also listed under an allowed heading is allowed there; none may be dangerous
    for _title, e in leaked:
        assert not re.search(r"<script|DROP TABLE|\x1b|\$\{|\{\{|/etc/passwd|\.\./|<!ENTITY", e), e


def test_allowed_strings_have_no_control_characters_urls_or_long_digit_runs():
    n = default_config().checks.digit_run_deny_length
    for strings in load_allowed_strings().values():
        for s in strings:
            assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in s)
            assert not mock.looks_like_url(s)
            assert not re.search(rf"\d{{{n},}}", s)
            assert find_unsafe([s]) == []


# ------------------------------------------------------------------ pools (FN-09)


def test_kannada_pool_is_curated_and_in_kannada_script():
    data = yaml.safe_load((POOLS_DIR / "kn_IN.yaml").read_text(encoding="utf-8"))
    assert data["needs_native_review"] is True
    assert len(data["people"]) >= 60
    assert len(data["places"]) >= 40
    assert len(set(data["people"])) == len(data["people"])
    assert script_share(data["people"] + data["places"], "Kannada") == 1.0
    pool = pool_for_language("kn")
    assert pool.needs_native_review and pool.script == "Kannada"


def test_a_fixture_whose_names_fell_back_to_english_fails():
    english = ["Ravi Kumar", "Anita Rao", "Mysore", "Hubli"]
    assert script_share(english, "Kannada") == 0.0
    with pytest.raises(LocaleFallbackError):
        assert_script_share(english, "Kannada")
    mixed = ["ರಾಜೇಶ್ ಗೌಡ", "Ravi Kumar", "Anita Rao", "Mysore"]
    with pytest.raises(LocaleFallbackError):
        assert_script_share(mixed, "Kannada")  # 25% is under the configured share


def test_script_share_ignores_digits_and_punctuation():
    assert script_share(["१२३ ४५"], "Devanagari") == 0.0  # no letters at all: skipped
    assert script_share(["पुणे 411"], "Devanagari") == 1.0


@pytest.mark.parametrize(
    ("language", "script"),
    [
        ("mr", "Devanagari"),
        ("hi", "Devanagari"),
        ("en", "Latin"),
        ("kn", "Kannada"),
        ("ar", "Arabic"),
        ("fr", "Latin"),
        ("sw", "Latin"),
        ("es", "Latin"),
    ],
)
def test_every_language_has_a_pool_in_its_own_script(language, script):
    pool = pool_for_language(language)
    assert pool.script == script == script_for_language(language)
    assert (
        script_share(list(pool.people) + list(pool.places), script)
        >= default_config().checks.locale_script_share_min
    )
    assert len(pool.people) >= 20 and len(pool.places) >= 10


def test_faker_pools_are_seeded_and_only_use_person_and_city_providers():
    assert pool_for_language("mr").people == pool_for_language("mr").people
    called = set(mock._faker_lists.__code__.co_names) | {
        n for c in mock._faker_lists.__code__.co_consts if isinstance(c, str) for n in [c]
    }
    for banned in ("ssn", "bank_country", "phone_number", "bban", "iban", "credit_card_number"):
        assert banned not in called
    assert {"first_name", "last_name", "city"} <= called
    assert pool_for_language("mr").source == "faker" and pool_for_language("mr").key == "mr_IN"


def test_pool_draws_are_deterministic_for_a_seed():
    p = pool_for_language("hi")
    assert p.pick_people(30, random.Random(9)) == p.pick_people(30, random.Random(9))
    assert p.pick_people(30, random.Random(9)) != p.pick_people(30, random.Random(10))
    assert (
        len(set(p.pick_places(10, random.Random(1)))) == 10
    )  # no repeat before the pool is used up


def test_rtl_pool_is_marked_right_to_left():
    assert pool_for_language("ar").rtl is True


def test_indian_digit_grouping_for_money_and_numbers():
    en = pool_for_language("en")
    assert format_money(1234567.891, en).replace("\xa0", "") == "₹12,34,567.89"
    assert format_number(1234567, en) == "12,34,567"


@pytest.mark.parametrize("language", ["en", "hi", "mr", "kn"])
def test_money_and_numbers_group_the_same_way_for_every_indian_language(language):
    pool = pool_for_language(language)
    assert format_money(1234567, pool) == "₹12,34,567.00"
    assert format_money(-1234567, pool) == "-₹12,34,567.00"
    assert format_number(1234567, pool) == "12,34,567"


def test_money_keeps_the_locale_pattern_outside_the_digits():
    ar, fr = pool_for_language("ar"), pool_for_language("fr")
    assert format_money(1234567, ar).startswith(
        "\u200f1,234,567.00"
    )  # the locale's own place of the symbol
    assert "12,34,567" not in format_number(1234567, fr) and "," not in format_number(1234567, fr)


# ------------------------------------------------------------------ masking (FN-11)


def test_mask_phone_shows_first_two_and_last_three_digits():
    assert mask_phone("+919876543210") == "+91 98••• ••210"
    assert mask_phone("+91 98765 43210") == "+91 98••• ••210"
    assert "9876543210" not in mask_phone("9876543210")


def test_masked_phone_is_safe_and_the_whole_number_is_not():
    assert_fixture_safe([mask_phone("+919876543210")])
    with pytest.raises(FixtureSafetyError):
        assert_fixture_safe(["+91 98765 43210"])


def test_mask_id_keeps_only_the_last_four():
    assert mask_id("2345 6789 0123") == "•••• 0123"
    assert_fixture_safe([mask_id("2345 6789 0123")])


# ------------------------------------------------------------------ deny rules (FN-11, FN-12)


def _valid_aadhaar() -> str:
    body = "234567890123"[:11]
    return body + verhoeff.calc_check_digit(body)


def test_an_aadhaar_that_passes_the_checksum_fails_the_fixture():
    number = _valid_aadhaar()
    assert aadhaar.is_valid(number)
    spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
    v = find_unsafe([f"Aadhaar {spaced}"])
    assert any(x.kind == "id_validates" and "Aadhaar" in x.detail for x in v)
    assert spaced not in str(v)  # the report never repeats the value
    with pytest.raises(FixtureSafetyError):
        assert_fixture_safe([f"Aadhaar {spaced}"])


def test_a_twelve_digit_number_that_fails_the_checksum_still_fails_on_the_digit_run():
    bad = next(n for n in (f"2345678901{i:02d}" for i in range(100)) if not aadhaar.is_valid(n))
    assert any(v.kind == "digit_run" for v in find_unsafe([bad]))


def test_pan_and_gstin_that_validate_fail():
    assert pan.is_valid("ABCPE1234F")
    assert any(v.kind == "id_validates" for v in find_unsafe(["PAN ABCPE1234F"]))
    sample = "27AAPFU0939F1ZV"
    if gstin.is_valid(sample):
        assert any(v.kind == "id_validates" for v in find_unsafe([sample]))


def test_a_pan_shaped_value_that_fails_validation_is_not_flagged_as_an_id():
    assert not any(
        v.kind == "id_validates" for v in find_unsafe(["ABCZE1234F"])
    )  # no such holder type


def test_digit_runs_of_nine_or_more_fail_eight_pass():
    n = default_config().checks.digit_run_deny_length
    assert n == 9
    assert find_unsafe(["12345678"]) == []
    assert any(v.kind == "digit_run" for v in find_unsafe(["123456789"]))
    assert any(v.kind == "digit_run" for v in find_unsafe(["१२३४५६७८९०"]))  # Devanagari digits too


@pytest.mark.parametrize("text", ["+91 98765 43210", "98765 43210", "+1 650 253 0000"])
def test_unmasked_phone_numbers_fail(text):
    assert any(v.kind == "phone_unmasked" for v in find_unsafe([text]))


def test_money_dates_and_ordinary_text_are_safe():
    assert_fixture_safe(
        ["₹12,34,567.89", "now-12d", "Order 12 of 40", "Nashik, Maharashtra", "2026-11-14"]
    )


@pytest.mark.parametrize(
    "text", ["MH 15 AB 1234", "KA03MN4567".replace("KA03MN4567", "KA 03 MN 4567")]
)
def test_plate_like_values_fail(text):
    assert any(v.kind == "plate_like" for v in find_unsafe([text]))


def test_emails_must_be_at_example_com():
    assert find_unsafe(["demo.farmer@example.com"]) == []
    assert any(v.kind == "email_outside_example" for v in find_unsafe(["ravi@gmail.com"]))
    assert any(v.kind == "email_outside_example" for v in find_unsafe(["ravi@example.org"]))


def test_unmasked_upi_like_ids_fail_and_masked_ones_pass():
    assert any(v.kind == "upi_like_id" for v in find_unsafe(["pay ramesh@okhdfcbank now"]))
    assert find_unsafe(["ra•••@okhdfcbank"]) == []
    assert not any(v.kind == "upi_like_id" for v in find_unsafe(["ramesh@example.com"]))


def test_real_brands_fail_unless_the_user_gave_the_name():
    assert any(v.kind == "real_brand" for v in find_unsafe(["Fresh from Amazon"]))
    assert find_unsafe(["Tata Tea Stall"], allowed_names=["Tata Tea Stall"]) == []
    assert any(v.kind == "real_brand" for v in find_unsafe(["Tata Tea Stall"]))


def test_urls_fail():
    assert any(v.kind == "url" for v in find_unsafe(["see https://x.test/a.jpg"]))


def test_assert_fixture_safe_walks_nested_structures():
    with pytest.raises(FixtureSafetyError) as err:
        assert_fixture_safe({"rows": [{"fields": {"phone": "+91 98765 43210"}}]})
    assert err.value.violations
    assert_fixture_safe({"rows": [{"fields": {"phone": mask_phone("+919876543210")}}], "n": 3})
