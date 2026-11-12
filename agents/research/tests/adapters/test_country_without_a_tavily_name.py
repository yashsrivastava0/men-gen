"""A country the catalog knows but Tavily's ``country`` enum does not list (``name: null`` in countries.yaml).

Tavily sends no ``country`` for it and reports a weak locale (it would answer 400 to a value outside its enum); Serper's
``gl`` takes the alpha-2 code itself, so it still sends it. No country in the shipped file is null today (the repo's own
copy of Tavily's enum lists every one); the entry below is the shape a new one would have.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from mde_research_agent.adapters.search import common
from mde_research_agent.adapters.search.common import CountryInfo
from mde_research_agent.adapters.search.serper import SerperAdapter
from mde_research_agent.adapters.search.tavily import TavilyAdapter
from mde_research_agent.ports.search import SearchRequest

UNLISTED = CountryInfo(code="zz", name=None, tld="zz", language="es")


@pytest.fixture
def unlisted_country(monkeypatch: pytest.MonkeyPatch) -> None:
    table = MappingProxyType({**common.load_countries(), "zz": UNLISTED})
    monkeypatch.setattr(common, "load_countries", lambda: table)


def tavily() -> TavilyAdapter:
    return TavilyAdapter(api_key="x" * 20)


def test_tavily_sends_no_country_for_an_entry_without_a_name_and_says_the_locale_is_weak(
    unlisted_country: None,
) -> None:
    plan = tavily()._plan(SearchRequest(query="payments api documentation", country="zz"))
    assert "country" not in plan.body and "country" not in plan.applied
    assert plan.locale_weak is True


def test_tavily_still_sends_a_country_that_has_a_name(unlisted_country: None) -> None:
    plan = tavily()._plan(SearchRequest(query="payments api documentation", country="gt"))
    assert plan.body["country"] == "guatemala" and plan.locale_weak is False


def test_the_countries_file_loader_accepts_a_null_name_only_with_a_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = {"zz": {"name": None, "label": "zedland", "tld": "zz", "language": "es"}}
    bad = {"zz": {"name": None, "tld": "zz", "language": "es"}}
    common.load_countries.cache_clear()
    try:
        monkeypatch.setattr(common, "_read_data", lambda _name: good)
        assert common.load_countries()["zz"].name is None
        common.load_countries.cache_clear()
        monkeypatch.setattr(common, "_read_data", lambda _name: bad)
        with pytest.raises(ValueError, match="needs a label"):
            common.load_countries()
    finally:
        common.load_countries.cache_clear()


def test_serper_still_sends_the_alpha2_code_it_takes_directly(unlisted_country: None) -> None:
    adapter = SerperAdapter(api_key="x" * 20)
    plan = adapter._plan(SearchRequest(query="payments api documentation", country="zz"))
    assert plan.body["gl"] == "zz" and plan.locale_weak is False
