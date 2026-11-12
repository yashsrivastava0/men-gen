"""Platform constraints: the ordered list of ``platform_constraint`` items, built by code (D-159).

Each entry is one stated rule for one capability (push notifications, background location, in-app payments ...) with the
platform it applies to, what it means for the build ONLY when the page said so (``effect_on_build``; null otherwise, R27), the
evidence label and the domain of its best source. The capability is mapped to an id of ``data/capabilities.yaml`` by the
words of its label; text that matches none stays as the page's words. Order: the capability's place in the data file, then
platform, then evidence, then item id. Items about another region or entity are left out. Pure function of the items.
"""

from __future__ import annotations

from mde_research_agent.catalog.content import CapabilitiesConfig
from mde_research_agent.core.norm_keys import clean_key
from mde_research_agent.core.text import stem_set
from mde_research_agent.domain.research import Item, PlatformConstraint

EVIDENCE_ORDER = {"official": 0, "corroborated": 1, "single_source": 2, "unverified": 3}
PLATFORM_ORDER = {"both": 0, "ios": 1, "android": 2, "expo": 3, "react_native": 4, "web": 5}
SKIP_FLAGS = frozenset({"other_region", "other_entity"})


def capability_id_of(text: str, cfg: CapabilitiesConfig) -> str | None:
    """The capability of ``data/capabilities.yaml`` the stated text is about: the most shared stems with its label, at least
    two of them, or one when every word of the text is a word of the label or its terms ("notifications"); then the order of the data file."""
    mine = stem_set(text)
    best: str | None = None
    best_hits = 0
    for c in cfg.capabilities:
        words = stem_set(c.label) | stem_set(c.id.replace("_", " "))
        hits = len(mine & words)
        broad = words | stem_set(" ".join(c.terms))
        if hits > best_hits and (hits >= 2 or (hits >= 1 and mine <= broad)):
            best, best_hits = c.id, hits
    return best


def build_platform_constraints(
    items: list[Item], cfg: CapabilitiesConfig
) -> list[PlatformConstraint]:
    order = {c.id: n for n, c in enumerate(cfg.capabilities)}
    rows: list[tuple[tuple[int, str, int, int, str], PlatformConstraint]] = []
    for i in items:
        if i.kind != "platform_constraint" or set(i.flags) & SKIP_FLAGS:
            continue
        d = i.data
        stated = str(d.get("capability", ""))
        cap = capability_id_of(stated, cfg) or clean_key(stated)
        row = PlatformConstraint(
            item_id=i.id,
            capability=cap,
            platform=d.get("platform"),
            rule=str(d.get("rule", "")),
            effect_on_build=d.get("effect_on_build"),
            status=d.get("status"),
            evidence=i.evidence,
            domain=i.sources[0].domain if i.sources else None,
        )
        key = (
            order.get(cap, len(order)),
            cap,
            PLATFORM_ORDER.get(str(d.get("platform")), len(PLATFORM_ORDER)),
            EVIDENCE_ORDER[i.evidence],
            i.id,
        )
        rows.append((key, row))
    return [r for _, r in sorted(rows, key=lambda t: t[0])]
