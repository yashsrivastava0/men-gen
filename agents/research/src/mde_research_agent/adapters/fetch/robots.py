"""robots.txt: parse one file with the stdlib parser, and cache the answer per host.

* ``parse_robots(status, body, user_agent=...)`` turns what a ``/robots.txt`` request returned into
  ``RobotsRules``. The status decides first, as in RFC 9309: 2xx parse the body; 4xx (and a file we
  could not get at all) allow everything; 5xx disallow everything for that host.
* ``RobotsCache`` asks a loader for ``<origin>/robots.txt`` once per host (concurrent callers share
  one load) and answers ``allowed(url)``. The cache lives on the instance: no global state.
* ``Crawl-delay`` is only exposed (``crawl_delay(host)``); nothing here sleeps.

The stdlib parser (Python 3.13) follows RFC 9309: ``*`` and ``$`` wildcards, longest match wins, and
``Allow`` beats ``Disallow`` on a tie. The user agent is matched by its product token
(``MobigenResearchBot/1.0 (...)`` -> ``mobigenresearchbot``).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

ROBOTS_MAX_BYTES: Final = 512 * 1024  # RFC 9309: parse at least 500 KiB, ignore the rest

# What a loader returns: (status code, body bytes), or None when robots.txt could not be fetched
# (network error, timeout, refused address, too many redirects).
RobotsResponse = tuple[int, bytes]
RobotsLoader = Callable[[str], Awaitable[RobotsResponse | None]]


def product_token(user_agent: str) -> str:
    """``'MobigenResearchBot/1.0 (product research)'`` -> ``'MobigenResearchBot'`` (``*`` if none)."""
    found = re.match(r"[A-Za-z0-9_-]+", user_agent.strip())
    return found.group(0) if found else "*"


def origin_of(url: str) -> str | None:
    """``scheme://host[:port]`` (lowercase, default port dropped), or None for a non-http(s) URL."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        return None
    shown = f"[{host}]" if ":" in host else host
    default = 80 if scheme == "http" else 443
    return f"{scheme}://{shown}" + (f":{port}" if port not in (None, default) else "")


@dataclass(frozen=True)
class RobotsRules:
    """The rules of one host for one user agent. ``parser is None`` means there is no file to obey:
    everything is allowed (``allow_all``) or, for a failing server, nothing is."""

    parser: RobotFileParser | None
    token: str
    allow_all: bool = True

    def allows(self, url: str) -> bool:
        if self.parser is None:
            return self.allow_all
        return bool(self.parser.can_fetch(self.token, url))

    def crawl_delay(self) -> float | None:
        if self.parser is None:
            return None
        delay = self.parser.crawl_delay(self.token)
        return float(delay) if delay is not None else None


def parse_robots(status: int | None, body: bytes = b"", *, user_agent: str = "*") -> RobotsRules:
    """Rules from a robots.txt response. ``status=None`` means it could not be fetched (allow)."""
    token = product_token(user_agent)
    if status is not None and 200 <= status < 300:
        parser = RobotFileParser()
        text = body[:ROBOTS_MAX_BYTES].decode("utf-8-sig", errors="replace")
        parser.parse(text.splitlines())
        return RobotsRules(parser=parser, token=token)
    if status is not None and status >= 500:
        # RFC 9309 2.3.1.4: the server is failing, assume everything is disallowed
        return RobotsRules(parser=None, token=token, allow_all=False)
    # 4xx: no robots file; None: unreachable; anything else: nothing to obey
    return RobotsRules(parser=None, token=token, allow_all=True)


@dataclass
class _Entry:
    rules: RobotsRules
    host: str
    expires_at: float


class RobotsCache:
    """Per-instance robots.txt cache. ``loader(origin)`` fetches ``<origin>/robots.txt``; it must not
    raise (a failure is ``None``), though anything it does raise is treated as "unreachable"."""

    def __init__(
        self,
        loader: RobotsLoader,
        *,
        user_agent: str,
        ttl_seconds: float = 86_400.0,
        max_origins: int = 2048,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._user_agent = user_agent
        self._ttl = ttl_seconds
        self._max = max(max_origins, 1)
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _fresh(self, origin: str) -> RobotsRules | None:
        entry = self._entries.get(origin)
        if entry is None or entry.expires_at <= self._clock():
            return None
        return entry.rules

    async def rules_for(self, origin: str) -> RobotsRules:
        """The rules of ``origin`` (``https://example.com``), loading robots.txt on first use."""
        cached = self._fresh(origin)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(origin, asyncio.Lock())
        try:
            async with lock:
                cached = self._fresh(origin)
                if cached is not None:
                    return cached
                rules = await self._load(origin)
                self._store(origin, rules)
                return rules
        finally:
            if not lock.locked():
                self._locks.pop(origin, None)

    async def _load(self, origin: str) -> RobotsRules:
        try:
            response = await self._loader(origin)
        except Exception:  # a loader that raises is the same as an unreachable host
            response = None
        status, body = response if response is not None else (None, b"")
        return parse_robots(status, body, user_agent=self._user_agent)

    def _store(self, origin: str, rules: RobotsRules) -> None:
        host = (urlsplit(origin).hostname or "").lower()
        self._entries.pop(origin, None)
        self._entries[origin] = _Entry(rules, host, self._clock() + self._ttl)
        while len(self._entries) > self._max:
            self._entries.pop(next(iter(self._entries)))

    async def allowed(self, url: str) -> bool:
        """May the user agent fetch ``url``? True for anything that is not an http(s) URL (the
        caller's own URL checks refuse those)."""
        origin = origin_of(url)
        if origin is None:
            return True
        return (await self.rules_for(origin)).allows(url)

    def crawl_delay(self, host: str) -> float | None:
        """The ``Crawl-delay`` (seconds) a host asked of this user agent, if its robots.txt is cached
        and names one. ``host`` is a hostname or a URL. Nothing here waits: the caller decides."""
        name = (urlsplit(host).hostname or "") if "/" in host else host
        name = name.strip().lower().rstrip(".")
        for entry in self._entries.values():
            if entry.host == name:
                delay = entry.rules.crawl_delay()
                if delay is not None:
                    return delay
        return None

    def cached_origins(self) -> tuple[str, ...]:
        return tuple(self._entries)
