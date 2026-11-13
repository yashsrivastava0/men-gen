"""Where to search: the stated geography, a model's country guess (flagged), and the languages."""

from __future__ import annotations

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.countries import code_for_name, country_language, country_name
from mde_research_agent.domain.input import Country, ResearchInput
from mde_research_agent.domain.plan import Place
from mde_research_agent.plan.proposal import PlanProposal


def _title(name: str | None) -> str | None:
    return " ".join(w.capitalize() for w in name.split()) if name else None


def build_place(inp: ResearchInput, cat: Catalog, proposal: PlanProposal | None = None) -> Place:
    """The place for queries. Uncertain geography means the country plus every stated city.

    A Brief that names several countries ("Mexico, Guatemala and Costa Rica") keeps them all in ``countries``; the
    primary one (the first) carries the query words and the search filter, and the per-country topics are a later
    step of the plan."""
    geo = inp.geography
    code = geo.country_code
    inferred = False
    if not code and proposal and proposal.country_code:
        guess = proposal.country_code.lower()
        code = guess if country_name(guess) else code_for_name(proposal.country_name)
        inferred = code is not None
    country = _title(geo.country) or _title(country_name(code))
    countries = tuple(geo.countries) or (
        (Country(name=country, code=code),) if country and inferred else ()
    )
    cities = list(geo.cities)
    if proposal:
        cities += [
            c for c in proposal.cities if c and c.casefold() not in {x.casefold() for x in cities}
        ]
    langs = tuple(
        dict.fromkeys(
            c.split("-")[0]
            for c in (inp.locales.search_languages or inp.locales.codes)
            if c.split("-")[0] != "en" and c != "hi-Latn"
        )
    )
    phrases: list[str] = []
    if geo.uncertain and country:
        phrases = [country, *cities]
    else:
        primary = cities[0] if cities else (geo.region or country)
        if primary:
            phrases.append(primary)
        if country and country not in phrases:
            phrases.append(country)
    phrases = [p for p in dict.fromkeys(phrases) if p]
    return Place(
        phrases=tuple(phrases),
        country_code=code,
        country_name=country,
        countries=countries,
        languages=langs,
        unknown=not phrases,
        uncertain=geo.uncertain,
        inferred=inferred,
    )


def scoped_place(place: Place, scope: str, country: Country | None = None) -> Place | None:
    """The part of the Brief's place a topic's queries carry: all of it (``city``), the country only, or none.

    A city name finds local shops, not API documentation: "payments webhook authentication documentation <city>
    <country>" is a worse query than the same words without the city. Country scope falls back to the whole place when no
    country is known, and a place nothing was stated for is never added.

    ``country`` makes the place that ONE country's (a per-country topic, ``scope: country_each``): its name carries the
    query words and its code the search filter, whatever the Brief's primary country is."""
    if place.unknown or scope == "none":
        return None
    if country is not None:
        return place.model_copy(
            update={
                "phrases": (country.name,),
                "country_code": country.code,
                "country_name": country.name,
                "countries": (country,),
                "languages": country_languages(country.code, place.languages),
            }
        )
    if scope == "country" and place.country_name:
        return place.model_copy(update={"phrases": (place.country_name,)})
    return place


def country_languages(code: str | None, brief_languages: tuple[str, ...]) -> tuple[str, ...]:
    """The non-English search languages for one country's topics: the country's own main language (from
    ``countries.yaml``) first, then the Brief's other languages. A single-country Brief keeps the Brief's languages."""
    own = country_language(code)
    first = [own] if own and own != "en" else []
    return tuple(dict.fromkeys([*first, *brief_languages]))
