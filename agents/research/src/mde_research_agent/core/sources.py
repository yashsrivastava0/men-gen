"""Source classes and independence.

``classify_source`` puts a page into one of official, store, help, press, blog, forum, seo, by the
authority registry first, then TLD patterns, documentation and help subdomains, store and forum domains,
blog markers and SEO title patterns, and finally "the entity's own site". Rules and domains are data.
Forum, blog and SEO pages cannot support a high-impact claim (R8, R9).

``independent_count`` counts how many INDEPENDENT sources a set of pages gives: one per organisation
(``docs.stripe.com`` and ``stripe.com`` and ``stripe.dev`` are one), and pages whose texts are near
duplicates (syndicated content) are one.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.text import tokens
from mde_research_agent.core.urls import host_of, registrable_domain
from mde_research_agent.domain.research import SourceClass

SHINGLE_WORDS = 5
NEAR_DUPLICATE = 0.8


def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _authority_class(host: str, cat: Catalog, country: str | None) -> SourceClass | None:
    best: tuple[int, SourceClass] | None = None
    for entry in cat.authority:
        if entry.country not in ("global", (country or "").lower()) and country is not None:
            continue
        if _matches(host, entry.domain) and (best is None or len(entry.domain) > best[0]):
            best = (len(entry.domain), entry.class_)
    return best[1] if best else None


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
) -> SourceClass:
    """The source class of a page (see the module docstring for the order of the rules)."""
    host = host_of(url)
    if not host:
        return "blog"
    rules = cat.source_rules
    from_registry = _authority_class(host, cat, country)
    if from_registry:
        return from_registry
    if any(host.endswith("." + s) or host == s for s in rules.official_tld_suffixes):
        return "official"
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
    if own_site(entity, registrable_domain(url)):
        return "official"
    return "blog"


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
