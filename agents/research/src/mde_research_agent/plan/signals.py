"""Capability signals, the trust decision and light mode, computed by CODE from the Brief (doc 13 sections 4.1 and 4.2).

A signal is a generic capability (``payments``, ``scheduling``, ``health_data``...), found from the words in the Brief's
features, facts, roles, apps, integrations and glossary, or from its structure (roles, apps, locales, the sensitive-data
flags). A STRONG word is enough; WEAK words count only when at least two different units contain one. The word lists are
data (``data/signals.yaml``), so the same signal gives the same slot pack in a clinic, a shop or a fleet app.
"""

from __future__ import annotations

from mde_research_agent.catalog.models import Catalog, SignalsConfig
from mde_research_agent.core.text import stem_set
from mde_research_agent.domain.input import ResearchInput

SELLER_ROLE_WORDS = frozenset({"seller", "vendor", "merchant", "supplier", "farmer", "trader", "shopkeeper"})
BUYER_ROLE_WORDS = frozenset({"buyer", "customer", "consumer", "shopper", "client", "restaurant"})


def _units(inp: ResearchInput) -> list[set[str]]:
    """One stem set per piece of the Brief that can reveal a capability."""
    texts: list[str] = []
    texts += [f"{f.name} {f.intent}" for f in inp.features if f.duplicate_of is None]
    texts += [i.name for i in inp.integrations]
    texts += [f.statement for f in inp.facts if not f.is_template]
    texts += [f"{r.name} {r.goal}" for r in inp.roles if r.duplicate_of is None]
    texts += [f"{a.name} {a.purpose}" for a in inp.apps if a.is_real]
    texts += list(inp.glossary)
    texts += [f"{inp.idea.summary} {inp.idea.category} {inp.idea.subcategory}"]
    return [stem_set(t) for t in texts if t.strip()]


def _hit(word: str, unit: set[str]) -> bool:
    stems = stem_set(word)
    return bool(stems) and stems <= unit


def compute_signals(inp: ResearchInput, cfg: SignalsConfig) -> tuple[str, ...]:
    units = _units(inp)
    found: set[str] = set()
    for name, words in cfg.signals.items():
        if any(_hit(w, u) for w in words.strong for u in units):
            found.add(name)
            continue
        carrying = sum(1 for u in units if any(_hit(w, u) for w in words.weak))
        if carrying >= 2:
            found.add(name)
    roles = [r for r in inp.roles if r.duplicate_of is None and not r.is_group]
    role_stems = [stem_set(r.name) for r in roles]
    if any(s & stem_set(w) for s in role_stems for w in SELLER_ROLE_WORDS) and any(
        s & stem_set(w) for s in role_stems for w in BUYER_ROLE_WORDS
    ):
        found.add("two_sided")
    if inp.locales.non_latin:
        found.add("non_latin_locale")
    if len(roles) >= cfg.structure.many_roles_at_least:
        found.add("many_roles")
    if sum(1 for a in inp.apps if a.is_real) >= cfg.structure.multi_app_at_least:
        found.add("multi_app")
    if inp.sensitive_flags:
        found.add("sensitive_data")
    return tuple(sorted(found))


def trust_decision(
    inp: ResearchInput, signals: tuple[str, ...], cat: Catalog
) -> tuple[bool, str]:
    """Whether the ``trust`` job runs, with a plain reason. ``payments`` ALONE never triggers it; payments together with
    two-sided money (payouts, commissions) does; a content trigger overrides size; the deep class always runs it."""
    cfg = cat.signals.trust
    klass = inp.load.load_class if inp.load else "standard"
    if klass in cfg.always_for:
        return True, "This is a big app: privacy, security and store rules are always checked."
    fired = [s for s in cfg.triggers if s in signals]
    if fired:
        return True, "The Brief shows " + ", ".join(s.replace("_", " ") for s in fired) + ", so privacy and security rules are checked."
    if all(s in signals for s in cfg.together):
        return True, "Money moves between people (payments with sellers and buyers), so payment rules and privacy are checked."
    return False, "Nothing in the Brief needs the extra privacy and security checks."


def light_mode(inp: ResearchInput, signals: tuple[str, ...], cat: Catalog) -> bool:
    """A tiny app that needs no outside facts (doc 13 4.2): load below the limit, no signal, no named reference, no
    integration or rule fact and no place. Only the feature norms and the screen inventory are researched."""
    load = inp.load.score if inp.load else 0
    rule_facts = [f for f in inp.facts if (f.kind or "") in cat.plan.rule_fact_kinds and not f.is_template]
    has_place = bool(inp.geography.country or inp.geography.cities)
    return (
        load < cat.signals.light.below
        and not signals
        and not inp.references
        and not inp.integrations
        and not rule_facts
        and not has_place
    )
