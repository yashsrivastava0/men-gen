"""The shared helpers of the search adapters: dates, Retry-After, domains, data files, the key holder."""

from __future__ import annotations

import pickle
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime

import pytest

from mde_research_agent.adapters.search.common import (
    Secret,
    check_key,
    clean_query,
    country_info,
    load_countries,
    load_prices,
    normalize_country_code,
    normalize_domains,
    normalize_language,
    parse_published,
    parse_retry_after,
    price_int,
    quote_phrase,
    redact,
    subtract_months,
)
from mde_research_agent.ports.search import SearchError

TODAY = date(2026, 11, 11)

# The `country` enum of the official Tavily Search OpenAPI spec, opened 2026-11-11.
TAVILY_COUNTRY_ENUM = frozenset(
    {
        "afghanistan", "albania", "algeria", "andorra", "angola", "argentina", "armenia", "australia",
        "austria", "azerbaijan", "bahamas", "bahrain", "bangladesh", "barbados", "belarus", "belgium",
        "belize", "benin", "bhutan", "bolivia", "bosnia and herzegovina", "botswana", "brazil", "brunei",
        "bulgaria", "burkina faso", "burundi", "cambodia", "cameroon", "canada", "cape verde",
        "central african republic", "chad", "chile", "china", "colombia", "comoros", "congo",
        "costa rica", "croatia", "cuba", "cyprus", "czech republic", "denmark", "djibouti",
        "dominican republic", "ecuador", "egypt", "el salvador", "equatorial guinea", "eritrea",
        "estonia", "ethiopia", "fiji", "finland", "france", "gabon", "gambia", "georgia", "germany",
        "ghana", "greece", "guatemala", "guinea", "haiti", "honduras", "hungary", "iceland", "india",
        "indonesia", "iran", "iraq", "ireland", "israel", "italy", "jamaica", "japan", "jordan",
        "kazakhstan", "kenya", "kuwait", "kyrgyzstan", "latvia", "lebanon", "lesotho", "liberia",
        "libya", "liechtenstein", "lithuania", "luxembourg", "madagascar", "malawi", "malaysia",
        "maldives", "mali", "malta", "mauritania", "mauritius", "mexico", "moldova", "monaco",
        "mongolia", "montenegro", "morocco", "mozambique", "myanmar", "namibia", "nepal",
        "netherlands", "new zealand", "nicaragua", "niger", "nigeria", "north korea",
        "north macedonia", "norway", "oman", "pakistan", "panama", "papua new guinea", "paraguay",
        "peru", "philippines", "poland", "portugal", "qatar", "romania", "russia", "rwanda",
        "saudi arabia", "senegal", "serbia", "singapore", "slovakia", "slovenia", "somalia",
        "south africa", "south korea", "south sudan", "spain", "sri lanka", "sudan", "sweden",
        "switzerland", "syria", "taiwan", "tajikistan", "tanzania", "thailand", "togo",
        "trinidad and tobago", "tunisia", "turkey", "turkmenistan", "uganda", "ukraine",
        "united arab emirates", "united kingdom", "united states", "uruguay", "uzbekistan",
        "venezuela", "vietnam", "yemen", "zambia", "zimbabwe",
    }
)  # fmt: skip


# ---------------------------------------------------------------------------------------------
# countries.yaml and provider_prices.yaml
# ---------------------------------------------------------------------------------------------


def test_countries_file_shape_and_coverage() -> None:
    countries = load_countries()
    assert len(countries) >= 60
    for code in (
        "in",
        "ae",
        "ke",
        "gb",
        "us",
        "ng",
        "za",
        "eg",
        "sa",
        "pk",
        "bd",
        "lk",
        "sg",
        "au",
    ):
        assert code in countries
    for code in ("ca", "de", "fr", "br", "id"):
        assert code in countries
    for code, info in countries.items():
        assert code == code.lower() and len(code) == 2
        assert info.code == code
        assert info.name is None or info.name == info.name.lower()  # None: not in Tavily's enum
        assert info.tld and "." not in info.tld
        assert len(info.language) == 2


def test_every_country_name_is_in_the_tavily_enum() -> None:
    unknown = {c.name for c in load_countries().values() if c.name} - TAVILY_COUNTRY_ENUM
    assert not unknown


