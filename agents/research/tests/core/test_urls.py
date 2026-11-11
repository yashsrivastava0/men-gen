import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_research_agent.core.urls import (
    canonical_url,
    host_of,
    is_public_url,
    public_url_problem,
    registrable_domain,
)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("HTTPS://Example.COM:443/a/b/?utm_source=x&b=2&a=1#frag", "https://example.com/a/b?a=1&b=2"),
        ("http://www.example.com", "http://example.com/"),
        ("https://example.com/a/?gclid=abc", "https://example.com/a"),
        ("https://example.com/p?fbclid=1&id=7", "https://example.com/p?id=7"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        ("https://bücher.example/straße", "https://xn--bcher-kva.example/stra%C3%9Fe"),
    ],
)
def test_canonical_url_examples(a: str, b: str) -> None:
    assert canonical_url(a) == b


@given(
    st.sampled_from(["http", "https", "HTTP"]),
    st.sampled_from(["example.com", "WWW.Example.com", "docs.example.co.in", "x.gov.in"]),
    st.sampled_from(["", "/", "/a", "/a/", "/a/b?x=1&utm_medium=m", "/a?b=2&a=1"]),
    st.sampled_from(["", "#top", "#a b"]),
)
def test_canonical_url_is_idempotent(scheme: str, host: str, path: str, frag: str) -> None:
    once = canonical_url(f"{scheme}://{host}{path}{frag}")
    assert canonical_url(once) == once


def test_canonical_url_leaves_non_urls_alone() -> None:
    assert canonical_url("  not a url ") == "not a url"
    assert canonical_url("ftp://example.com/x") == "ftp://example.com/x"


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://docs.stripe.com/payments", "stripe.com"),
        ("https://www.ey.co.in/en_in", "ey.co.in"),
        ("https://eway.nic.in/x", "eway.nic.in"),
        ("https://services.gst.gov.in/", "gst.gov.in"),
        ("https://a.b.example.org", "example.org"),
        ("stripe.com", "stripe.com"),
        ("docs.razorpay.com", "razorpay.com"),
    ],
)
def test_registrable_domain(url: str, domain: str) -> None:
    assert registrable_domain(url) == domain


def test_two_subdomains_are_one_domain_and_two_co_in_sites_are_two() -> None:
    assert registrable_domain("https://docs.stripe.com") == registrable_domain("https://stripe.com/a")
    assert registrable_domain("https://a.co.in") != registrable_domain("https://b.co.in")


def test_host_of_strips_www() -> None:
    assert host_of("https://WWW.Example.com/x") == "example.com"
    assert host_of("garbage") == ""


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://127.0.0.1/",
        "http://127.1/",  # short dotted form
        "http://2130706433/",  # decimal
        "http://0x7f000001/",  # hex
        "http://017700000001/",  # octal
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://172.16.4.4/",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://0.0.0.0/",
        "https://printer.local/",
        "https://intranet/x",
        "https://host.internal/x",
        "https://user:pw@example.com/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "http://example.com:22/",
        "javascript:alert(1)",
        "",
    ],
)
def test_private_or_odd_urls_are_not_public(url: str) -> None:
    assert not is_public_url(url), url
    assert public_url_problem(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://example.com:8080/x",
        "https://docs.stripe.com/api",
        "https://93.184.216.34/",  # a public literal
        "https://eway.nic.in",
    ],
)
def test_public_urls(url: str) -> None:
    assert is_public_url(url), url
