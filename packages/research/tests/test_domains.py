"""Domain helpers: registrable domains, allow and block rules, safe source urls."""

from __future__ import annotations

import pytest

from mde_research.domains import (
    domain_allowed,
    domain_matches,
    host_of,
    normalize_rule,
    registrable_domain,
    safe_http_url,
    url_key,
)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("example.com", "example.com"),
        ("www.example.com", "example.com"),
        ("a.b.example.com", "example.com"),
        ("docs.example.co.uk", "example.co.uk"),
        ("example.co.uk", "example.co.uk"),
        ("shop.example.com.au", "example.com.au"),
        ("portal.gov.in", "portal.gov.in"),
        ("localhost", "localhost"),
        ("10.0.0.1", "10.0.0.1"),
        ("EXAMPLE.org.", "example.org"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected


def test_independent_subdomains_share_a_registrable_domain() -> None:
    assert registrable_domain("a.example.com") == registrable_domain("b.example.com")
    assert registrable_domain("example.com") != registrable_domain("example.org")


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("Docs.Expo.dev", "docs.expo.dev"),
        ("https://www.owasp.org/path", "owasp.org"),
        ("*.nist.gov", "nist.gov"),
        (".reactnative.dev", "reactnative.dev"),
        ("example.com:8080/x", "example.com"),
        ("  ", ""),
    ],
)
def test_normalize_rule(rule: str, expected: str) -> None:
    assert normalize_rule(rule) == expected


def test_domain_matches_subdomains_but_not_lookalikes() -> None:
    assert domain_matches("docs.expo.dev", "expo.dev")
    assert domain_matches("expo.dev", "expo.dev")
    assert domain_matches("www.expo.dev", "expo.dev")
    assert not domain_matches("notexpo.dev", "expo.dev")
    assert not domain_matches("expo.dev.evil.example", "expo.dev")
    assert not domain_matches("expo.dev", "")


def test_blocked_beats_allowed_and_an_allow_list_restricts() -> None:
    assert domain_allowed("a.example.com", [], [])
    assert not domain_allowed("a.example.com", [], ["example.com"])
    assert domain_allowed("docs.expo.dev", ["expo.dev"], [])
    assert not domain_allowed("example.com", ["expo.dev"], [])
    assert not domain_allowed("docs.expo.dev", ["expo.dev"], ["docs.expo.dev"])
    assert domain_allowed("example.com", ["", " "], [])  # empty rules mean no restriction


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a?b=1",
        "http://example.com",
        "https://docs.example.co.uk/guide",
        "https://example.com:8443/x",
    ],
)
def test_safe_urls_pass(url: str) -> None:
    assert safe_http_url(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "",
        "javascript:alert(1)",
        "data:text/html,hi",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "https://user:pw@example.com/",
        "https://localhost/x",
        "https://127.0.0.1/x",
        "https://192.168.1.5/x",
        "https://10.0.0.2/x",
        "https://[::1]/x",
        "https://intranet/x",
        "https://host.internal/x",
        "https://exa mple.com/x",
        "https://example.com/" + "a" * 600,
        "https://example.com/<script>",
        "https://example.com/\x00",
        "https://-bad-.example.com/",
        "notaurl",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    assert safe_http_url(url) is None


def test_safe_url_drops_the_fragment_and_default_port() -> None:
    assert safe_http_url("https://Example.com:443/a#frag") == "https://example.com/a"
    assert safe_http_url("https://example.com") == "https://example.com/"


def test_host_of() -> None:
    assert host_of("https://Docs.Example.com:8080/x") == "docs.example.com"
    assert host_of("not a url") is None
    assert host_of("https:///path") is None


def test_url_key_ignores_scheme_fragment_trailing_slash_and_www() -> None:
    assert url_key("https://www.example.com/a/") == url_key("http://example.com/a#x")
    assert url_key("https://example.com/a?x=1") != url_key("https://example.com/a")
