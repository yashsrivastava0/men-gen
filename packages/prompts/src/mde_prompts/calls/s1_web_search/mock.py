"""Offline mock of S1.web_search: canned answers chosen by job and query keywords.

Deterministic, no network. The text carries inline markdown links the way a hosted search answer
does; ``citations`` lists the same sources with character offsets of those links. Sources are
reserved example domains (see ``fixtures/search.yaml``). When the call carries ``allowed_domains``
(policy), citations are re-pointed at those domains under a ``mock-source`` path so the policy
filter in the research engine still sees compliant sources.

An entry may carry a ``subject_pattern`` (a regular expression with one group). The text of such an
entry says ``{subject}`` where the part of the query the group captured goes (for example the name of
a feature, a sensitive area or a service), so queries that differ only in that part get different
facts back, as a real search would. ``subject_default`` is the wording used when nothing matches.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

from mde_contracts.calls import S1Input
from mde_contracts.gateway import Citation, FreeTextResult

FIXTURE = Path(__file__).parent / "fixtures" / "search.yaml"
_PLACEHOLDER = re.compile(r"\[\[(\d+)\]\]")
_SUBJECT_WORD = re.compile(r"[\w'-]+")
SUBJECT_MAX_CHARS = 60
DEFAULT_SUBJECT = "this feature"


@lru_cache(maxsize=1)
def _entries() -> tuple[dict[str, Any], ...]:
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    return tuple(cast("list[dict[str, Any]]", raw["entries"]))


def _score(entry: dict[str, Any], query: str) -> int:
    return sum(1 for kw in cast("list[str]", entry.get("keywords", [])) if kw.casefold() in query)


def pick_entry(job: str, query: str) -> dict[str, Any] | None:
    """The fixture entry for ``job`` that best matches ``query`` (default entry if none match)."""
    candidates = [e for e in _entries() if e["job"] == job]
    if not candidates:
        return None
    folded = query.casefold()
    best: dict[str, Any] | None = None
    best_score = 0
    for entry in candidates:
        score = _score(entry, folded)
        if score > best_score:
            best, best_score = entry, score
    if best is not None:
        return best
    return next((e for e in candidates if e.get("default")), candidates[0])


def _subject(entry: dict[str, Any], query: str) -> str:
    """The part of ``query`` an entry's ``subject_pattern`` captures, as plain words."""
    pattern = cast("str | None", entry.get("subject_pattern"))
    match = re.search(pattern, query, re.IGNORECASE) if pattern else None
    words = _SUBJECT_WORD.findall(match.group(1)) if match else []
    fallback = cast("str", entry.get("subject_default", DEFAULT_SUBJECT))
    return " ".join(words)[:SUBJECT_MAX_CHARS].strip() or fallback


def _slug(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "page"


def _sources(entry: dict[str, Any], allowed: list[str]) -> list[dict[str, str]]:
    sources = [dict(s) for s in cast("list[dict[str, str]]", entry["sources"])]
    if not allowed:
        return sources
    if entry.get("brand_site"):
        return [{"url": f"https://{allowed[0]}/", "title": "Home page (mock)"}]
    out: list[dict[str, str]] = []
    for i, src in enumerate(sources):
        domain = allowed[i % len(allowed)]
        out.append(
            {"url": f"https://{domain}/mock-source/{_slug(src['url'])}", "title": src["title"]}
        )
    return out


def handle(input: S1Input) -> FreeTextResult:
    entry = pick_entry(input.job, input.query)
    if entry is None:
        return FreeTextResult(text="", citations=[], search_calls=1)
    sources = _sources(entry, list(input.allowed_domains))
    template = " ".join(cast("str", entry["text"]).split())
    if "{subject}" in template:
        template = template.replace("{subject}", _subject(entry, input.query))

    pieces: list[str] = []
    citations: list[Citation] = []
    cursor = 0  # position in the output text
    last = 0
    for match in _PLACEHOLDER.finditer(template):
        pieces.append(template[last : match.start()])
        cursor += match.start() - last
        index = int(match.group(1)) - 1
        src = sources[index] if 0 <= index < len(sources) else sources[0]
        host = urlparse(src["url"]).hostname or "source"
        link = f"[{host}]({src['url']})"
        # a leading space before the link is already in the template text; keep it
        citations.append(
            Citation(url=src["url"], title=src["title"], start=cursor, end=cursor + len(link))
        )
        pieces.append(link)
        cursor += len(link)
        last = match.end()
    pieces.append(template[last:])
    text = "".join(pieces)
    # one citation per distinct url, in first-seen order (annotations repeat in real answers; the
    # research engine de-duplicates anyway, the mock keeps the list short and plausible)
    seen: set[str] = set()
    unique: list[Citation] = []
    for c in citations:
        if c.url not in seen:
            seen.add(c.url)
            unique.append(c)
    for src in sources:  # a source cited nowhere in the text is still listed
        if src["url"] not in seen:
            seen.add(src["url"])
            unique.append(Citation(url=src["url"], title=src["title"]))
    return FreeTextResult(text=text, citations=unique, search_calls=1)
