"""Source classes and independence.

``classify_source`` puts a page into one of official, official_inferred, store, help, press, blog, forum, seo, by the
authority registry first, then public-suffix patterns (gov.*, gob.*, gouv.*, go.*, *.gov, europa.eu, .int: a match is
``official_inferred``, official by pattern and not by name), documentation and help subdomains, store and forum domains,
blog markers and SEO title patterns, and finally "the entity's own site" (a provider's own domain speaking about itself,
matched against the entities of the run: also ``official_inferred``). Rules and domains are data; code decides the class,
never a model. Forum, blog and SEO pages cannot support a high-impact claim (R8, R9).

``official_domains_for`` picks the authority domains a topic asks for first (official-first discovery), and
``authority_language`` says which language an authority writes in.

``independent_count`` counts how many INDEPENDENT sources a set of pages gives: one per organisation
(``docs.stripe.com`` and ``stripe.com`` and ``stripe.dev`` are one), and pages whose texts are near
duplicates (syndicated content) are one.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from functools import lru_cache

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.countries import countries, country_language
from mde_research_agent.core.text import stem_set, tokens
from mde_research_agent.core.urls import host_of, registrable_domain
from mde_research_agent.domain.research import SourceClass

SHINGLE_WORDS = 5
NEAR_DUPLICATE = 0.8
MAX_OFFICIAL_DOMAINS = 20  # official domains one topic may ask a provider to prefer (Tavily takes far more; 20 is plenty)


def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def country_scopes(country: str | None) -> frozenset[str]:
    """The authority files that apply to a country: its own, its bloc's (``bloc`` in countries.yaml, ``eu`` for a member
    state) and ``global``. ``frozenset({"global"})`` when no country is known."""
    code = (country or "").lower()
    bloc = countries().get(code, {}).get("bloc")
    return frozenset({"global", *([code] if code else []), *([bloc] if bloc else [])})


def _authority_class(host: str, cat: Catalog, country: str | None) -> SourceClass | None:
    best: tuple[int, SourceClass] | None = None
    scopes = country_scopes(country)
    for entry in cat.authority:
        if entry.country not in scopes and country is not None:
            continue
        if _matches(host, entry.domain) and (best is None or len(entry.domain) > best[0]):
            best = (len(entry.domain), entry.class_)
    return best[1] if best else None


def _label_matches(label: str, want: str) -> bool:
    """One pattern label against one host label; ``*`` is exactly two letters (a country code)."""
    if want == "*":
        return len(label) == 2 and label.isalpha()
    return label == want


def matches_suffix_pattern(host: str, pattern: str) -> bool:
    """Does the END of ``host``'s labels match ``pattern`` (``gov``, ``gov.*``, ``europa.eu``)? ``*`` is a country code."""
    want = pattern.casefold().strip(".").split(".")
    labels = host.casefold().strip(".").split(".")
    if len(labels) <= len(want):  # the pattern alone ("gov.in") is a suffix, not a site
        return False
    return all(_label_matches(lab, w) for lab, w in zip(labels[-len(want) :], want, strict=True))


@lru_cache(maxsize=1)
def _tld_country() -> dict[str, str]:
    """``{country-code top-level domain: alpha-2 code}`` from ``countries.yaml`` (``uk`` for gb, ``in`` for in...)."""
    return {info["tld"].lower(): code for code, info in countries().items() if info.get("tld")}


def host_country(host: str) -> str | None:
    """The country a host belongs to by its country-code top-level domain (``gov.in`` -> in), else None."""
    last = host.casefold().rsplit(".", 1)[-1]
    return _tld_country().get(last) if len(last) == 2 else None


def inferred_official(host: str, cat: Catalog, country: str | None) -> bool:
    """Is ``host`` official by its public suffix (see ``official_suffix_patterns``)?

    When the topic has a country and the host carries ANOTHER country's code (a Brazilian ministry on an Indian topic), the
    pattern does not count: it is an official source about its own country. ``.gov``, ``.int``, ``europa.eu`` and the like
    carry no country code and count for any topic."""
    if not any(matches_suffix_pattern(host, p) for p in cat.source_rules.official_suffix_patterns):
        return False
    owner = host_country(host)
    return not (country and owner and owner != country.lower())


def own_site(entity: str | None, domain: str) -> bool:
    """Does ``domain`` look like the home of ``entity`` (``Razorpay`` ~ ``razorpay.com``)?"""
    if not entity:
        return False
    label = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].casefold())
    name = re.sub(r"[^a-z0-9]", "", "".join(tokens(entity)))
    return len(label) >= 4 and len(name) >= 4 and (label in name or name in label)


