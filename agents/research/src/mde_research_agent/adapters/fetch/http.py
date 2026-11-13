"""HttpFetcher: the FetchPort adapter. Reads one public page over HTTP and returns its main text.

Baseline web hygiene, not a security boundary:

* Public http(s) URLs only. ``public_url_problem`` runs on the asked URL and on EVERY redirect hop
  (redirects are followed by hand, at most ``max_redirects``), and the host name is resolved and
  refused when ANY address it resolves to is not public. Known gap: the name is resolved here and
  again by the HTTP client when it connects, so a DNS server that changes its answer in between (DNS
  rebinding) is not caught. Accepted for this use; pin the connection to the checked address if the
  fetcher ever serves untrusted callers.
* robots.txt is read once per host per fetcher and obeyed (see ``robots.py``). Crawl-delay is
  exposed through ``crawl_delay(host)``; the fetcher never sleeps.
* No logins, no paywall or captcha bypass, no cookies kept. A wall is only ever labelled
  (``paywall`` / ``blocked``).
* Limits: total time, body size (streamed, reading stops at the cap), PDF pages, extracted characters.
* ``fetch`` NEVER raises. Every failure is a ``FetchedPage`` with ``text == ""`` and a ``reason``.

The text is stored as ``extract.py`` returns it (NFC, wording untouched); ``content_hash`` is the
sha256 of that text.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from http.cookiejar import DefaultCookiePolicy
from typing import Final, TypeVar
from urllib.parse import urljoin, urlsplit

import httpx

from mde_research_agent.adapters.fetch.extract import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_PDF_PAGES,
    content_hash,
    content_kind,
    detect_soft_wall,
    detect_wall,
    extract_body,
    sniff_media_type,
    split_content_type,
)
from mde_research_agent.adapters.fetch.robots import ROBOTS_MAX_BYTES, RobotsCache
from mde_research_agent.core.urls import ip_is_public, public_url_problem, registrable_domain
from mde_research_agent.ports.fetch import FetchedPage, SkipReason

DEFAULT_USER_AGENT: Final = "MobigenResearchBot/1.0 (product research)"
_ACCEPT: Final = "text/html,application/xhtml+xml,text/plain;q=0.9,application/pdf;q=0.9"
_ROBOTS_ACCEPT: Final = "text/plain,*/*;q=0.1"
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_STATUS_REASONS: Final[Mapping[int, SkipReason]] = {
    401: "paywall",
    402: "paywall",
    403: "blocked",
    429: "blocked",
    404: "not_found",
    410: "not_found",
}
# public_url_problem() codes -> the reason a caller sees
_PROBLEM_REASONS: Final[Mapping[str, SkipReason]] = {
    "private_address": "private",
    "private_name": "private",
    "port": "private",
    "userinfo": "private",
    "invalid_url": "error",
    "scheme": "error",
    "no_host": "error",
}
_TRANSIENT_STATUSES: Final = frozenset({500, 502, 503, 504})
_SNIFF_TYPES: Final = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_SNIFF_BYTES: Final = 1024
_MAX_URL_CHARS: Final = 4096
_NAT64: Final = ipaddress.IPv6Network("64:ff9b::/96")

Resolver = Callable[[str], Awaitable[list[str]]]
_T = TypeVar("_T")


async def system_resolver(host: str) -> list[str]:
    """Default resolver: the event loop's ``getaddrinfo``; every address the name resolves to."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _address_is_public(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])  # drop an IPv6 zone id
    except ValueError:
        return False
    # NAT64 embeds an IPv4 address (64:ff9b::7f00:1 is 127.0.0.1): judge that one too
    embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF) if ip in _NAT64 else None
    return ip_is_public(ip) and (embedded is None or ip_is_public(embedded))


def _resolver_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def _declared_length(headers: httpx.Headers) -> int | None:
    raw = headers.get("content-length", "").strip()
    if not (raw.isascii() and raw.isdigit()):
        return None
    return int(raw) if len(raw) <= 18 else 10**18


class _Skip(Exception):
    """Abandon this fetch with a reason; ``HttpFetcher.fetch`` turns it into a page."""

    def __init__(self, reason: SkipReason) -> None:
        super().__init__(reason)
        self.reason: SkipReason = reason


