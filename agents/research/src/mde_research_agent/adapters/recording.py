"""Record real search and fetch results to cassettes and replay them offline.

A cassette is one gzip JSON file per request in a directory: searches keyed by their request fields, pages by canonical
URL. ``Recording*`` wraps a real port and writes what it returns (page text capped, so the capped text IS what the run
used and a replay is self-consistent); ``Replay*`` serves them and raises a plain error for anything not recorded (never
goes live). With LLM cassettes from the gateway (``MDE_RECORD_CASSETTES``) a whole run replays offline with the same
``research.json`` hash.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Any

from mde_research_agent.core.urls import canonical_url
from mde_research_agent.ports.fetch import FetchedPage, FetchPort
from mde_research_agent.ports.search import (
    Capabilities,
    SearchError,
    SearchHit,
    SearchPort,
    SearchRequest,
    SearchResult,
)

MAX_PAGE_CHARS = 80_000


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, sort_keys=True, default=str)


def _read(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)  # type: ignore[no-any-return]


def search_key(r: SearchRequest) -> str:
    raw = json.dumps(asdict(r), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _hit(d: dict[str, Any]) -> SearchHit:
    return SearchHit(
        url=d["url"],
        title=d.get("title", ""),
        snippet=d.get("snippet", ""),
        content=d.get("content"),
        published=date.fromisoformat(d["published"]) if d.get("published") else None,
        rank=d.get("rank", 0),
        provider_score=d.get("provider_score"),
    )


class RecordingSearch:
    def __init__(self, inner: SearchPort, directory: Path) -> None:
        self.inner, self.dir = inner, Path(directory)
        self.name = inner.name
        self.capabilities: Capabilities = inner.capabilities

    async def search(self, request: SearchRequest) -> SearchResult:
        result = await self.inner.search(request)
        _write(
            self.dir / "search" / f"{search_key(request)}.json.gz",
            {"request": asdict(request), "result": asdict(result)},
        )
        return result


class ReplaySearch:
    def __init__(
        self, directory: Path, *, name: str = "replay", capabilities: Capabilities | None = None
    ) -> None:
        self.dir = Path(directory)
        self.name = name
        self.capabilities = capabilities or Capabilities(
            country=True,
            language=True,
            domain_prefer=True,
            domain_restrict=True,
            exact_match=True,
            time_range=True,
            page_content=True,
        )

    async def search(self, request: SearchRequest) -> SearchResult:
        path = self.dir / "search" / f"{search_key(request)}.json.gz"
        if not path.exists():
            raise SearchError("bad_request", "no cassette for this search (replay never goes live)")
        d = _read(path)["result"]
        return SearchResult(
            hits=tuple(_hit(h) for h in d["hits"]),
            provider=d["provider"],
            cost_micros=0,
            applied=d.get("applied", {}),
            locale_weak=d.get("locale_weak", False),
            latency_ms=0,
            cached=True,
        )


def _page_path(directory: Path, url: str) -> Path:
    return (
        directory
        / "fetch"
        / f"{hashlib.sha256(canonical_url(url).encode()).hexdigest()[:24]}.json.gz"
    )


class RecordingFetch:
    def __init__(self, inner: FetchPort, directory: Path) -> None:
        self.inner, self.dir = inner, Path(directory)

    async def fetch(self, url: str) -> FetchedPage:
        page = await self.inner.fetch(url)
        if len(page.text) > MAX_PAGE_CHARS:
            page = replace(page, text=page.text[:MAX_PAGE_CHARS])
        _write(_page_path(self.dir, url), {"url": url, "page": asdict(page)})
        return page


class ReplayFetch:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)

    async def fetch(self, url: str) -> FetchedPage:
        path = _page_path(self.dir, url)
        if not path.exists():
            return FetchedPage(url=url, final_url=url, reason="error")
        d = _read(path)["page"]
        d["published"] = date.fromisoformat(d["published"]) if d.get("published") else None
        return FetchedPage(**d)
