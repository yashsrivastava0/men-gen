"""Offline tests for robots.txt handling: the parser wrapper, the per-host cache, and the fetcher
obeying it. No network: ``httpx.MockTransport`` and an injected resolver."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

import httpx
import pytest

from mde_research_agent.adapters.fetch.http import DEFAULT_USER_AGENT, HttpFetcher
from mde_research_agent.adapters.fetch.robots import (
    ROBOTS_MAX_BYTES,
    RobotsCache,
    RobotsResponse,
    origin_of,
    parse_robots,
    product_token,
)
from mde_research_agent.ports.fetch import SkipReason

ARTICLE = (
    "<html><head><title>Cold chain notes</title></head><body><article>"
    + "".join(
        f"<p>Paragraph {n}: produce kept cold between farm and market loses less value on the way.</p>"
        for n in range(8)
    )
    + "</article></body></html>"
)
UA = DEFAULT_USER_AGENT
HOST = "https://shop.example.com"
ROBOTS_URL = f"{HOST}/robots.txt"

Reply = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


# ------------------------------------------------------------------------------------ helpers


class Web:
    def __init__(self, routes: Mapping[str, Reply] | None = None) -> None:
        self.routes: dict[str, Reply] = dict(routes or {})
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> Any:
        self.requests.append(request)
        route = self.routes.get(str(request.url))
        if route is not None:
            return route(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=ARTICLE.encode())

    def robots_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/robots.txt"]

    def page_urls(self) -> list[str]:
        return [str(r.url) for r in self.requests if r.url.path != "/robots.txt"]


def robots_txt(body: str | bytes, status: int = 200, ctype: str | None = "text/plain") -> Reply:
    data = body.encode("utf-8") if isinstance(body, str) else body

    def make(_request: httpx.Request) -> httpx.Response:
        headers = {"content-type": ctype} if ctype else {}
        return httpx.Response(status, headers=headers, content=data)

    return make


def raises(exc: Exception) -> Reply:
    def make(_request: httpx.Request) -> httpx.Response:
        raise exc

    return make


def redirect(to: str) -> Reply:
    return lambda _r: httpx.Response(301, headers={"location": to})


async def public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.fixture
async def build() -> AsyncIterator[Callable[..., HttpFetcher]]:
    clients: list[httpx.AsyncClient] = []

    def factory(web: Web, **kwargs: object) -> HttpFetcher:
        client = httpx.AsyncClient(transport=httpx.MockTransport(web), follow_redirects=False)
        clients.append(client)
        kwargs.setdefault("resolver", public_resolver)
        return HttpFetcher(client=client, **kwargs)  # type: ignore[arg-type]

    yield factory
    for client in clients:
        await client.aclose()


# ---------------------------------------------------------------------- parse_robots (pure)


def allows(body: str, url: str, *, status: int | None = 200, ua: str = UA) -> bool:
    return parse_robots(status, body.encode("utf-8"), user_agent=ua).allows(url)


def test_product_token_and_origin() -> None:
    assert product_token("MobigenResearchBot/1.0 (product research)") == "MobigenResearchBot"
    assert product_token("  Acme-Bot_2 ") == "Acme-Bot_2"
    assert product_token("") == "*"
    assert product_token("(weird)") == "*"
    assert origin_of("HTTPS://Shop.Example.com:443/a?b=1#c") == "https://shop.example.com"
    assert origin_of("http://shop.example.com:80/") == "http://shop.example.com"
    assert origin_of("http://shop.example.com:8080/x") == "http://shop.example.com:8080"
    assert origin_of("https://[2606:4700::1111]/x") == "https://[2606:4700::1111]"
    for bad in ("ftp://x.com/", "mailto:a@b.c", "not a url", "", "http://", "http://[::1"):
        assert origin_of(bad) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, True),  # could not be fetched at all
        (404, True),
        (410, True),
        (401, True),
        (403, True),
        (429, True),
        (418, True),
        (500, False),
        (502, False),
        (503, False),
        (599, False),
        (301, True),  # nothing to obey
        (200, True),  # an empty file allows everything
    ],
)
def test_status_decides_before_the_body(status: int | None, expected: bool) -> None:
    rules = parse_robots(status, b"", user_agent=UA)
    assert rules.allows("https://shop.example.com/any/page") is expected


def test_a_body_is_ignored_unless_the_status_is_2xx() -> None:
    deny_all = "User-agent: *\nDisallow: /\n"
    assert allows(deny_all, "https://x.com/a", status=404) is True
    assert allows(deny_all, "https://x.com/a", status=None) is True
    assert allows(deny_all, "https://x.com/a", status=200) is False
    assert allows("User-agent: *\nAllow: /\n", "https://x.com/a", status=503) is False


def test_basic_rules_and_comments() -> None:
    body = """
    # a comment
    User-agent: *
    Disallow: /private/   # trailing comment
    Disallow: /tmp
    Allow: /private/public.html
    """
    assert allows(body, "https://x.com/") is True
    assert allows(body, "https://x.com/blog/post") is True
    assert allows(body, "https://x.com/private/secret") is False
    assert allows(body, "https://x.com/private/public.html") is True  # longest match wins
    assert allows(body, "https://x.com/tmp") is False
    assert allows(body, "https://x.com/tmpfile") is False  # prefix match
    assert allows(body, "https://x.com/robots.txt") is True  # always readable


def test_wildcards_and_end_anchor() -> None:
    body = "User-agent: *\nDisallow: /*.pdf$\nDisallow: /search?*sort=\nDisallow: /a*b*c\n"
    assert allows(body, "https://x.com/files/report.pdf") is False
    assert allows(body, "https://x.com/files/report.pdf?download=1") is True  # $ anchors the end
    assert allows(body, "https://x.com/files/report.pdfx") is True
    assert allows(body, "https://x.com/search?q=1&sort=asc") is False
    assert allows(body, "https://x.com/search?q=1") is True
    assert allows(body, "https://x.com/a/xx/b/yy/c") is False
    assert allows(body, "https://x.com/a/b") is True


def test_our_group_beats_the_star_group() -> None:
    body = """
    User-agent: *
    Disallow: /

    User-agent: MobigenResearchBot
    Disallow: /admin
    """
    assert allows(body, "https://x.com/story") is True
    assert allows(body, "https://x.com/admin/panel") is False
    assert allows(body, "https://x.com/story", ua="SomeOtherBot/1.0") is False
    # matching is case-insensitive and by product token
    assert (
        allows(body.replace("MobigenResearchBot", "mobigenresearchbot"), "https://x.com/admin")
        is False
    )


def test_a_group_for_another_bot_does_not_apply_to_us() -> None:
    body = "User-agent: Googlebot\nDisallow: /\n\nUser-agent: BadBot\nDisallow: /\n"
    assert allows(body, "https://x.com/story") is True


def test_disallow_all_for_our_token() -> None:
    assert allows("User-agent: MobigenResearchBot\nDisallow: /\n", "https://x.com/story") is False


def test_empty_disallow_means_allow_everything() -> None:
    assert allows("User-agent: *\nDisallow:\n", "https://x.com/anything") is True


def test_garbage_and_html_bodies_allow_everything() -> None:
    assert allows("<html><body>Not found, but with a 200</body></html>", "https://x.com/a") is True
    assert allows("\x00\x01 total garbage ::: \n::\n:", "https://x.com/a") is True
    assert allows("Disallow: /\n", "https://x.com/a") is True  # a rule with no User-agent group
    parse_robots(200, bytes(range(256)) * 50, user_agent=UA)  # must not raise


def test_a_byte_order_mark_does_not_hide_the_first_line() -> None:
    assert allows("﻿User-agent: *\nDisallow: /\n", "https://x.com/a") is False


def test_crawl_delay_is_exposed() -> None:
    body = "User-agent: *\nCrawl-delay: 3\n\nUser-agent: MobigenResearchBot\nCrawl-delay: 7\nDisallow: /x\n"
    assert parse_robots(200, body.encode(), user_agent=UA).crawl_delay() == 7.0
    assert parse_robots(200, body.encode(), user_agent="OtherBot/1").crawl_delay() == 3.0
    assert parse_robots(200, b"User-agent: *\nDisallow: /x\n", user_agent=UA).crawl_delay() is None
    assert parse_robots(404, b"", user_agent=UA).crawl_delay() is None
    assert (
        parse_robots(200, b"User-agent: *\nCrawl-delay: soon\n", user_agent=UA).crawl_delay()
        is None
    )


def test_rules_past_the_size_cap_are_ignored_not_fatal() -> None:
    padding = "# padding line\n" * (ROBOTS_MAX_BYTES // 15 + 10)
    body = f"User-agent: *\nDisallow: /early\n{padding}Disallow: /late\n"
    assert len(body) > ROBOTS_MAX_BYTES
    rules = parse_robots(200, body.encode(), user_agent=UA)
    assert rules.allows("https://x.com/early") is False
    assert rules.allows("https://x.com/late") is True


# ----------------------------------------------------------------------- RobotsCache (unit)


class Loader:
    def __init__(
        self, answers: Mapping[str, RobotsResponse | Exception | None] | None = None
    ) -> None:
        self.answers = dict(answers or {})
        self.calls: list[str] = []
        self.gate: asyncio.Event | None = None

    async def __call__(self, origin: str) -> RobotsResponse | None:
        self.calls.append(origin)
        if self.gate is not None:
            await self.gate.wait()
        answer = self.answers.get(origin, (404, b""))
        if isinstance(answer, Exception):
            raise answer
        return answer


DENY = (200, b"User-agent: *\nDisallow: /private\n")


async def test_cache_loads_once_per_origin() -> None:
    loader = Loader({"https://a.com": DENY})
    cache = RobotsCache(loader, user_agent=UA)
    assert await cache.allowed("https://a.com/ok") is True
    assert await cache.allowed("https://a.com/private/x") is False
    assert await cache.allowed("https://a.com/other?x=1") is True
    assert loader.calls == ["https://a.com"]
    assert await cache.allowed("https://b.com/private/x") is True  # another host, its own file
    assert loader.calls == ["https://a.com", "https://b.com"]
    assert cache.cached_origins() == ("https://a.com", "https://b.com")


async def test_scheme_and_port_make_distinct_origins() -> None:
    loader = Loader()
    cache = RobotsCache(loader, user_agent=UA)
    for url in ("http://a.com/x", "https://a.com/x", "https://a.com:8443/x", "https://A.com/y"):
        await cache.allowed(url)
    assert loader.calls == ["http://a.com", "https://a.com", "https://a.com:8443"]


async def test_concurrent_callers_share_one_load() -> None:
    loader = Loader({"https://a.com": DENY})
    loader.gate = asyncio.Event()
    cache = RobotsCache(loader, user_agent=UA)
    tasks = [asyncio.create_task(cache.allowed(f"https://a.com/private/{i}")) for i in range(8)]
    for _ in range(20):
        await asyncio.sleep(0)
    assert loader.calls == ["https://a.com"]  # the others are waiting on the first
    loader.gate.set()
    assert await asyncio.gather(*tasks) == [False] * 8
    assert loader.calls == ["https://a.com"]


async def test_failures_are_cached_as_their_policy() -> None:
    loader = Loader(
        {
            "https://down.com": None,
            "https://boom.com": RuntimeError("loader bug"),
            "https://err.com": (503, b""),
            "https://gone.com": (404, b""),
            "https://locked.com": (403, b""),
        }
    )
    cache = RobotsCache(loader, user_agent=UA)
    assert await cache.allowed("https://down.com/x") is True
    assert await cache.allowed("https://boom.com/x") is True
    assert await cache.allowed("https://err.com/x") is False
    assert await cache.allowed("https://gone.com/x") is True
    assert await cache.allowed("https://locked.com/x") is True
    for url in ("down", "boom", "err", "gone", "locked"):
        await cache.allowed(f"https://{url}.com/again")
    assert len(loader.calls) == 5  # nothing is asked twice


async def test_cache_expires_with_the_clock() -> None:
    now = [1000.0]
    loader = Loader({"https://a.com": DENY})
    cache = RobotsCache(loader, user_agent=UA, ttl_seconds=60, clock=lambda: now[0])
    await cache.allowed("https://a.com/x")
    now[0] += 59
    await cache.allowed("https://a.com/x")
    assert loader.calls == ["https://a.com"]
    now[0] += 2
    loader.answers["https://a.com"] = (200, b"User-agent: *\nDisallow: /\n")
    assert await cache.allowed("https://a.com/x") is False  # the new file is in force
    assert loader.calls == ["https://a.com", "https://a.com"]


async def test_cache_is_bounded() -> None:
    loader = Loader()
    cache = RobotsCache(loader, user_agent=UA, max_origins=2)
    for host in ("a", "b", "c"):
        await cache.allowed(f"https://{host}.com/x")
    assert cache.cached_origins() == ("https://b.com", "https://c.com")
    await cache.allowed("https://a.com/x")  # evicted, so it is loaded again
    assert loader.calls.count("https://a.com") == 2


async def test_a_cancelled_load_is_not_cached() -> None:
    loader = Loader({"https://a.com": DENY})
    loader.gate = asyncio.Event()
    cache = RobotsCache(loader, user_agent=UA)
    task = asyncio.create_task(cache.allowed("https://a.com/private"))
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cache.cached_origins() == ()
    loader.gate.set()
    assert await cache.allowed("https://a.com/private") is False


async def test_non_http_urls_are_allowed_without_a_load() -> None:
    loader = Loader()
    cache = RobotsCache(loader, user_agent=UA)
    assert await cache.allowed("ftp://a.com/x") is True
    assert await cache.allowed("not a url") is True
    assert loader.calls == []


async def test_crawl_delay_lookup_by_host_or_url() -> None:
    loader = Loader({"https://a.com": (200, b"User-agent: *\nCrawl-delay: 4\n")})
    cache = RobotsCache(loader, user_agent=UA)
    assert cache.crawl_delay("a.com") is None  # nothing cached yet: no guess
    await cache.allowed("https://a.com/x")
    await cache.allowed("https://b.com/x")
    assert cache.crawl_delay("a.com") == 4.0
    assert cache.crawl_delay("A.COM.") == 4.0
    assert cache.crawl_delay("https://a.com/some/page") == 4.0
    assert cache.crawl_delay("b.com") is None
    assert cache.crawl_delay("unknown.org") is None


# ------------------------------------------------------------- the fetcher obeys robots.txt


async def test_allowed_page_is_fetched_after_robots_once(build: Callable[..., HttpFetcher]) -> None:
    web = Web({ROBOTS_URL: robots_txt("User-agent: *\nDisallow: /admin\n")})
    fetcher = build(web)
    first = await fetcher.fetch(f"{HOST}/story/1")
    second = await fetcher.fetch(f"{HOST}/story/2")
    assert first.ok and second.ok
    assert [r.url.path for r in web.requests] == ["/robots.txt", "/story/1", "/story/2"]
    robots = web.robots_requests()[0]
    assert robots.headers["user-agent"] == UA
    assert "text/plain" in robots.headers["accept"]


async def test_disallowed_page_is_not_requested(build: Callable[..., HttpFetcher]) -> None:
    web = Web({ROBOTS_URL: robots_txt("User-agent: *\nDisallow: /\n")})
    page = await build(web).fetch(f"{HOST}/story")
    assert page.reason == "robots" and page.text == "" and not page.ok
    assert page.status is None and page.bytes_read == 0
    assert page.url == page.final_url == f"{HOST}/story"
    assert web.page_urls() == []


async def test_rules_apply_per_path_and_robots_is_read_once(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web(
        {ROBOTS_URL: robots_txt("User-agent: *\nDisallow: /members/\nAllow: /members/free\n")}
    )
    fetcher = build(web)
    verdicts = {
        path: (await fetcher.fetch(f"{HOST}{path}")).reason
        for path in ("/news", "/members/area", "/members/free", "/members/free/guide")
    }
    assert verdicts == {
        "/news": None,
        "/members/area": "robots",
        "/members/free": None,
        "/members/free/guide": None,
    }
    assert len(web.robots_requests()) == 1


async def test_only_our_own_group_can_shut_us_out(build: Callable[..., HttpFetcher]) -> None:
    ours = Web({ROBOTS_URL: robots_txt("User-agent: MobigenResearchBot\nDisallow: /\n")})
    assert (await build(ours).fetch(f"{HOST}/story")).reason == "robots"
    theirs = Web({ROBOTS_URL: robots_txt("User-agent: GPTBot\nDisallow: /\n")})
    assert (await build(theirs).fetch(f"{HOST}/story")).ok
    custom = Web({ROBOTS_URL: robots_txt("User-agent: AcmeBot\nDisallow: /\n")})
    assert (
        await build(custom, user_agent="AcmeBot/9.9 (test)").fetch(f"{HOST}/story")
    ).reason == "robots"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_robots_5xx_means_disallowed(build: Callable[..., HttpFetcher], status: int) -> None:
    web = Web({ROBOTS_URL: robots_txt("User-agent: *\nAllow: /\n", status=status)})
    fetcher = build(web)
    page = await fetcher.fetch(f"{HOST}/story")
    assert page.reason == "robots" and web.page_urls() == []
    assert (
        await fetcher.fetch(f"{HOST}/other")
    ).reason == "robots"  # for that host, from the cache
    assert len(web.robots_requests()) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429])
async def test_robots_4xx_means_no_rules(build: Callable[..., HttpFetcher], status: int) -> None:
    web = Web({ROBOTS_URL: robots_txt("User-agent: *\nDisallow: /\n", status=status)})
    page = await build(web).fetch(f"{HOST}/story")
    assert page.ok  # the body of an error response is not a rule file


@pytest.mark.parametrize(
    "unreachable",
    [
        raises(httpx.ConnectError("refused")),
        raises(httpx.ReadTimeout("slow")),
        raises(httpx.RemoteProtocolError("bad")),
        raises(RuntimeError("boom")),
        redirect(
            "http://10.0.0.1/robots.txt"
        ),  # a robots redirect into a private address is refused
        redirect("/robots.txt"),  # a redirect loop
    ],
    ids=["connect", "timeout", "protocol", "bug", "private redirect", "loop"],
)
async def test_unreachable_robots_means_no_rules(
    build: Callable[..., HttpFetcher], unreachable: Reply
) -> None:
    web = Web({ROBOTS_URL: unreachable})
    fetcher = build(web)
    assert (await fetcher.fetch(f"{HOST}/a")).ok
    assert (await fetcher.fetch(f"{HOST}/b")).ok
    assert len(web.robots_requests()) <= 6  # a loop is cut at the redirect limit, then cached
    assert len([r for r in web.requests if r.url.host == "10.0.0.1"]) == 0


async def test_slow_robots_does_not_hold_the_page_hostage(
    build: Callable[..., HttpFetcher],
) -> None:
    async def never(_request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    web = Web({ROBOTS_URL: never})
    page = await build(web, robots_timeout=0.05).fetch(f"{HOST}/story")
    assert page.ok


async def test_robots_redirect_is_followed(build: Callable[..., HttpFetcher]) -> None:
    web = Web(
        {
            ROBOTS_URL: redirect(f"{HOST}/static/robots.txt"),
            f"{HOST}/static/robots.txt": robots_txt("User-agent: *\nDisallow: /secret\n"),
        }
    )
    fetcher = build(web)
    assert (await fetcher.fetch(f"{HOST}/secret/x")).reason == "robots"
    assert (await fetcher.fetch(f"{HOST}/open")).ok


async def test_robots_page_that_is_html_means_no_rules(build: Callable[..., HttpFetcher]) -> None:
    html = "<html><body><h1>Welcome</h1> Disallow everything? No.</body></html>"
    web = Web({ROBOTS_URL: robots_txt(html, ctype="text/html")})
    assert (await build(web).fetch(f"{HOST}/story")).ok


async def test_huge_robots_file_is_read_up_to_the_cap_only(
    build: Callable[..., HttpFetcher],
) -> None:
    pulled: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        yield b"User-agent: *\nDisallow: /early\n"
        for i in range(2000):  # about 2 MB of comments
            pulled.append(i)
            yield b"# padding " + b"x" * 1000 + b"\n"
        yield b"Disallow: /late\n"

    def big(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=body())

    web = Web({ROBOTS_URL: big})
    fetcher = build(web)
    assert (await fetcher.fetch(f"{HOST}/early")).reason == "robots"
    assert (await fetcher.fetch(f"{HOST}/late")).ok
    assert len(pulled) < 600  # stopped at about 512 KiB, did not read the rest


async def test_each_host_has_its_own_robots_file(build: Callable[..., HttpFetcher]) -> None:
    other = "https://blog.example.org"
    web = Web(
        {
            ROBOTS_URL: robots_txt("User-agent: *\nDisallow: /\n"),
            f"{other}/robots.txt": robots_txt("User-agent: *\nDisallow: /nope\n"),
        }
    )
    fetcher = build(web)
    assert (await fetcher.fetch(f"{HOST}/a")).reason == "robots"
    assert (await fetcher.fetch(f"{other}/a")).ok
    assert (await fetcher.fetch(f"{other}/nope")).reason == "robots"
    assert (
        await fetcher.fetch("https://other.example.com/a")
    ).ok  # a sibling host, not the same file
    assert sorted(r.url.host for r in web.robots_requests()) == [
        "blog.example.org",
        "other.example.com",
        "shop.example.com",
    ]


async def test_concurrent_fetches_of_one_host_read_robots_once(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web({ROBOTS_URL: robots_txt("User-agent: *\nDisallow: /x\n")})
    fetcher = build(web, per_host_concurrency=0)  # let them all run at once
    pages = await asyncio.gather(*(fetcher.fetch(f"{HOST}/p{i}") for i in range(6)))
    assert all(p.ok for p in pages)
    assert len(web.robots_requests()) == 1


async def test_a_redirect_hop_is_checked_against_the_robots_of_the_hop_host(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web(
        {
            f"{HOST}/go": redirect("https://cdn.example.net/article"),
            "https://cdn.example.net/robots.txt": robots_txt("User-agent: *\nDisallow: /article\n"),
        }
    )
    page = await build(web).fetch(f"{HOST}/go")
    assert page.reason == "robots"
    assert page.final_url == "https://cdn.example.net/article"  # public, and where the refusal was
    assert web.page_urls() == [f"{HOST}/go"]  # the disallowed hop was never requested


async def test_robots_is_not_asked_for_urls_that_fail_the_public_check(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web()
    page = await build(web).fetch("http://127.0.0.1/story")
    assert page.reason == "private" and web.requests == []


async def test_crawl_delay_is_exposed_by_the_fetcher_and_never_slept(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web({ROBOTS_URL: robots_txt("User-agent: *\nCrawl-delay: 30\n")})
    fetcher = build(web, timeout=5.0)
    assert fetcher.crawl_delay("shop.example.com") is None  # not fetched yet
    pages = [await fetcher.fetch(f"{HOST}/p{i}") for i in range(3)]  # back to back: no 30 s waits
    assert all(p.ok for p in pages)
    assert fetcher.crawl_delay("shop.example.com") == 30.0
    assert fetcher.crawl_delay(f"{HOST}/p1") == 30.0
    assert fetcher.crawl_delay("elsewhere.example.org") is None


async def test_robots_reasons_are_the_documented_ones(build: Callable[..., HttpFetcher]) -> None:
    seen: set[SkipReason | None] = set()
    for status, rules in ((200, "User-agent: *\nDisallow: /\n"), (503, ""), (404, "")):
        web = Web({ROBOTS_URL: robots_txt(rules, status=status)})
        seen.add((await build(web).fetch(f"{HOST}/story")).reason)
    assert seen == {"robots", None}