def classify_source(
    url: str,
    cat: Catalog,
    *,
    title: str = "",
    entity: str | None = None,
    country: str | None = None,
    entities: Iterable[str] = (),
) -> SourceClass:
    """The source class of a page (see the module docstring for the order of the rules).

    ``entity`` is the topic's own entity, ``entities`` every entity the run has discovered: a page on the home domain of
    any of them is the provider speaking about itself (``official_inferred``)."""
    host = host_of(url)
    if not host:
        return "blog"
    rules = cat.source_rules
    from_registry = _authority_class(host, cat, country)
    if from_registry:
        return from_registry
    if inferred_official(host, cat, country):
        return "official_inferred"
    first = host.split(".")[0]
    if first in rules.doc_subdomain_prefixes:
        return "official"
    if first in rules.help_subdomain_prefixes:
        return "help"
    if any(_matches(host, d) for d in rules.store_domains):
        return "store"
    if (
        any(_matches(host, d) for d in rules.forum_domains)
        or first in rules.forum_subdomain_prefixes
    ):
        return "forum"
    if any(_matches(host, d) for d in rules.seo_domains):
        return "seo"
    low_title = title.casefold()
    if any(re.search(p, low_title) for p in rules.seo_title_patterns):
        return "seo"
    if any(_matches(host, d) for d in rules.blog_domains):
        return "blog"
    if any(_matches(host, d) for d in rules.press_domains):
        return "press"  # a news site's /article/ pages are press, not blogs
    path = url.split(host, 1)[-1].casefold()
    if any(m in path for m in rules.blog_path_markers):
        return "blog"
    own = registrable_domain(url)
    if any(own_site(name, own) for name in (entity, *entities)):
        return "official_inferred"
    return "blog"


def is_walled(url: str, cat: Catalog) -> bool:
    """Does the host refuse our page reader? Social platforms (``walled_domains``) and registry entries whose ``fetch`` is
    ``blocked`` or ``unresolved``: such a page is read only when the search provider already returned its text."""
    host = host_of(url)
    if not host:
        return False
    return any(_matches(host, d) for d in cat.source_rules.walled_domains) or any(
        e.fetch != "ok" and _matches(host, e.domain) for e in cat.authority
    )


def authority_language(cat: Catalog, country: str | None) -> str:
    """The language the authorities of ``country`` write in (the authority file's ``language``, else the country's main
    language in ``countries.yaml``), ``en`` when nothing is known. Used for the wording of rules queries."""
    code = (country or "").lower()
    for entry in cat.authority:
        if entry.country == code and entry.language:
            return entry.language
    return country_language(code) or "en"


def official_domains_for(
    cat: Catalog,
    words: Iterable[str],
    countries_: Sequence[str],
    *,
    limit: int = MAX_OFFICIAL_DOMAINS,
) -> tuple[str, ...]:
    """The official domains of the authority registry that fit a topic: those of ``countries_`` and ``global`` whose
    ``topics`` keywords (every word of a keyword phrase) all appear in ``words``. Best match first (more words matched,
    then a country's own domain before a global one, then by name); at most ``limit``.

    Official-first discovery sends these as a preferred (or, where a provider cannot prefer, restricted) domain list with
    one query variant of the topic, so the topic's official pages are asked for by name instead of hoped for."""
    have = stem_set(" ".join(words))
    wanted = set().union(*(country_scopes(c) for c in countries_)) if countries_ else {"global"}
    best: dict[str, tuple[int, int]] = {}
    for entry in cat.authority:
        if entry.class_ != "official" or entry.country not in wanted:
            continue
        for phrase in entry.topics:
            need = stem_set(phrase)
            if need and need <= have:
                score = (-len(need), 1 if entry.country == "global" else 0)
                if entry.domain not in best or score < best[entry.domain]:
                    best[entry.domain] = score
    return tuple(sorted(best, key=lambda d: (best[d], d))[:limit])


def can_support_high_impact(source_class: SourceClass, cat: Catalog) -> bool:
    return source_class not in cat.source_rules.cannot_support_high_impact


def org_key(url: str) -> str:
    """One key per organisation: the registrable domain's name without its suffix (``stripe``)."""
    fld = registrable_domain(url)
    return fld.split(".")[0] if fld else ""


def shingles(text: str, n: int = SHINGLE_WORDS) -> frozenset[int]:
    words = tokens(text)
    if len(words) < n:
        return frozenset({hash(tuple(words))}) if words else frozenset()
    return frozenset(hash(tuple(words[i : i + n])) for i in range(len(words) - n + 1))


def near_duplicate(
    a: frozenset[int], b: frozenset[int], *, threshold: float = NEAR_DUPLICATE
) -> bool:
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


def independent_count(
    urls: Sequence[str],
    *,
    texts: Sequence[str] | None = None,
    is_duplicate: Callable[[int, int], bool] | None = None,
) -> int:
    """How many independent sources ``urls`` give (one per organisation; near-duplicate pages merge)."""
    parent = list(range(len(urls)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    keys = [org_key(u) or u for u in urls]
    for i in range(len(urls)):
        for j in range(i + 1, len(urls)):
            if keys[i] == keys[j]:
                union(i, j)
    sh = [shingles(t) for t in texts] if texts is not None else None
    for i in range(len(urls)):
        for j in range(i + 1, len(urls)):
            dup = (
                is_duplicate(i, j)
                if is_duplicate
                else (sh is not None and near_duplicate(sh[i], sh[j]))
            )
            if dup:
                union(i, j)
    return len({find(i) for i in range(len(urls))})
