"""Offline stand-ins for the web: a fake search provider and a fake page fetcher over one ``FakeWorld``.

Used by tests, the mock dev server and ``run --mock``. Two modes work together:

* canned pages and routes a scenario registers (``add_page``, ``route``): exact control over what a query returns
  and what a page says (an injected instruction, a non-ASCII quote, a contradiction, a paywall);
* a deterministic generator for everything else: a query returns six hits on varied fake domains (documentation
  sites, help centres, blogs) and each page is synthesised from the query's own words, so extraction has real
  sentences to quote. Everything derives from SHA-256 of the inputs (never ``hash()``, which varies per process), so
  the same query and the same ``as_of`` always give the same hits and pages.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from mde_research_agent.core.text import STOP_WORDS, tokens
from mde_research_agent.core.urls import canonical_url, host_of
from mde_research_agent.ports.fetch import FetchedPage, SkipReason
from mde_research_agent.ports.search import (
    Capabilities,
    SearchError,
    SearchHit,
    SearchRequest,
    SearchResult,
)

_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
_SENTENCES = [
    "{A} apps let members {w2} and manage {w3} directly from the mobile app.",
    "Most {a} providers list {w2} and {w3} as core features on their pricing page.",
    "A typical {a} screen shows {w2} first, then {w3}, with an empty state when nothing is available.",
    "Fees for {a} start at {n} percent and funds settle in {d} business days after capture.",
    "Setting up {w2} takes {d} steps: choose the option, confirm the details and review {w3}.",
    "Teams can export {w2} reports and filter {w3} by date, status and owner.",
    "The sandbox for {a} supports test keys, webhook events for {w2} and rate limits of {n} requests per second.",
    "Customers rate {w3} and {w2} as the most used parts of {a} products in the region.",
    "Each {a} record has a status, a created date, an owner and a reference number used in {w3}.",
    "Recent updates added {w2} and improved {w3} for {a} users on Android and iOS.",
    "Support is available by chat and email, and the help centre explains {w2} and {w3} step by step.",
    "Accessibility options include larger text, high contrast and screen reader labels for {w2}.",
]


def _h(*parts: object) -> int:
    return int.from_bytes(
        hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()[:8], "big"
    )


def _slug_words(text: str, n: int = 3) -> list[str]:
    words = [w for w in tokens(text) if w not in STOP_WORDS and len(w) > 2 and w.isascii()]
    return list(dict.fromkeys(words))[:n] or ["service"]


@dataclass
class Canned:
    text: str
    title: str = ""
    published: date | None = None
    status: int = 200
    reason: SkipReason | None = None
    in_search_content: bool = (
        False  # the search provider returns the text itself (Tavily raw content)
    )


@dataclass
class FakeWorld:
    as_of: date = date(2026, 11, 11)
    pages: dict[str, Canned] = field(default_factory=dict)
    routes: list[tuple[re.Pattern[str], list[str]]] = field(default_factory=list)
    generate: bool = True
    provider_content_every: int = 0  # every Nth generated hit carries page text (0 = none)
    search_cost_micros: int = 0
    fail_search: Callable[[SearchRequest], SearchError | None] | None = None
    gate: asyncio.Event | None = (
        None  # when set and not open, every search waits (tests hold a run mid-flight)
    )

    def add_page(
        self, url: str, text: str, *, title: str = "", published: date | None = None, **kw: object
    ) -> str:
        key = canonical_url(url)
        self.pages[key] = Canned(text=text, title=title or host_of(url), published=published, **kw)  # type: ignore[arg-type]
        return key

    def route(self, pattern: str, urls: list[str]) -> None:
        """Queries whose text matches ``pattern`` (regex, case-insensitive) return exactly ``urls`` in order."""
        self.routes.append((re.compile(pattern, re.I), [canonical_url(u) for u in urls]))

    # --- the generator -----------------------------------------------------------------------------
    def _domain(self, words: list[str], k: int) -> str:
        base = f"{words[0]}{(_h(*words, k) % 90) + 10}"
        return [
            f"docs.{base}.example",
            f"www.{base}.example",
            f"help.{base}.example",
            f"news{base}.example",
            f"www.{base}-app.example",
            f"developer.{base}.example",
        ][k % 6]

    def _url(self, words: list[str], k: int) -> str:
        return f"https://{self._domain(words, k)}/{'-'.join(words)}/{k}"

    def synth_text(self, words: list[str], k: int) -> tuple[str, date]:
        a = " ".join(words)
        w1, w2, w3 = (words + words + words)[:3]
        age = 2 + (k * 5) % 30
        pub = date(
            self.as_of.year - (age // 12 if self.as_of.month > age % 12 else age // 12 + 1),
            ((self.as_of.month - age % 12 - 1) % 12) + 1,
            1,
        )
        base = _h(*words, k)
        sentences = []
        for i in range(len(_SENTENCES)):
            tpl = _SENTENCES[(i + base) % len(_SENTENCES)]
            sentences.append(
                tpl.format(
                    A=a.capitalize(), a=a, w2=w2, w3=w3, n=2 + (k % 3) + (i % 2), d=2 + (k % 4)
                )
            )
        sentences.append(f"Updated {_MONTHS[pub.month - 1]} {pub.year} for {a} teams.")
        return " ".join(dict.fromkeys(sentences)) + f" {w1.capitalize()} guide number {k}.", pub

    # --- search and fetch -------------------------------------------------------------------------
    def hits_for(self, query: str, max_results: int = 10) -> list[SearchHit]:
        for pattern, urls in self.routes:
            if pattern.search(query):
                return [
                    SearchHit(
                        url=u,
                        title=self.pages[u].title if u in self.pages else host_of(u),
                        snippet=(self.pages[u].text[:160] if u in self.pages else ""),
                        content=self.pages[u].text
                        if u in self.pages and self.pages[u].in_search_content
                        else None,
                        published=self.pages[u].published if u in self.pages else None,
                        rank=i,
                    )
                    for i, u in enumerate(urls[:max_results], start=1)
                ]
        if not self.generate:
            return []
        words = _slug_words(query)
        out = []
        for k in range(6):
            url = self._url(words, k)
            text, pub = self.synth_text(words, k)
            content = (
                text
                if self.provider_content_every and k % self.provider_content_every == 0
                else None
            )
            out.append(
                SearchHit(
                    url=url,
                    title=f"{' '.join(words).title()} guide {k}",
                    snippet=text[:160],
                    content=content,
                    published=pub,
                    rank=k + 1,
                )
            )
        return out[:max_results]

    def page_for(self, url: str) -> FetchedPage:
        key = canonical_url(url)
        if key in self.pages:
            c = self.pages[key]
            ok = c.reason is None and c.status == 200 and bool(c.text)
            return FetchedPage(
                url=url,
                final_url=key,
                text=c.text if ok else "",
                status=c.status,
                content_type="text/html",
                title=c.title,
                published=c.published,
                content_hash=hashlib.sha256(c.text.encode()).hexdigest() if ok else "",
                reason=None if ok else (c.reason or ("no_content" if c.status == 200 else "error")),
                bytes_read=len(c.text),
            )
        m = re.match(r"https://[^/]+/([^/]+)/(\d+)$", key)
        if not m or not self.generate:
            return FetchedPage(url=url, final_url=key, status=404, reason="not_found")
        words = m.group(1).split("-")
        text, pub = self.synth_text(words, int(m.group(2)))
        return FetchedPage(
            url=url,
            final_url=key,
            text=text,
            status=200,
            content_type="text/html",
            title=f"{' '.join(words).title()} guide",
            published=pub,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            bytes_read=len(text),
        )


class FakeSearch:
    """A ``SearchPort`` over a :class:`FakeWorld`; counts calls and can fail on demand."""

    name = "fake"
    capabilities = Capabilities(
        country=True,
        language=True,
        domain_prefer=True,
        domain_restrict=True,
        exact_match=True,
        time_range=True,
        page_content=True,
    )

    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.requests: list[SearchRequest] = []

    async def search(self, request: SearchRequest) -> SearchResult:
        self.requests.append(request)
        if self.world.gate is not None:
            await self.world.gate.wait()
        if self.world.fail_search is not None:
            err = self.world.fail_search(request)
            if err is not None:
                raise err
        hits = self.world.hits_for(request.query, request.max_results)
        if request.domain_mode == "restrict" and request.domains:
            keep = [h for h in hits if any(host_of(h.url).endswith(d) for d in request.domains)]
            hits = keep or [
                SearchHit(
                    url=f"https://{request.domains[0]}/{'-'.join(_slug_words(request.query))}/0",
                    title="official",
                    rank=1,
                )
            ]
        applied = {
            k: v for k, v in (("country", request.country), ("language", request.language)) if v
        }
        return SearchResult(
            hits=tuple(hits),
            provider=self.name,
            cost_micros=self.world.search_cost_micros,
            applied=applied,
        )


class FakeFetch:
    """A ``FetchPort`` over a :class:`FakeWorld`; counts fetches."""

    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.fetched.append(url)
        return self.world.page_for(url)
