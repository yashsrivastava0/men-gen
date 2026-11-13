"""Source classes by pattern, the authority registries, walled domains and the fusion of two engines (D-163)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_research_agent.catalog import load_catalog
from mde_research_agent.core.rrf import fuse, fuse_engines
from mde_research_agent.core.sources import (
    authority_language,
    classify_source,
    country_scopes,
    host_country,
    inferred_official,
    is_walled,
    matches_suffix_pattern,
)
from mde_research_agent.core.urls import registrable_domain
from mde_research_agent.ports.search import SearchHit

CAT = load_catalog()


# --- suffix patterns -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "pattern", "expected"),
    [
        ("www.example.gov", "gov", True),
        ("x.gov.in", "gov.*", True),
        ("x.gov.uk", "gov.*", True),
        ("x.gob.mx", "gob.*", True),
        ("x.gob.gt", "gob.*", True),
        ("x.gouv.sn", "gouv.*", True),
        ("x.go.ke", "go.*", True),
        ("x.go.cr", "go.*", True),
        ("x.go.com", "go.*", False),  # "com" is not a country code
        ("go.in", "go.*", False),  # the pattern alone is a suffix, not a site
        ("gov.in", "gov.*", False),
        ("x.europa.eu", "europa.eu", True),
        ("europa.eu", "europa.eu", False),
        ("x.bceao.int", "int", True),
        ("x.nic.in", "nic.in", True),
        ("x.gov.in.example.com", "gov.*", False),  # the pattern must be the END of the host
        ("x.ac.uk", "ac.*", True),
        ("government.example.org", "gov", False),
    ],
)
def test_suffix_patterns_match_the_end_of_the_host(host: str, pattern: str, expected: bool) -> None:
    assert matches_suffix_pattern(host, pattern) is expected


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz.-", min_size=0, max_size=40))
def test_pattern_matching_never_raises(host: str) -> None:
    for pattern in CAT.source_rules.official_suffix_patterns:
        matches_suffix_pattern(host, pattern)


def test_the_country_code_of_a_host_comes_from_the_country_data() -> None:
    assert host_country("x.gov.in") == "in" and host_country("x.gov.uk") == "gb"
    assert host_country("x.gov") is None and host_country("x.europa.eu") is None


@pytest.mark.parametrize(
    ("url", "country", "expected"),
    [
        ("https://some-ministry.gov.in/x", "in", "official_inferred"),
        ("https://some-ministry.gov.in/x", None, "official_inferred"),
        (
            "https://some-ministry.gov.br/x",
            "in",
            "blog",
        ),  # another country's ministry says nothing official about India
        ("https://some-ministry.gov.br/x", "br", "official_inferred"),
        ("https://portal.gob.mx/x", "mx", "official_inferred"),
        ("https://x.gouv.sn/x", "sn", "official_inferred"),
        ("https://x.go.cr/x", "cr", "official_inferred"),
        ("https://x.go.ke/x", "ke", "official_inferred"),
        ("https://x.nasa.gov/x", "in", "official_inferred"),  # .gov carries no country code
        ("https://x.europa.eu/x", "pt", "official_inferred"),
        ("https://x.some-body.int/x", "in", "official_inferred"),
        ("https://cnpd.pt/x", "pt", "official"),  # named in the registry: official, not inferred
        ("https://www.mohfw.gov.in/x", "in", "official"),
        ("https://eur-lex.europa.eu/x", "pt", "official"),  # the EU file applies to a member state
        ("https://eur-lex.europa.eu/x", "in", "official_inferred"),  # not a member: by pattern only
        ("https://x.gov.in.example.com/x", "in", "blog"),
    ],
)
def test_pattern_inference_and_the_registry(url: str, country: str | None, expected: str) -> None:
    assert classify_source(url, CAT, country=country) == expected


def test_a_providers_own_domain_is_official_inferred_only_for_entities_the_run_knows() -> None:
    url = "https://acmepay.com/pricing"
    assert classify_source(url, CAT) == "blog"
    assert classify_source(url, CAT, entities=["Acmepay"]) == "official_inferred"
    assert classify_source(url, CAT, entities=["Other Company"]) == "blog"
    assert classify_source(url, CAT, entity="Acmepay") == "official_inferred"


def test_inferred_official_is_not_a_registry_class_and_never_beats_it() -> None:
    assert not inferred_official("www.example.com", CAT, "in")
    assert inferred_official("x.gov.in", CAT, "in")
    assert classify_source("https://www.gst.gov.in/x", CAT, country="in") == "official"


def test_the_ladder_treats_official_inferred_as_official_but_keeps_the_class_visible() -> None:
    from mde_research_agent.assemble.items import evidence_of

    kwargs = {"verdicts": ["supported"], "flags": set(), "independent": 1, "independent_non_low": 1}
    assert evidence_of(classes=["official_inferred"], **kwargs) == "official"
    assert evidence_of(classes=["official"], **kwargs) == "official"
    assert evidence_of(classes=["blog"], **kwargs) == "single_source"


# --- the registry as data ----------------------------------------------------------------------------------------------------


COUNTRY_FILES = {"pt", "sn", "mx", "gt", "cr", "eu", "in", "ae", "ke", "global"}


def test_every_country_file_is_loaded_with_a_language_where_it_has_one() -> None:
    countries = {e.country for e in CAT.authority}
    assert countries >= COUNTRY_FILES
    for code in ("pt", "sn", "mx", "gt", "cr", "eu", "in", "ae", "ke"):
        assert authority_language(CAT, code) in CAT.languages, code
    assert [authority_language(CAT, c) for c in ("pt", "sn", "mx", "gt", "cr", "in")] == [
        "pt",
        "fr",
        "es",
        "es",
        "es",
        "en",
    ]
    assert authority_language(CAT, "br") == "pt" and authority_language(CAT, None) == "en"


def test_a_blocked_or_unresolved_domain_is_dated() -> None:
    for e in CAT.authority:
        if e.fetch != "ok":
            assert e.verified_on is not None, e.domain
            assert e.class_ == "official"


def test_verified_domains_are_present_and_the_rejected_one_is_not() -> None:
    have = {e.domain: e for e in CAT.authority}
    for domain in (
        "cnpd.pt",
        "bportugal.pt",
        "portaldasfinancas.gov.pt",
        "eur-lex.europa.eu",
        "cdp.sn",
        "bceao.int",
        "artp.sn",
        "dgid.sn",
        "stps.gob.mx",
        "dof.gob.mx",
        "sat.gob.mx",
        "mtss.go.cr",
        "ccss.sa.cr",
        "mintrabajo.gob.gt",
        "indiacode.nic.in",
        "egazette.gov.in",
        "consumeraffairs.gov.in",
        "msamb.com",
        "sfacindia.com",
        "nabard.org",
        "dot.gov.in",
        "irdai.gov.in",
        "abdm.gov.in",
        "nmc.org.in",
        "cdsco.gov.in",
        "mohfw.gov.in",
    ):
        assert domain in have, domain
    assert "arai.in" not in have, (
        "arai.in did not verify (TLS error; the body publishes at araiindia.com)"
    )
    assert have["indiacode.nic.in"].fetch == "blocked" and have["dof.gob.mx"].fetch == "unresolved"
    assert have["cnpd.pt"].fetch == "ok" and str(have["cnpd.pt"].verified_on) == "2026-11-13"


def test_registry_domains_are_registrable_names_and_unique_per_country() -> None:
    seen: set[tuple[str, str]] = set()
    for e in CAT.authority:
        assert e.domain == e.domain.lower() and "/" not in e.domain and " " not in e.domain, (
            e.domain
        )
        key = (e.country, e.domain)
        if e.country not in (
            "ae",
            "in",
            "ke",
        ):  # the older seed files repeat a domain with another topic list
            assert key not in seen, key
        seen.add(key)
        assert registrable_domain(f"https://{e.domain}/") or "." in e.domain


def test_a_bloc_file_applies_to_its_members() -> None:
    assert country_scopes("pt") == {"pt", "eu", "global"}
    assert country_scopes("in") == {"in", "global"}
    assert country_scopes(None) == {"global"}


# --- pages our reader cannot get -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "walled"),
    [
        ("https://www.indiacode.nic.in/handle/1", True),  # blocked in the registry
        ("https://dof.gob.mx/nota.php", True),  # unresolved for our reader
        ("https://www.facebook.com/x", True),  # a social platform
        ("https://cnpd.pt/x", False),
        ("https://docs.example.com/x", False),
    ],
)
def test_pages_our_reader_cannot_get_are_read_only_from_provider_text(
    url: str, walled: bool
) -> None:
    assert is_walled(url, CAT) is walled


# --- fusing two engines ------------------------------------------------------------------------------------------------------


def hit(url: str, rank: int) -> SearchHit:
    return SearchHit(url=url, rank=rank)


def test_one_engine_gives_the_order_of_plain_fusion() -> None:
    lists = [[hit("https://a.com/x", 1), hit("https://b.com/x", 2)], [hit("https://b.com/x", 1)]]
    assert [f.url for f in fuse_engines([lists])] == [f.url for f in fuse(lists)]
    assert fuse_engines([]) == [] and fuse_engines([[]]) == []


def test_agreement_between_engines_outranks_one_engine_asked_more_often() -> None:
    # engine one was asked three variants and all three put A first; engine two was asked once and put B first and A second
    one = [[hit("https://a.com/x", 1)], [hit("https://a.com/x", 1)], [hit("https://a.com/x", 1)]]
    two = [[hit("https://b.com/x", 1), hit("https://a.com/x", 2)]]
    fused = fuse_engines([one, two])
    assert fused[0].url == "https://a.com/x"
    assert fused[0].engines == 2 and fused[1].engines == 1
    # a page only the busy engine returned does not outrank a page both returned
    one2 = [[hit("https://c.com/x", 1)], [hit("https://c.com/x", 1)], [hit("https://c.com/x", 1)]]
    fused2 = fuse_engines([one2, [[hit("https://d.com/x", 1)]]])
    assert {f.engines for f in fused2} == {1}
    both = fuse_engines(
        [[[hit("https://e.com/x", 5)]], [[hit("https://e.com/x", 5)], [hit("https://f.com/x", 1)]]]
    )
    assert both[0].url == "https://e.com/x" and both[0].engines == 2


def test_engine_fusion_is_deterministic_and_keeps_the_richest_copy() -> None:
    rich = SearchHit(url="https://a.com/x", rank=9, content="full page text")
    fused = fuse_engines([[[hit("https://a.com/x", 1)]], [[rich]]])
    assert fused[0].hit.content == "full page text"
    again = fuse_engines([[[hit("https://a.com/x", 1)]], [[rich]]])
    assert fused == again
    swapped = fuse_engines([[[rich]], [[hit("https://a.com/x", 1)]]])
    assert [f.url for f in swapped] == [f.url for f in fused]
