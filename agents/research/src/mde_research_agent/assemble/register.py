"""The compliance register: ``compliance_note`` items grouped by country, then regime, built by code (D-159).

Each entry carries the statement, the authority, the effective date, what it applies to (flows, roles or kinds of data, as
the page states them), the penalty when one is stated, the source domain and the evidence label. Every entry is legal
information and never advice; every item in it is on the owner's confirm list (``assemble/output.py`` puts every
compliance note there, RV-26 checks it). The country is the item's own ``about_region`` when it is a country code, else the
country the page names, else ``global`` or ``unknown``. The regime is the one the page names, else the note's topic. Items
about another region or entity are left out. Pure function of the items.
"""

from __future__ import annotations

from collections import defaultdict

from mde_research_agent.core.norm_keys import clean_key
from mde_research_agent.domain.research import Item, RegisterCountry, RegisterEntry, RegisterRegime

EVIDENCE_ORDER = {"official": 0, "corroborated": 1, "single_source": 2, "unverified": 3}
SKIP_FLAGS = frozenset({"other_region", "other_entity"})


def country_of(item: Item) -> str:
    region = item.about_region
    if region not in ("unknown", "global"):
        return region
    stated = clean_key(str(item.data.get("country") or ""))
    return stated or region


def regime_of(item: Item) -> str:
    return str(item.data.get("regime") or item.data.get("topic") or "unspecified").strip()


def build_register(items: list[Item]) -> list[RegisterCountry]:
    grouped: dict[str, dict[str, list[tuple[tuple[int, str], RegisterEntry]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    names: dict[tuple[str, str], str] = {}
    for i in items:
        if i.kind != "compliance_note" or set(i.flags) & SKIP_FLAGS:
            continue
        d = i.data
        entry = RegisterEntry(
            item_id=i.id,
            topic=str(d.get("topic", "")),
            statement=str(d.get("summary", "")),
            authority=d.get("authority"),
            effective_date=d.get("effective_date"),
            applies_to=[str(x) for x in d.get("applies_to", [])],
            penalty=d.get("penalty"),
            domain=i.sources[0].domain if i.sources else None,
            evidence=i.evidence,
        )
        country = country_of(i)
        regime = regime_of(i)
        key = clean_key(regime)
        names.setdefault((country, key), regime)
        grouped[country][key].append(((EVIDENCE_ORDER[i.evidence], i.id), entry))
    last = {"global": 1, "unknown": 2}
    out: list[RegisterCountry] = []
    for country in sorted(grouped, key=lambda c: (last.get(c, 0), c)):
        regimes = [
            RegisterRegime(
                regime=names[(country, key)],
                entries=[e for _, e in sorted(grouped[country][key], key=lambda t: t[0])],
            )
            for key in sorted(grouped[country])
        ]
        out.append(RegisterCountry(country=country, regimes=regimes))
    return out