@dataclass
class _State:
    """What is known about one fetch so far (the page a failure turns into carries it)."""

    url: str
    final_url: str
    status: int | None = None
    content_type: str = ""
    title: str = ""
    published: date | None = None
    bytes_read: int = 0
    retry_after: float | None = None

    def failed(self, reason: SkipReason) -> FetchedPage:
        return FetchedPage(
            url=self.url,
            final_url=self.final_url,
            text="",
            status=self.status,
            content_type=self.content_type,
            title=self.title,
            published=self.published,
            reason=reason,
            bytes_read=self.bytes_read,
            retry_after=self.retry_after,
        )


def retry_after_seconds(raw: str | None, *, now: datetime | None = None) -> float | None:
    """A Retry-After header as seconds: a number of seconds, or an HTTP date; None when absent or unreadable."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.isascii() and text.isdigit():
        return float(int(text)) if len(text) <= 9 else None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


class HttpFetcher:
    """FetchPort over httpx. One instance per run: it owns the robots.txt cache and the per-domain
    limiter. A client passed in is the caller's to close; one created here is closed by ``aclose``.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        accept_language: str | None = None,
        timeout: float = 10.0,
        robots_timeout: float = 5.0,
        max_bytes: int = 5 * 1024 * 1024,
        max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
        max_chars: int = DEFAULT_MAX_CHARS,
        min_text_chars: int = 200,
        max_redirects: int = 5,
        per_host_concurrency: int = 2,
        total_timeout: float = 40.0,
        extract_concurrency: int = 3,
        retries: int = 1,
        retry_backoff_s: float = 0.5,
        retry_after_cap_s: float = 10.0,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False)
            # no state from one page to the next (metered paywalls, consent cookies)
            client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        self._client = client
        self._resolver: Resolver = resolver or system_resolver
        self._user_agent = user_agent
        self._timeout = timeout  # each network request (connect, read); the page as a whole may take ``total_timeout``
        self._total_timeout = max(total_timeout, timeout)
        # Text extraction runs in threads and is CPU-bound: ten at once fight for the interpreter lock, slow the event
        # loop and time every fetch out (first live Big run, 2026-11-13: 347 timeouts, 27 pages read of 335 tried).
        self._extract_sem = asyncio.Semaphore(max(1, extract_concurrency))
        self._robots_timeout = robots_timeout
        self._max_bytes = max_bytes
        self._max_pdf_pages = max_pdf_pages
        self._max_chars = max_chars
        self._min_text_chars = min_text_chars
        self._max_redirects = max(max_redirects, 0)
        self._per_host = per_host_concurrency
        self._retries = max(0, retries)
        self._backoff = retry_backoff_s
        self._retry_after_cap = retry_after_cap_s
        self._sleep: Callable[[float], Awaitable[None]] = sleeper or asyncio.sleep
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._headers = {"User-Agent": user_agent, "Accept": _ACCEPT}
        language = "".join(ch for ch in (accept_language or "") if " " <= ch <= "~").strip()
        if language:
            self._headers["Accept-Language"] = language
        self._robots = RobotsCache(self._load_robots, user_agent=user_agent)

    # ------------------------------------------------------------------------------------- public

    @property
    def user_agent(self) -> str:
        return self._user_agent

    def crawl_delay(self, host: str) -> float | None:
        """The Crawl-delay (seconds) ``host`` asked for in its cached robots.txt; None if none."""
        return self._robots.crawl_delay(host)

    async def aclose(self) -> None:
        """Close the HTTP client, unless it was passed in."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> HttpFetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def fetch(self, url: str) -> FetchedPage:
        """Read ``url``. Never raises; see the module docstring for the reasons a page can fail.

        One more try (``retries``) after a failure that is probably temporary: a timeout, a 500, 502, 503 or 504, a
        connection problem, or a 429 whose Retry-After is within ``retry_after_cap_s``. A login, a paywall, robots, a
        missing page, a page with no text and a page that is too large are never retried.
        """
        page = await self._fetch_once(url)
        attempts = 1
        while (
            attempts <= self._retries
            and (wait := self._wait_before_retry(page, attempts)) is not None
        ):
            await self._sleep(wait)
            page = await self._fetch_once(url)
            attempts += 1
        return page if attempts == 1 else replace(page, attempts=attempts)

    def _wait_before_retry(self, page: FetchedPage, attempt: int) -> float | None:
        """Seconds to wait before another try, or None when the failure is not worth one."""
        if page.ok:
            return None
        if page.reason == "blocked" and page.status == 429:
            asked = page.retry_after
            if asked is None:
                return self._backoff * attempt
            return asked if asked <= self._retry_after_cap else None
        if page.reason == "timeout" or (
            page.reason == "error" and (page.status is None or page.status in _TRANSIENT_STATUSES)
        ):
            return self._backoff * attempt
        return None

    async def _fetch_once(self, url: str) -> FetchedPage:
        state = _State(url=url, final_url=url)
        try:
            async with self._host_slot(url), asyncio.timeout(self._total_timeout):
                return await self._fetch(state)
        except _Skip as skip:
            return state.failed(skip.reason)
        except (httpx.TimeoutException, TimeoutError):
            return state.failed("timeout")
        except Exception:  # the contract is "never raises": any other failure is an error page
            return state.failed("error")

    # ----------------------------------------------------------------------------- the page fetch

    @contextlib.asynccontextmanager
    async def _host_slot(self, url: str) -> AsyncIterator[None]:
        """At most ``per_host_concurrency`` fetches at once per registrable domain (waiting here does
        not count against the fetch timeout)."""
        domain = registrable_domain(url) if self._per_host >= 1 else ""
        if not domain:
            yield
            return
        semaphore = self._semaphores.get(domain)
        if semaphore is None:
            semaphore = self._semaphores[domain] = asyncio.Semaphore(self._per_host)
        async with semaphore:
            yield

    async def _fetch(self, state: _State) -> FetchedPage:
        async def on_hop(hop_url: str) -> None:
            state.final_url = hop_url  # it passed the public check, so it is a fair final_url
            if not await self._robots.allowed(hop_url):
                raise _Skip("robots")

        async def handle(_hop_url: str, response: httpx.Response) -> FetchedPage:
            return await self._consume(state, response)

        return await self._follow(
            state.url.strip(),
            headers=self._headers,
            request_timeout=self._timeout,
            on_hop=on_hop,
            handle=handle,
        )

    async def _consume(self, state: _State, response: httpx.Response) -> FetchedPage:
        status = response.status_code
        state.status = status
        media, charset = split_content_type(response.headers.get("content-type", ""))
        state.content_type = media
        reason = _STATUS_REASONS.get(status)
        if status == 429:
            state.retry_after = retry_after_seconds(response.headers.get("retry-after"))
        if reason is not None:
            raise _Skip(reason)  # a login or paywall is labelled, never worked around
        if not 200 <= status < 300:
            raise _Skip("error")
        sniff = media in _SNIFF_TYPES
        if not sniff and content_kind(media) is None:
            raise _Skip("unsupported")
        declared = _declared_length(response.headers)
        if declared is not None and declared > self._max_bytes:
            raise _Skip("too_large")
        body, sniffed = await self._read_body(state, response, sniff=sniff)
        if sniffed:
            media = state.content_type = sniffed
        async with self._extract_sem:
            extracted = await asyncio.to_thread(
                extract_body,
                body,
                media,
                charset,
                max_pages=self._max_pdf_pages,
                max_chars=self._max_chars,
            )
        state.title, state.published = extracted.title, extracted.published
        text = extracted.text
        if len(text) < self._min_text_chars:
            raise _Skip(detect_wall(extracted.title, text, state.final_url) or "no_content")
        soft = detect_soft_wall(extracted.title, text, state.final_url)
        if (
            soft is not None
        ):  # a soft 404 or a wall that came back as content: not research material
            raise _Skip(soft)
        return FetchedPage(
            url=state.url,
            final_url=state.final_url,
            text=text,
            status=status,
            content_type=media,
            title=extracted.title,
            published=extracted.published,
            content_hash=content_hash(text),
            reason=None,
            bytes_read=state.bytes_read,
        )

    async def _read_body(
        self, state: _State, response: httpx.Response, *, sniff: bool
    ) -> tuple[bytes, str]:
        """Stream the body. Stops and gives up the moment it passes ``max_bytes``; the rest is never
        read. With ``sniff`` (no usable Content-Type) the first bytes decide pdf, html or unsupported.
        """
        buffer = bytearray()
        sniffed = ""
        async for chunk in response.aiter_bytes():
            buffer += chunk
            state.bytes_read = len(buffer)
            if sniff and not sniffed and len(buffer) >= _SNIFF_BYTES:
                sniffed = sniff_media_type(bytes(buffer[:_SNIFF_BYTES]))
                if not sniffed:
                    raise _Skip("unsupported")
            if len(buffer) > self._max_bytes:
                raise _Skip("too_large")
        if sniff and not sniffed:
            sniffed = sniff_media_type(bytes(buffer))
            if not sniffed:
                raise _Skip("unsupported")
        return bytes(buffer), sniffed

    # ---------------------------------------------------------------------------------- hygiene

    async def _check_public(self, url: str) -> None:
        """Refuse a URL that is not public by its text, or whose host resolves to a non-public
        address (ANY resolved address). Raises ``_Skip``."""
        problem = public_url_problem(url)
        if problem is not None:
            raise _Skip(_PROBLEM_REASONS.get(problem, "private"))
        host = (urlsplit(url).hostname or "").rstrip(".")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass  # a name, not an IP literal: resolve it below
        else:
            if not _address_is_public(host):
                raise _Skip("private")
            return
        try:
            addresses = await self._resolver(_resolver_host(host))
        except OSError as exc:  # gaierror and friends: the name does not resolve
            raise _Skip("error") from exc
        if not addresses:
            raise _Skip("error")
        if not all(_address_is_public(address) for address in addresses):
            raise _Skip("private")

    async def _follow(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        request_timeout: float,
        on_hop: Callable[[str], Awaitable[None]],
        handle: Callable[[str, httpx.Response], Awaitable[_T]],
    ) -> _T:
        """GET ``url``, following redirects by hand. Every hop is checked for being public (text and
        DNS) before it is requested; ``on_hop`` may add checks (robots). ``handle`` gets the first
        response that is not a redirect, still open for streaming."""
        current = url
        for _ in range(self._max_redirects + 1):
            if len(current) > _MAX_URL_CHARS:
                raise _Skip("error")
            await self._check_public(current)
            await on_hop(current)
            async with self._client.stream(
                "GET",
                current,
                headers=headers,
                timeout=httpx.Timeout(request_timeout),
                follow_redirects=False,
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location", "").strip()
                    if not location:
                        raise _Skip("error")
                    current = urljoin(current, location)
                    continue
                return await handle(current, response)
        raise _Skip("error")  # redirect chain too long

    # ----------------------------------------------------------------------------------- robots

    async def _load_robots(self, origin: str) -> tuple[int, bytes] | None:
        """``<origin>/robots.txt`` as (status, body), or None when it could not be fetched. Same
        hygiene as a page (public hops only); a failure here means "no robots file"."""

        async def on_hop(_url: str) -> None:
            return None

        async def handle(_url: str, response: httpx.Response) -> tuple[int, bytes]:
            if not 200 <= response.status_code < 300:
                return response.status_code, b""
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer += chunk
                if len(buffer) >= ROBOTS_MAX_BYTES:
                    break  # the rest is ignored (RFC 9309), and not read
            return response.status_code, bytes(buffer[:ROBOTS_MAX_BYTES])

        try:
            async with asyncio.timeout(self._robots_timeout):
                return await self._follow(
                    f"{origin}/robots.txt",
                    headers={**self._headers, "Accept": _ROBOTS_ACCEPT},
                    request_timeout=self._robots_timeout,
                    on_hop=on_hop,
                    handle=handle,
                )
        except (_Skip, httpx.HTTPError, TimeoutError, ValueError):
            return None  # unreachable: allowed
