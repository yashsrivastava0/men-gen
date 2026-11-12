"""One more try after a failure that is probably temporary (D-163, robustness): timeouts, 5xx, connection errors and a
429 that says how long to wait; never after a wall, a missing page or a page with no text. Offline: MockTransport."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from mde_research_agent.adapters.fetch.http import HttpFetcher, retry_after_seconds
from mde_research_agent.ports.fetch import FetchedPage

URL = "https://news.example.com/story"
PUBLIC_IP = "93.184.216.34"
BODY = (
    "<html><head><title>Story</title></head><body><article>"
    + ("A long enough sentence about cold storage. " * 30)
    + "</article></body></html>"
)


async def _public(_host: str) -> list[str]:
    return [PUBLIC_IP]


class Script:
    """Answers page requests from a list (robots.txt always allows); records the sleeps the fetcher asked for."""

    def __init__(self, *answers: Callable[[], httpx.Response]) -> None:
        self.answers, self.pages, self.sleeps = list(answers), 0, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"}
            )
        self.pages += 1
        make = self.answers[min(self.pages - 1, len(self.answers) - 1)]
        return make()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def page(status: int = 200, **headers: str) -> Callable[[], httpx.Response]:
    def make() -> httpx.Response:
        if status == 200:
            return httpx.Response(
                200, text=BODY, headers={"content-type": "text/html; charset=utf-8"}
            )
        return httpx.Response(status, headers=headers)

    return make


def boom(exc: Exception) -> Callable[[], httpx.Response]:
    def make() -> httpx.Response:
        raise exc

    return make


@pytest.fixture
async def run() -> AsyncIterator[Callable[..., object]]:
    clients: list[httpx.AsyncClient] = []

    async def go(script: Script, **kwargs: object) -> FetchedPage:
        client = httpx.AsyncClient(transport=httpx.MockTransport(script), follow_redirects=False)
        clients.append(client)
        fetcher = HttpFetcher(client=client, resolver=_public, sleeper=script.sleep, **kwargs)  # type: ignore[arg-type]
        return await fetcher.fetch(URL)

    yield go
    for c in clients:
        await c.aclose()


async def test_a_503_then_a_good_page_is_read_after_one_backoff(run) -> None:  # type: ignore[no-untyped-def]
    script = Script(page(503), page(200))
    got = await run(script)
    assert got.ok and got.attempts == 2 and script.pages == 2 and script.sleeps == [0.5]


async def test_a_timeout_is_tried_once_more(run) -> None:  # type: ignore[no-untyped-def]
    script = Script(boom(httpx.ReadTimeout("slow")), page(200))
    got = await run(script)
    assert got.ok and got.attempts == 2


async def test_a_connection_error_is_tried_once_more_and_then_reported(run) -> None:  # type: ignore[no-untyped-def]
    script = Script(boom(httpx.ConnectError("refused")))
    got = await run(script)
    assert not got.ok and got.reason == "error" and got.attempts == 2 and script.pages == 2


async def test_a_429_waits_what_it_asked_and_a_long_wait_is_not_worth_it(run) -> None:  # type: ignore[no-untyped-def]
    short = Script(page(429, **{"retry-after": "3"}), page(200))
    got = await run(short)
    assert got.ok and short.sleeps == [3.0]
    long = Script(page(429, **{"retry-after": "120"}), page(200))
    gave_up = await run(long)
    assert not gave_up.ok and gave_up.reason == "blocked" and gave_up.retry_after == 120.0
    assert long.pages == 1 and long.sleeps == [] and gave_up.attempts == 1


@pytest.mark.parametrize("status", [401, 402, 403, 404, 410])
async def test_walls_and_missing_pages_are_never_retried(run, status: int) -> None:  # type: ignore[no-untyped-def]
    script = Script(page(status), page(200))
    got = await run(script)
    assert not got.ok and script.pages == 1 and got.attempts == 1


async def test_a_page_with_no_text_is_never_retried(run) -> None:  # type: ignore[no-untyped-def]
    def empty() -> httpx.Response:
        return httpx.Response(
            200, text="<html><body>hi</body></html>", headers={"content-type": "text/html"}
        )

    script = Script(empty, page(200))
    got = await run(script)
    assert got.reason == "no_content" and script.pages == 1


async def test_only_one_retry_is_made_by_default_and_none_when_switched_off(run) -> None:  # type: ignore[no-untyped-def]
    script = Script(page(503))
    assert (await run(script)).attempts == 2 and script.pages == 2
    off = Script(page(503), page(200))
    got = await run(off, retries=0)
    assert not got.ok and off.pages == 1 and got.attempts == 1


def test_retry_after_reads_seconds_and_dates_and_ignores_the_rest() -> None:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    assert retry_after_seconds("7") == 7.0
    assert (
        retry_after_seconds(format_datetime(now + timedelta(seconds=30), usegmt=True), now=now)
        == 30.0
    )
    assert (
        retry_after_seconds(format_datetime(now - timedelta(seconds=30), usegmt=True), now=now)
        == 0.0
    )
    for junk in (None, "", "soon", "-5", "9" * 30):
        assert retry_after_seconds(junk) is None