def test_country_lookup_forgives_case_and_rejects_non_codes() -> None:
    assert country_info("IN") is not None
    assert country_info(" in ") is not None
    assert country_info("in").name == "india"  # type: ignore[union-attr]
    assert country_info("gb").name == "united kingdom"  # type: ignore[union-attr]
    assert country_info("zz") is None
    assert country_info("india") is None
    assert country_info("") is None
    assert country_info(None) is None


def test_prices_are_integer_micros_and_flagged() -> None:
    prices = load_prices()
    for provider in ("tavily", "serper"):
        entry = prices[provider]
        assert isinstance(entry["micros_per_credit"], int)
        assert not isinstance(entry["micros_per_credit"], bool)
        assert isinstance(entry["verified"], bool)
    assert price_int(prices["tavily"], "micros_per_credit") == 8000  # USD 0.008 per credit
    assert price_int(prices["tavily"], "credits_per_search", "basic") == 1
    assert price_int(prices["tavily"], "credits_per_search", "advanced") == 2
    assert price_int(prices["serper"], "micros_per_credit") == 1000  # USD 1.00 per 1,000
    assert prices["serper"]["credits_when_num_over_10_verified"] is False  # third-party only


def test_price_int_refuses_floats_and_negatives() -> None:
    with pytest.raises(ValueError, match="non-negative int"):
        price_int({"x": 1.5}, "x")
    with pytest.raises(ValueError, match="non-negative int"):
        price_int({"x": -1}, "x")
    with pytest.raises(ValueError, match="non-negative int"):
        price_int({"x": True}, "x")


# ---------------------------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------------------------

KEY = "tvly-NOT-A-REAL-KEY-0001"  # secret-scan: allow


def test_secret_never_prints_or_pickles() -> None:
    secret = Secret(KEY)
    assert KEY not in repr(secret)
    assert KEY not in str(secret)
    assert KEY not in f"{secret!r} {secret}"
    assert secret.reveal() == KEY
    with pytest.raises(TypeError):
        pickle.dumps(secret)


def test_redact_removes_the_key_and_flattens() -> None:
    text = redact(f"bad key {KEY}\n  and {KEY} again", Secret(KEY))
    assert KEY not in text
    assert text == "bad key [redacted] and [redacted] again"
    assert len(redact("x" * 1000, None)) == 200


@pytest.mark.parametrize("bad", ["", "   ", "key with space", "key\nnewline", "kéy", "key\x00"])
def test_check_key_refuses_unusable_keys_without_quoting_them(bad: str) -> None:
    with pytest.raises(SearchError) as info:
        check_key(Secret(bad))
    assert info.value.kind == "auth"
    assert bad.strip() == "" or bad not in str(info.value)


def test_check_key_returns_a_clean_key() -> None:
    assert check_key(Secret(KEY)) == KEY


# ---------------------------------------------------------------------------------------------
# Retry-After
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("7", 7.0), ("0", 0.0), ("2.5", 2.5), (" 30 ", 30.0), ("999999999", 86_400.0)],
)
def test_retry_after_seconds(value: str, expected: float) -> None:
    assert parse_retry_after(value) == expected


def test_retry_after_http_date() -> None:
    now = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    later = format_datetime(now + timedelta(seconds=90), usegmt=True)
    earlier = format_datetime(now - timedelta(seconds=90), usegmt=True)
    assert parse_retry_after(later, now=now) == 90.0
    assert parse_retry_after(earlier, now=now) == 0.0  # never negative
    assert parse_retry_after("Wed, 11 Nov 2026 12:01:30 GMT", now=now) == 90.0


@pytest.mark.parametrize(
    "value", [None, "", "  ", "soon", "-5", "nan", "inf", "1e999", "1_000", "12 s"]
)
def test_retry_after_unreadable_is_none(value: str | None) -> None:
    assert parse_retry_after(value) is None


