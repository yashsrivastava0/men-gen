"""Where to search: the stated geography, a model's country guess (flagged), and the languages."""

from __future__ import annotations

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.countries import code_for_name, country_name
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.plan import Place
from mde_research_agent.plan.proposal import PlanProposal


def _title(name: str | None) -> str | None:
    return " ".join(w.capitalize() for w in name.split()) if name else None


def build_place(inp: ResearchInput, cat: Catalog, proposal: PlanProposal | None = None) -> Place:
    """The place for queries. Uncertain geography means the country plus every stated city."""
    geo = inp.geography
    code = geo.country_code
    inferred = False
    if not code and proposal and proposal.country_code:
        guess = proposal.country_code.lower()
        code = guess if country_name(guess) else code_for_name(proposal.country_name)
        inferred = code is not None
    country = _title(geo.country) or _title(country_name(code))
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
        languages=langs,
        unknown=not phrases,
        uncertain=geo.uncertain,
        inferred=inferred,
    )


def scoped_place(place: Place, scope: str) -> Place | None:
    """The part of the Brief's place a topic's queries carry: all of it (``city``), the country only, or none.

    A city name finds local shops, not API documentation: "payments webhook authentication documentation <city>
    <country>" is a worse query than the same words without the city. Country scope falls back to the whole place when no
    country is known, and a place nothing was stated for is never added."""
    if place.unknown or scope == "none":
        return None
    if scope == "country" and place.country_name:
        return place.model_copy(update={"phrases": (place.country_name,)})
    return place
