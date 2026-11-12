"""Country lookup and the reading of a stated country text that names several countries."""

from __future__ import annotations

import pytest

from mde_research_agent.core.countries import (
    code_for_name,
    countries,
    country_name,
    parse_countries,
)
from mde_research_agent.domain.input import Country


def pairs(text: str | None) -> list[tuple[str, str | None]]:
    return [(c.name, c.code) for c in parse_countries(text)]


def test_the_central_american_countries_are_known() -> None:
    assert code_for_name("Guatemala") == "gt" and code_for_name("costa rica") == "cr"
    assert country_name("gt") == "guatemala" and country_name("cr") == "costa rica"
    assert countries()["cr"]["tld"] == "cr" and countries()["gt"]["language"] == "es"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("India", [("India", "in")]),
        ("india", [("India", "in")]),
        ("in", [("India", "in")]),
        ("United Arab Emirates", [("United Arab Emirates", "ae")]),
        (
            "Mexico, Guatemala and Costa Rica",
            [("Mexico", "mx"), ("Guatemala", "gt"), ("Costa Rica", "cr")],
        ),
        (
            "Mexico, Guatemala, and Costa Rica",
            [("Mexico", "mx"), ("Guatemala", "gt"), ("Costa Rica", "cr")],
        ),
        ("Mexico y Guatemala", [("Mexico", "mx"), ("Guatemala", "gt")]),
        ("Senegal et Mexico", [("Senegal", "sn"), ("Mexico", "mx")]),
        ("Brasil e Portugal", [("Brasil", None), ("Portugal", "pt")]),
        ("India & Nepal", [("India", "in"), ("Nepal", "np")]),
        ("India/Nepal/Sri Lanka", [("India", "in"), ("Nepal", "np"), ("Sri Lanka", "lk")]),
        ("India; Kenya", [("India", "in"), ("Kenya", "ke")]),
        ("Mexico, Mexico and Mexico", [("Mexico", "mx")]),  # one country is not three
        # a list with one country this catalog does not know keeps it, with no code
        ("Mexico, Wakanda", [("Mexico", "mx"), ("Wakanda", None)]),
        # not a list of countries: one statement, kept as written
        ("East and West Africa", [("East and West Africa", None)]),
        ("Wakanda", [("Wakanda", None)]),
        ("", []),
        ("   ", []),
        (None, []),
    ],
)
def test_a_stated_country_text_becomes_one_entry_per_country(
    text: str | None, expected: list[tuple[str, str | None]]
) -> None:
    assert pairs(text) == expected


def test_a_known_name_that_contains_a_joining_word_is_never_cut_in_two(monkeypatch) -> None:
    from mde_research_agent.core import countries as mod

    monkeypatch.setattr(
        mod,
        "countries",
        lambda: {
            "tt": {
                "name": "trinidad and tobago",
                "label": "trinidad and tobago",
                "tld": "tt",
                "language": "en",
            },
            "mx": {"name": "mexico", "label": "mexico", "tld": "mx", "language": "es"},
        },
    )
    mod._by_name.cache_clear()
    mod._names_with_connective.cache_clear()
    try:
        assert pairs("Trinidad and Tobago") == [("Trinidad And Tobago", "tt")]
        assert pairs("Mexico and Trinidad and Tobago") == [
            ("Mexico", "mx"),
            ("Trinidad And Tobago", "tt"),
        ]
    finally:
        mod._by_name.cache_clear()
        mod._names_with_connective.cache_clear()


def test_the_entry_type_is_a_frozen_country() -> None:
    c = parse_countries("Mexico")[0]
    assert isinstance(c, Country) and c.model_dump() == {"name": "Mexico", "code": "mx"}