# ---------------------------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025-03-12", date(2025, 3, 12)),
        ("2025-03-12T10:30:00Z", date(2025, 3, 12)),
        ("2025-03-12 10:30:00", date(2025, 3, 12)),
        ("2025-03-12T23:30:00-08:00", date(2025, 3, 12)),
        ("Mar 12, 2025", date(2025, 3, 12)),
        ("March 12, 2025", date(2025, 3, 12)),
        ("Sept 3, 2024", date(2024, 9, 3)),
        ("12 Mar 2025", date(2025, 3, 12)),
        ("1st March 2025", date(2025, 3, 1)),
        ("Mon, 20 Jan 2025 13:00:00 GMT", date(2025, 1, 20)),
        ("3 days ago", date(2026, 11, 8)),
        ("1 day ago", date(2026, 11, 10)),
        ("a day ago", date(2026, 11, 10)),
        ("2 weeks ago", date(2026, 10, 28)),
        ("1 month ago", date(2026, 10, 11)),
        ("3 months ago", date(2026, 8, 11)),
        ("a year ago", date(2025, 11, 11)),
        ("2 years ago", date(2024, 11, 11)),
        ("5 hours ago", TODAY),
        ("an hour ago", TODAY),
        ("30 minutes ago", TODAY),
        ("yesterday", date(2026, 11, 10)),
        ("today", TODAY),
        ("  3   Days   Ago ", date(2026, 11, 8)),
    ],
)
def test_parse_published_understood_forms(text: str, expected: date) -> None:
    assert parse_published(text, TODAY) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        42,
        ["2025-03-12"],
        "someday",
        "Mar 2025",
        "2025-13-45",
        "Foo 12, 2025",
        "99999 days ago",
        "3 दिन पहले",  # a localised relative date is not guessed
        "2027-01-01",  # more than a day in the future
        "10000 years ago",
        "garbage: 12:00",
    ],
)
def test_parse_published_unparseable_is_none(value: object) -> None:
    assert parse_published(value, TODAY) is None


def test_parse_published_allows_tomorrow_for_timezone_skew() -> None:
    assert parse_published("2026-11-12", TODAY) == date(2026, 11, 12)


def test_subtract_months_clamps_the_day() -> None:
    assert subtract_months(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert subtract_months(date(2024, 3, 31), 1) == date(2024, 2, 29)
    assert subtract_months(date(2026, 1, 15), 2) == date(2025, 11, 15)
    assert subtract_months(date(2026, 11, 11), 12) == date(2025, 11, 11)
    assert subtract_months(date(2026, 11, 11), 0) == date(2026, 11, 11)


# ---------------------------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------------------------


def test_normalize_domains() -> None:
    got = normalize_domains(
        [
            "https://www.Example.com/path?x=1",
            "example.com",  # duplicate of the first once normalised
            "docs.stripe.com:443",
            "*.gov.in",
            "user@host.example.org",
            "bad domain!!",
            "a b.com",
            "",
            "localhost",
            "1.2.3.4",
            "-bad-.com",
            "x(y).com",
            'quo"te.com',
            "münchen.de",
        ],
        limit=50,
    )
    assert got == (
        "example.com",
        "docs.stripe.com",
        "gov.in",
        "host.example.org",
        "xn--mnchen-3ya.de",
    )


def test_normalize_domains_limit_and_non_strings() -> None:
    domains = [f"site{i}.com" for i in range(20)]
    assert normalize_domains(domains, limit=3) == ("site0.com", "site1.com", "site2.com")
    assert normalize_domains([None, 3, "a.com"], limit=5) == ("a.com",)  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("hi", ("hi", None)),
        ("HI", ("hi", None)),
        ("zh-CN", ("zh", "cn")),
        ("en_US", ("en", "us")),
        ("fil", ("fil", None)),
        ("hi-Latn", None),
        ("english", None),
        ("x", None),
        ("", None),
        (None, None),
        ("hi hi", None),
    ],
)
def test_normalize_language(value: str | None, expected: tuple[str, str | None] | None) -> None:
    assert normalize_language(value) == expected


def test_normalize_country_code() -> None:
    assert normalize_country_code("IN") == "in"
    assert normalize_country_code(" gb ") == "gb"
    assert normalize_country_code("india") is None
    assert normalize_country_code("i") is None
    assert normalize_country_code("") is None
    assert normalize_country_code(None) is None


def test_clean_query_and_quote_phrase() -> None:
    assert clean_query("  best \t crm\n\x00 for   india ") == "best crm for india"
    assert clean_query(None) == ""
    # a query that quotes a phrase keeps it: only that phrase must match as written
    assert quote_phrase('say "hello"   world') == 'say "hello" world'
    assert quote_phrase('"Mindbody" features pricing') == '"Mindbody" features pricing'
    # no quoted phrase, or unbalanced quotes: the whole query is one phrase
    assert quote_phrase("rule 86B limits") == '"rule 86B limits"'
    assert quote_phrase('say "hello world') == '"say hello world"'
    with pytest.raises(SearchError) as info:
        quote_phrase('" "')
    assert info.value.kind == "bad_request"
