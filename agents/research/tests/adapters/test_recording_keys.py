"""Cassette keys stay stable when SearchRequest grows (D-163): recorded runs must keep replaying."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from mde_research_agent.adapters.recording import key_payload, search_key
from mde_research_agent.ports.search import SearchRequest

ORIGINAL = (
    "query",
    "country",
    "language",
    "domain_mode",
    "domains",
    "exact",
    "recency_months",
    "want_content",
    "max_results",
)


def _legacy_key(r: SearchRequest) -> str:
    """The key formula of the first recordings: every field of the request, as JSON."""
    raw = json.dumps({k: asdict(r)[k] for k in ORIGINAL}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def test_a_request_keeps_the_key_it_was_recorded_under() -> None:
    plain = SearchRequest(query="online payments India API documentation")
    rich = SearchRequest(
        query="Exotel API documentation",
        country="in",
        language="en",
        domain_mode="restrict",
        domains=("exotel.com",),
        exact=True,
        recency_months=12,
        want_content=True,
        max_results=5,
    )
    assert search_key(plain) == _legacy_key(plain)
    assert search_key(rich) == _legacy_key(rich)


def test_a_new_field_at_its_default_does_not_change_the_key() -> None:
    fields = dict.fromkeys(ORIGINAL) | {"future_field": ()}
    defaults = {"future_field": ()}
    assert key_payload(fields, defaults) == dict.fromkeys(ORIGINAL)


def test_a_new_field_with_a_value_is_part_of_the_key() -> None:
    fields = dict.fromkeys(ORIGINAL) | {"future_field": ("a.com",)}
    defaults = {"future_field": ()}
    assert key_payload(fields, defaults)["future_field"] == ("a.com",)


def test_different_queries_have_different_keys() -> None:
    assert search_key(SearchRequest(query="a")) != search_key(SearchRequest(query="b"))
