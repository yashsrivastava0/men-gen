"""Offline tests for HttpFetcher: every network call is an ``httpx.MockTransport``, every DNS answer
is injected. Nothing here touches the real network."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import re
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest
from pypdf import PdfWriter

from mde_research_agent.adapters.fetch.http import DEFAULT_USER_AGENT, HttpFetcher
from mde_research_agent.ports.fetch import FetchedPage, FetchPort, SkipReason

PAGES = Path(__file__).parent.parent / "fixtures" / "pages"
ARTICLE = (PAGES / "article_en.html").read_text(encoding="utf-8")
ARTICLE_TITLE = "How cold storage is changing produce logistics in Nashik"
URL = "https://news.example.com/story"
PUBLIC_IP = "93.184.216.34"
REASONS = frozenset(get_args(SkipReason))

Reply = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


# ------------------------------------------------------------------------------------ helpers


class Web:
    """A scripted web: URL -> reply, anything else goes to ``default`` (404). Records every request."""

    def __init__(self, routes: Mapping[str, Reply] | None = None, default: Reply | None = None):
        self.routes: dict[str, Reply] = dict(routes or {})
        self.default = default
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> Any:
        self.requests.append(request)
        route = self.routes.get(str(request.url)) or self.default
        return route(request) if route is not None else httpx.Response(404)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]

    def hosts(self) -> set[str]:
        return {r.url.host for r in self.requests}

    def page_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/robots.txt"]


def reply(
    body: bytes | str = b"",
    *,
    status: int = 200,
    ctype: str | None = "text/html; charset=utf-8",
    headers: Mapping[str, str] | None = None,
) -> Reply:
    data = body.encode("utf-8") if isinstance(body, str) else body

    def make(_request: httpx.Request) -> httpx.Response:
        merged = {"content-type": ctype} if ctype else {}
        merged.update(headers or {})
        return httpx.Response(status, headers=merged, content=data)

    return make


def redirect(to: str, status: int = 302) -> Reply:
    return reply(b"", status=status, ctype=None, headers={"location": to})


def raises(exc: Exception) -> Reply:
    def make(_request: httpx.Request) -> httpx.Response:
        raise exc

    return make


async def public_resolver(_host: str) -> list[str]:
    return [PUBLIC_IP]


def resolving_to(*addresses: str) -> Callable[[str], Awaitable[list[str]]]:
    async def resolve(_host: str) -> list[str]:
        return list(addresses)

    return resolve


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


def check_invariants(page: FetchedPage, url: str) -> None:
    assert isinstance(page, FetchedPage)
    assert page.url == url
    assert page.ok == bool(page.text)
    if page.text:
        assert page.reason is None
        assert page.content_hash == hashlib.sha256(page.text.encode("utf-8")).hexdigest()
    else:
        assert page.reason in REASONS
        assert page.content_hash == ""


def build_pdf(pages: list[list[str]]) -> bytes:
    """A small valid PDF with a text layer: one list of lines per page (ASCII, Helvetica)."""
    count = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(count))
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for i, lines in enumerate(pages):
        stream = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({x}) Tj T*" for x in lines) + " ET"
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode("latin-1"))
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def report_pdf(page_count: int = 3) -> bytes:
    return build_pdf(
        [
            [
                f"Page {p} line {n}: keeping produce in a cold chain protects quality."
                for n in range(6)
            ]
            for p in range(1, page_count + 1)
        ]
    )


def scanned_pdf() -> bytes:
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(612, 792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------------------- the happy path


async def test_reads_an_article(build: Callable[..., HttpFetcher]) -> None:
    web = Web({URL: reply(ARTICLE)})
    fetcher = build(web)
    assert isinstance(fetcher, FetchPort)
    page = await fetcher.fetch(URL)
    assert page.ok and page.reason is None
    assert page.url == URL and page.final_url == URL
    assert page.status == 200 and page.content_type == "text/html"
    assert page.title == ARTICLE_TITLE
    assert page.published == date(2024, 3, 5)
    assert page.text_source == "fetch"
    assert "Ten years ago a tomato grown near Nashik" in page.text
    assert "Privacy Policy" not in page.text and "newsletter" not in page.text.lower()
    assert page.content_hash == hashlib.sha256(page.text.encode("utf-8")).hexdigest()
    assert page.bytes_read == len(ARTICLE.encode("utf-8"))
    # robots.txt first, then the page; nothing else
    assert web.urls() == ["https://news.example.com/robots.txt", URL]


async def test_request_headers(build: Callable[..., HttpFetcher]) -> None:
    web = Web({URL: reply(ARTICLE)})
    await build(web, accept_language="hi-IN,hi;q=0.9,en;q=0.5").fetch(URL)
    request = web.page_requests()[0]
    assert request.method == "GET"
    assert request.headers["user-agent"] == DEFAULT_USER_AGENT
    assert re.fullmatch(r"MobigenResearchBot/\d+\.\d+ \(product research\)", DEFAULT_USER_AGENT)
    assert "@" not in DEFAULT_USER_AGENT and "://" not in DEFAULT_USER_AGENT  # no personal data
    for wanted in ("text/html", "application/xhtml+xml", "text/plain", "application/pdf"):
        assert wanted in request.headers["accept"]
    assert request.headers["accept-language"] == "hi-IN,hi;q=0.9,en;q=0.5"
    robots = web.requests[0]
    assert robots.url.path == "/robots.txt" and robots.headers["user-agent"] == DEFAULT_USER_AGENT
    assert robots.headers["accept-language"] == "hi-IN,hi;q=0.9,en;q=0.5"


async def test_no_accept_language_unless_asked_and_user_agent_is_overridable(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web({URL: reply(ARTICLE)})
    fetcher = build(web, user_agent="AcmeBot/2.0 (research)")
    await fetcher.fetch(URL)
    assert "accept-language" not in web.page_requests()[0].headers
    assert web.page_requests()[0].headers["user-agent"] == "AcmeBot/2.0 (research)"
    assert fetcher.user_agent == "AcmeBot/2.0 (research)"


async def test_accept_language_cannot_inject_headers(build: Callable[..., HttpFetcher]) -> None:
    web = Web({URL: reply(ARTICLE)})
    page = await build(web, accept_language="hi\r\nX-Evil: 1").fetch(URL)
    assert page.ok
    assert "x-evil" not in web.page_requests()[0].headers


async def test_follows_redirects_by_hand_and_reports_the_final_url(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web(
        {
            "https://a.example.com/start": redirect("/middle"),
            "https://a.example.com/middle": redirect("https://b.example.org/end?x=1", 301),
            "https://b.example.org/end?x=1": reply(ARTICLE),
        }
    )
    page = await build(web).fetch("https://a.example.com/start")
    assert page.ok
    assert page.url == "https://a.example.com/start"
    assert page.final_url == "https://b.example.org/end?x=1"


async def test_five_redirects_are_followed_and_a_sixth_is_not(
    build: Callable[..., HttpFetcher],
) -> None:
    def chain(length: int) -> Web:
        routes: dict[str, Reply] = {
            f"https://r.example.com/{i}": redirect(f"/{i + 1}") for i in range(length)
        }
        routes[f"https://r.example.com/{length}"] = reply(ARTICLE)
        return Web(routes)

    assert (await build(chain(5)).fetch("https://r.example.com/0")).ok
    page = await build(chain(6)).fetch("https://r.example.com/0")
    assert page.reason == "error" and page.text == ""


async def test_redirect_loop_and_bad_redirects_are_errors(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web(
        {
            "https://l.example.com/a": redirect("/b"),
            "https://l.example.com/b": redirect("/a"),
            "https://l.example.com/nolocation": reply(b"", status=302, ctype=None),
            "https://l.example.com/emptylocation": redirect(""),
        }
    )
    fetcher = build(web)
    for path in ("a", "nolocation", "emptylocation"):
        page = await fetcher.fetch(f"https://l.example.com/{path}")
        assert page.reason == "error", path


# -------------------------------------------------------------------------------- public only

PRIVATE_TARGETS = [
    "http://10.0.0.5/admin",
    "http://192.168.1.1/",
    "http://172.16.0.9/",
    "http://127.0.0.1:8080/",
    "http://localhost/admin",
    "http://LOCALHOST:8443/",
    "http://2130706433/",  # decimal spelling of 127.0.0.1
    "http://0x7f000001/",
    "http://0x7f.0.0.1/",
    "http://017700000001/",  # octal
    "http://127.1/",
    "http://0.0.0.0/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[fe80::1]/",
    "http://169.254.169.254/latest/meta-data/",
    "http://printer.local/",
    "http://metadata.internal/",
    "http://intranet/",
    "https://example.com:22/",
    "http://svc.localhost/",
]


@pytest.mark.parametrize("target", PRIVATE_TARGETS)
async def test_redirect_hop_to_a_private_address_is_refused(
    build: Callable[..., HttpFetcher], target: str
) -> None:
    start = "https://a.example.com/start"
    web = Web({start: redirect(target)}, default=reply(ARTICLE))  # would "work" if it were fetched
    page = await build(web).fetch(start)
    assert page.reason == "private" and page.text == "" and not page.ok
    assert page.final_url == start  # the last public place we were, never the private hop
    assert web.hosts() == {"a.example.com"}


async def test_private_hop_in_the_middle_of_a_chain(build: Callable[..., HttpFetcher]) -> None:
    web = Web(
        {
            "https://a.example.com/1": redirect("https://b.example.com/2"),
            "https://b.example.com/2": redirect("http://10.0.0.1/secret"),
        },
        default=reply(ARTICLE),
    )
    page = await build(web).fetch("https://a.example.com/1")
    assert page.reason == "private"
    assert page.final_url == "https://b.example.com/2"
    assert "10.0.0.1" not in web.hosts()


@pytest.mark.parametrize("target", PRIVATE_TARGETS)
async def test_asked_url_that_is_private_is_never_requested(
    build: Callable[..., HttpFetcher], target: str
) -> None:
    calls: list[str] = []

    async def resolver(host: str) -> list[str]:
        calls.append(host)
        return [PUBLIC_IP]

    web = Web(default=reply(ARTICLE))
    page = await build(web, resolver=resolver).fetch(target)
    assert page.reason == "private" and page.text == ""
    assert page.url == target and page.final_url == target
    assert web.requests == []  # not even robots.txt


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/f", "file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", ""],
)
async def test_non_http_urls_are_refused_without_a_request(
    build: Callable[..., HttpFetcher], url: str
) -> None:
    web = Web(default=reply(ARTICLE))
    page = await build(web).fetch(url)
    assert page.reason == "error" and page.text == "" and web.requests == []


async def test_userinfo_urls_are_refused(build: Callable[..., HttpFetcher]) -> None:
    web = Web(default=reply(ARTICLE))
    page = await build(web).fetch("https://user:pass@example.com/")
    assert page.reason == "private" and web.requests == []


@pytest.mark.parametrize(
    "answers",
    [
        ["10.1.2.3"],
        ["127.0.0.1"],
        ["::1"],
        ["192.168.0.10"],
        ["169.254.169.254"],
        ["100.64.0.1"],  # carrier-grade NAT
        [PUBLIC_IP, "10.0.0.1"],  # ANY address being private is enough
        ["10.0.0.1", PUBLIC_IP],
        ["64:ff9b::7f00:1"],  # NAT64 wrapper around 127.0.0.1
        ["fe80::1%en0"],
        ["::ffff:10.0.0.1"],
        ["not-an-ip"],
    ],
)
async def test_name_that_resolves_to_a_private_address_is_refused(
    build: Callable[..., HttpFetcher], answers: list[str]
) -> None:
    web = Web(default=reply(ARTICLE))
    page = await build(web, resolver=resolving_to(*answers)).fetch(URL)
    assert page.reason == "private" and page.text == ""
    assert web.requests == []


async def test_redirect_hop_to_a_name_that_resolves_private_is_refused(
    build: Callable[..., HttpFetcher],
) -> None:
    async def resolver(host: str) -> list[str]:
        return ["10.9.9.9"] if host == "sneaky.example.net" else [PUBLIC_IP]

    web = Web({"https://a.example.com/s": redirect("https://sneaky.example.net/x")}, default=None)
    page = await build(web, resolver=resolver).fetch("https://a.example.com/s")
    assert page.reason == "private" and page.final_url == "https://a.example.com/s"
    assert web.hosts() == {"a.example.com"}


async def test_resolver_failures_are_errors(build: Callable[..., HttpFetcher]) -> None:
    async def gone(_host: str) -> list[str]:
        raise OSError("Name or service not known")

    async def odd(_host: str) -> list[str]:
        raise RuntimeError("resolver bug")

    for resolver in (gone, odd, resolving_to()):
        web = Web(default=reply(ARTICLE))
        page = await build(web, resolver=resolver).fetch(URL)
        assert page.reason == "error" and page.text == "" and web.requests == []


async def test_resolver_gets_the_ascii_host_and_ip_literals_skip_it(
    build: Callable[..., HttpFetcher],
) -> None:
    seen: list[str] = []

    async def resolver(host: str) -> list[str]:
        seen.append(host)
        return [PUBLIC_IP]

    web = Web(default=reply(ARTICLE))
    fetcher = build(web, resolver=resolver)
    await fetcher.fetch("https://bücher.example/seite")
    assert seen == ["xn--bcher-kva.example", "xn--bcher-kva.example"]  # page, then robots.txt
    seen.clear()
    page = await fetcher.fetch(f"http://{PUBLIC_IP}/page")
    assert page.ok and seen == []


# -------------------------------------------------------------------------- status and types


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "paywall"),
        (402, "paywall"),
        (403, "blocked"),
        (429, "blocked"),
        (404, "not_found"),
        (410, "not_found"),
        (400, "error"),
        (405, "error"),
        (406, "error"),
        (418, "error"),
        (451, "error"),
        (500, "error"),
        (502, "error"),
        (503, "error"),
        (304, "error"),
        (299 + 700, "error"),
    ],
)
async def test_status_mapping(
    build: Callable[..., HttpFetcher], status: int, reason: SkipReason
) -> None:
    # the body is a perfectly good article: an error status is never read as content
    web = Web({URL: reply(ARTICLE, status=status)})
    page = await build(web).fetch(URL)
    assert page.reason == reason and page.text == "" and page.content_hash == ""
    assert page.status == status
    assert page.bytes_read == 0


async def test_empty_success_is_no_content(build: Callable[..., HttpFetcher]) -> None:
    for status in (200, 204):
        web = Web({URL: reply(b"", status=status)})
        page = await build(web).fetch(URL)
        assert page.reason == "no_content" and page.status == status


async def test_timeouts(build: Callable[..., HttpFetcher]) -> None:
    for exc in (
        httpx.ReadTimeout("read"),
        httpx.ConnectTimeout("connect"),
        httpx.PoolTimeout("pool"),
        httpx.WriteTimeout("write"),
        TimeoutError("asyncio"),
    ):
        page = await build(Web({URL: raises(exc)})).fetch(URL)
        assert page.reason == "timeout" and page.text == "", type(exc)


async def test_total_timeout_covers_a_server_that_never_answers(
    build: Callable[..., HttpFetcher],
) -> None:
    async def never(_request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    page = await build(Web({URL: never}), timeout=0.05).fetch(URL)
    assert page.reason == "timeout" and page.text == ""


async def test_total_timeout_covers_a_body_that_drips(build: Callable[..., HttpFetcher]) -> None:
    async def drip() -> AsyncIterator[bytes]:
        yield b"<html><body><p>start</p>"
        await asyncio.Event().wait()

    web = Web(
        {URL: lambda _r: httpx.Response(200, headers={"content-type": "text/html"}, content=drip())}
    )
    page = await build(web, timeout=0.05).fetch(URL)
    assert page.reason == "timeout"


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ReadError("reset"),
        httpx.RemoteProtocolError("bad framing"),
        httpx.TooManyRedirects("loop"),
        httpx.InvalidURL("bad"),
        httpx.UnsupportedProtocol("ftp"),
        OSError("network is unreachable"),
        RuntimeError("boom"),
        ValueError("boom"),
        KeyError("boom"),
    ],
)
async def test_other_failures_are_errors(build: Callable[..., HttpFetcher], exc: Exception) -> None:
    page = await build(Web({URL: raises(exc)})).fetch(URL)
    assert page.reason == "error" and page.text == ""


# ------------------------------------------------------------------------------------- size


async def test_body_is_streamed_and_reading_stops_at_the_cap(
    build: Callable[..., HttpFetcher],
) -> None:
    pulled: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        for i in range(500):
            pulled.append(i)
            yield b"y" * 1024

    web = Web(
        {URL: lambda _r: httpx.Response(200, headers={"content-type": "text/html"}, content=body())}
    )
    page = await build(web, max_bytes=4096).fetch(URL)
    assert page.reason == "too_large" and page.text == ""
    assert 4096 < page.bytes_read <= 4096 + 1024
    assert len(pulled) <= 6  # stopped right after the cap, did not read the other ~495 chunks


async def test_declared_length_over_the_cap_is_refused_without_reading(
    build: Callable[..., HttpFetcher],
) -> None:
    pulled: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        pulled.append(1)
        yield b"y"

    for declared in ("999999999", "9" * 5000):
        web = Web(
            {
                URL: lambda _r, d=declared: httpx.Response(  # type: ignore[misc]
                    200,
                    headers={"content-type": "text/html", "content-length": d},
                    content=body(),
                )
            }
        )
        page = await build(web, max_bytes=1_000_000).fetch(URL)
        assert page.reason == "too_large" and page.bytes_read == 0
    assert pulled == []


async def test_a_body_exactly_at_the_cap_is_read(build: Callable[..., HttpFetcher]) -> None:
    size = len(ARTICLE.encode("utf-8"))
    web = Web({URL: reply(ARTICLE)})
    assert (await build(web, max_bytes=size).fetch(URL)).ok
    assert (await build(web, max_bytes=size - 1).fetch(URL)).reason == "too_large"


async def test_default_cap_is_five_megabytes(build: Callable[..., HttpFetcher]) -> None:
    page = await build(Web({URL: reply(b"<p>" + b"x" * (5 * 1024 * 1024) + b"</p>")})).fetch(URL)
    assert page.reason == "too_large"


async def test_extracted_text_is_truncated_not_failed(build: Callable[..., HttpFetcher]) -> None:
    long_text = ("The quick brown fox jumps over the lazy dog. " * 400).encode()
    web = Web({URL: reply(long_text, ctype="text/plain; charset=utf-8")})
    page = await build(web, max_chars=1000).fetch(URL)
    assert page.ok and len(page.text) == 1000
    assert (await build(web).fetch(URL)).text == long_text.decode().strip()


# ---------------------------------------------------------------------------------- content


@pytest.mark.parametrize(
    ("ctype", "body"),
    [
        ("image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 300),
        ("application/json", b'{"a": 1}'),
        ("text/css", b"body { color: red }"),
        ("application/zip", b"PK\x03\x04" + b"\x00" * 300),
        ("video/mp4", b"\x00\x00\x00\x18ftyp"),
        ("application/octet-stream", b"PK\x03\x04" + b"\x00" * 2000),
        ("application/octet-stream", b"just some text " * 100),
    ],
)
async def test_unsupported_types(
    build: Callable[..., HttpFetcher], ctype: str, body: bytes
) -> None:
    page = await build(Web({URL: reply(body, ctype=ctype)})).fetch(URL)
    assert page.reason == "unsupported" and page.text == ""
    assert page.content_type == ctype


async def test_supported_types(build: Callable[..., HttpFetcher]) -> None:
    text = ("Plain text pages are fine as they are. " * 20).strip()
    cases = {
        "text/html": ARTICLE,
        "application/xhtml+xml": ARTICLE,
        "text/plain": text,
        "TEXT/HTML; Charset=UTF-8": ARTICLE,
    }
    for ctype, body in cases.items():
        page = await build(Web({URL: reply(body, ctype=ctype)})).fetch(URL)
        assert page.ok, ctype
        assert page.content_type == ctype.split(";")[0].lower()  # media type only
    assert (await build(Web({URL: reply(report_pdf(), ctype="application/pdf")})).fetch(URL)).ok


async def test_missing_or_generic_content_type_is_sniffed(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web(
        {
            "https://s.example.com/html": reply(ARTICLE, ctype=None),
            "https://s.example.com/pdf": reply(report_pdf(), ctype="application/octet-stream"),
            "https://s.example.com/bin": reply(b"\x00\x01" * 3000, ctype=None),
        }
    )
    fetcher = build(web)
    html = await fetcher.fetch("https://s.example.com/html")
    assert html.ok and html.content_type == "text/html"
    pdf = await fetcher.fetch("https://s.example.com/pdf")
    assert pdf.ok and pdf.content_type == "application/pdf" and "Page 1 line 0" in pdf.text
    assert (await fetcher.fetch("https://s.example.com/bin")).reason == "unsupported"


async def test_js_only_shell_is_no_content(build: Callable[..., HttpFetcher]) -> None:
    shell = (PAGES / "js_shell.html").read_text(encoding="utf-8")
    page = await build(Web({URL: reply(shell)})).fetch(URL)
    assert page.reason == "no_content" and page.text == "" and page.content_hash == ""
    assert page.status == 200 and page.title == "Fleetly"


async def test_short_pages_are_no_content_unless_the_minimum_is_lowered(
    build: Callable[..., HttpFetcher],
) -> None:
    web = Web(
        {URL: reply("<html><body><p>Only a short note about nothing much.</p></body></html>")}
    )
    assert (await build(web).fetch(URL)).reason == "no_content"
    assert (await build(web, min_text_chars=10).fetch(URL)).ok


async def test_pdf_text_layer_and_page_cap(build: Callable[..., HttpFetcher]) -> None:
    web = Web({URL: reply(report_pdf(3), ctype="application/pdf")})
    page = await build(web).fetch(URL)
    assert page.ok and page.content_type == "application/pdf"
    assert "Page 1 line 0" in page.text and "Page 3 line 5" in page.text
    assert "\n\n" in page.text  # pages joined by blank lines
    capped = await build(web, max_pdf_pages=1).fetch(URL)
    assert capped.ok and "Page 1 line 0" in capped.text and "Page 2" not in capped.text


async def test_scanned_and_broken_pdfs_are_no_content(build: Callable[..., HttpFetcher]) -> None:
    for data in (scanned_pdf(), b"%PDF-1.4 this is not really a pdf", report_pdf()[:-200]):
        page = await build(Web({URL: reply(data, ctype="application/pdf")})).fetch(URL)
        assert page.reason == "no_content" and page.text == "", data[:12]


@pytest.mark.parametrize(
    ("title", "body", "reason"),
    [
        ("Just a moment...", "<p>Enable JavaScript and cookies to continue</p>", "blocked"),
        ("Attention Required! | Cloudflare", "<p>Sorry, you have been blocked.</p>", "blocked"),
        ("Premium story", "<p>Subscribe to continue reading this article.</p>", "paywall"),
        ("Sign in", "<form><input name=email></form>", "paywall"),
    ],
)
async def test_a_bot_wall_or_login_wall_is_labelled_not_bypassed(
    build: Callable[..., HttpFetcher], title: str, body: str, reason: SkipReason
) -> None:
    html = f"<html><head><title>{title}</title></head><body>{body}</body></html>"
    web = Web({URL: reply(html)})
    page = await build(web).fetch(URL)
    assert page.reason == reason and page.text == ""
    assert len(web.page_requests()) == 1  # no second attempt with other headers or cookies


async def test_a_long_article_that_mentions_captchas_is_still_read(
    build: Callable[..., HttpFetcher],
) -> None:
    paragraph = "Websites use a captcha or a security check to tell people from scripts. " * 6
    html = f"<html><head><title>Access denied to bots</title></head><body><article><p>{paragraph}</p></article></body></html>"
    page = await build(Web({URL: reply(html)})).fetch(URL)
    assert page.ok and "captcha" in page.text


# ---------------------------------------------------------------------------------- languages


@pytest.mark.parametrize("name", ["article_hi.html", "article_kn.html", "article_ar.html"])
async def test_devanagari_kannada_and_arabic_pages_survive_intact(
    build: Callable[..., HttpFetcher], name: str
) -> None:
    html = (PAGES / name).read_text(encoding="utf-8")
    paragraphs = re.findall(r"<p>(.*?)</p>", html, flags=re.DOTALL)[
        :-1
    ]  # the last one is the footer
    assert len(paragraphs) >= 2
    variants = {
        "header": reply(html, ctype="text/html; charset=utf-8"),
        "meta only": reply(html, ctype="text/html"),
        "bom": reply(b"\xef\xbb\xbf" + html.encode("utf-8"), ctype="text/html"),
        "no header, no meta": reply(html.replace('<meta charset="utf-8">', ""), ctype=None),
    }
    for label, route in variants.items():
        page = await build(Web({URL: route})).fetch(URL)
        assert page.ok, label
        for paragraph in paragraphs:
            assert unicodedata.normalize("NFC", paragraph) in page.text, label
        assert "�" not in page.text, label
        assert "Privacy Policy" not in page.text, label


async def test_declared_legacy_charset_and_invalid_utf8(build: Callable[..., HttpFetcher]) -> None:
    sentence = "Le café prépare une crème brûlée délicieuse. " * 8
    html = f"<html><body><article><p>{sentence}</p></article></body></html>"
    legacy = reply(html.encode("windows-1252"), ctype="text/html; charset=windows-1252")
    page = await build(Web({URL: legacy})).fetch(URL)
    assert page.ok and sentence.strip() in page.text
    broken = (
        b"<html><body><article><p>" + b"Valid words here. \xff\xfe broken bytes. " * 12 + b"</p>"
    )
    page = await build(Web({URL: reply(broken)})).fetch(URL)
    assert page.ok and "Valid words here." in page.text and "�" in page.text


# ------------------------------------------------------------------------------------ hashes


async def test_content_hash_is_stable(build: Callable[..., HttpFetcher]) -> None:
    web = Web(
        {URL: reply(ARTICLE), "https://news.example.com/other": reply(ARTICLE + "<!-- x -->")}
    )
    fetcher = build(web)
    first, second = await fetcher.fetch(URL), await fetcher.fetch(URL)
    assert first.content_hash == second.content_hash != ""
    assert (
        await build(web).fetch("https://news.example.com/other")
    ).content_hash == first.content_hash
    changed = ARTICLE.replace("Ten years ago", "Eleven years ago")
    other = await build(Web({URL: reply(changed)})).fetch(URL)
    assert other.content_hash not in ("", first.content_hash)


async def test_content_hash_ignores_unicode_form(build: Callable[..., HttpFetcher]) -> None:
    text = "Café naïve résumé and more words to pass the minimum length. " * 5
    nfc = reply(unicodedata.normalize("NFC", text), ctype="text/plain; charset=utf-8")
    nfd = reply(unicodedata.normalize("NFD", text), ctype="text/plain; charset=utf-8")
    a = await build(Web({URL: nfc})).fetch(URL)
    b = await build(Web({URL: nfd})).fetch(URL)
    assert a.ok and b.ok and a.text == b.text and a.content_hash == b.content_hash


# --------------------------------------------------------------------------- never raises

NASTY_URLS = [
    "",
    " ",
    "\t\n",
    "not a url",
    "http://",
    "https://",
    "//example.com/x",
    "ftp://example.com/f",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "mailto:a@b.c",
    "http://[::1",
    "http://exa mple.com/",
    "http://a_b.example.com/",
    "https://example.com:99999/",
    "https://example.com:abc/",
    "https://user:pw@example.com/",
    "https://example.com/" + "a" * 10_000,
    "https://" + "a" * 300 + ".com/",
    "https://" + ".".join(["a" * 60] * 6) + "/",
    "http://.example.com/",
    "http://example..com/",
    "https://例え.jp/テスト",
    "https://exa\x00mple.com/",
    "https://example.com/\x00\x01",
    "https://example.com/%zz",
    "http://0/",
    "http://1.2.3.4.5/",
    "http://999.999.999.999/",
    "https://example.com/\ud800",
]


@pytest.mark.parametrize("url", NASTY_URLS, ids=[repr(u[:30]) for u in NASTY_URLS])
async def test_never_raises_on_garbage_urls(build: Callable[..., HttpFetcher], url: str) -> None:
    web = Web(default=reply(ARTICLE))
    page = await build(web).fetch(url)
    check_invariants(page, url)


async def test_never_raises_on_a_non_string_url(build: Callable[..., HttpFetcher]) -> None:
    page = await build(Web(default=reply(ARTICLE))).fetch(None)  # type: ignore[arg-type]
    assert page.text == "" and page.reason == "error"


async def exploding_body() -> AsyncIterator[bytes]:
    yield b"<html><body><p>partial</p>"
    raise httpx.ReadError("connection reset mid-body")


async def runtime_exploding_body() -> AsyncIterator[bytes]:
    yield b"<html>"
    raise RuntimeError("generator bug")


def streaming(gen: Callable[[], AsyncIterator[bytes]], ctype: str = "text/html") -> Reply:
    return lambda _r: httpx.Response(200, headers={"content-type": ctype}, content=gen())


NASTY_RESPONSES: dict[str, Reply] = {
    "empty html": reply(b""),
    "empty pdf": reply(b"", ctype="application/pdf"),
    "empty no type": reply(b"", ctype=None),
    "invalid utf8": reply(b"<html><body><p>" + b"\xff\xfe\xfa" * 200 + b"</p></body></html>"),
    "bogus charset": reply(b"<p>" + b"hello " * 100, ctype="text/html; charset=no-such-charset"),
    "weird charset": reply(b"\xff\xfe\x00" * 50, ctype="text/html; charset=utf-16"),
    "non-text codec": reply(b"hello " * 100, ctype="text/plain; charset=base64"),
    "garbage content type": reply(b"x", ctype=';;;charset="'),
    "huge header value": reply(ARTICLE, headers={"x-big": "a" * 200_000}),
    "many headers": reply(ARTICLE, headers={f"x-h{i}": "v" * 50 for i in range(2000)}),
    "huge content type": reply(ARTICLE, ctype="text/html; x=" + "a" * 100_000),
    "bad content-length": reply(ARTICLE, headers={"content-length": "abc"}),
    "negative content-length": reply(b"x", headers={"content-length": "-5"}),
    "huge content-length": reply(b"x", headers={"content-length": "9" * 5000}),
    "garbage gzip": reply(b"this is not gzip data" * 50, headers={"content-encoding": "gzip"}),
    "truncated gzip": reply(
        gzip.compress(ARTICLE.encode())[:40], headers={"content-encoding": "gzip"}
    ),
    "unknown encoding": reply(ARTICLE, headers={"content-encoding": "x-nonsense"}),
    "garbage pdf": reply(b"%PDF-1.7\n" + bytes(range(256)) * 40, ctype="application/pdf"),
    "html as pdf": reply(ARTICLE, ctype="application/pdf"),
    "pdf as html": reply(report_pdf()),
    "deep nesting": reply("<div>" * 4000 + "deep text" + "</div>" * 4000),
    "angle brackets": reply("<" * 5000),
    "nul text": reply(b"\x00" * 3000, ctype="text/plain"),
    "huge single line": reply("word " * 300_000, ctype="text/plain"),
    "redirect without location": reply(b"", status=302, ctype=None),
    "redirect to junk": redirect("http://"),
    "redirect to huge url": redirect("https://example.com/" + "a" * 10_000),
    "redirect to js": redirect("javascript:alert(1)"),
    "redirect to ftp": redirect("ftp://example.com/x"),
    "redirect with spaces": redirect("  /a b \t c  "),
    "redirect to self": redirect("https://news.example.com/story"),
    "informational status": reply(b"", status=199, ctype=None),
    "status 600": reply(b"x", status=600),
    "body dies mid-stream": streaming(exploding_body),
    "generator bug mid-stream": streaming(runtime_exploding_body),
}


@pytest.mark.parametrize("name", list(NASTY_RESPONSES))
async def test_never_raises_on_nasty_responses(
    build: Callable[..., HttpFetcher], name: str
) -> None:
    web = Web({URL: NASTY_RESPONSES[name]})
    page = await build(web).fetch(URL)
    check_invariants(page, URL)


async def test_never_raises_when_robots_misbehaves(build: Callable[..., HttpFetcher]) -> None:
    for robots in (
        raises(RuntimeError("boom")),
        reply(b"\xff\xfe\x00 User-agent: *\n\x00Disallow: /\n", ctype="text/plain"),
        reply(b"x" * 2_000_000, ctype="text/plain"),
        reply(b"Disallow: /\n" * 10, ctype=None),
        redirect("http://10.0.0.1/robots.txt"),
        redirect("/robots.txt"),
    ):
        web = Web({URL: reply(ARTICLE), "https://news.example.com/robots.txt": robots})
        check_invariants(await build(web).fetch(URL), URL)


# ------------------------------------------------------------- concurrency, lifecycle, state


async def _flood(
    build: Callable[..., HttpFetcher], hosts: list[str], limit: int
) -> tuple[int, int, list[FetchedPage]]:
    """Fetch one URL per entry of ``hosts`` at once; returns (running at the first look, the peak
    number running, the pages). Every request holds until released."""
    running = peak = 0
    release = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        running += 1
        peak = max(peak, running)
        try:
            await release.wait()
        finally:
            running -= 1
        return httpx.Response(200, headers={"content-type": "text/html"}, content=ARTICLE.encode())

    fetcher = build(Web(default=slow), per_host_concurrency=limit)
    tasks = [asyncio.create_task(fetcher.fetch(f"https://{h}/p{i}")) for i, h in enumerate(hosts)]
    for _ in range(200):
        await asyncio.sleep(0)
    first_look = running
    release.set()
    pages = list(await asyncio.gather(*tasks))
    return first_look, peak, pages


async def test_per_host_concurrency_limit_is_per_registrable_domain(
    build: Callable[..., HttpFetcher],
) -> None:
    hosts = ["a.example.com", "b.example.com", "example.com", "www.example.com", "c.example.com"]
    first_look, peak, pages = await _flood(build, hosts, limit=2)
    assert first_look == 2 and peak == 2  # subdomains share one slot pool
    assert all(p.ok for p in pages)


async def test_other_domains_are_not_held_up(build: Callable[..., HttpFetcher]) -> None:
    hosts = ["a.example.com", "a.example.org", "a.example.net", "b.example.com"]
    first_look, peak, pages = await _flood(build, hosts, limit=1)
    assert first_look == 3 and peak == 3  # three domains at once, the 4th waits for example.com
    assert all(p.ok for p in pages)


async def test_zero_means_unlimited(build: Callable[..., HttpFetcher]) -> None:
    first_look, peak, _ = await _flood(build, ["a.example.com"] * 5, limit=0)
    assert first_look == 5 and peak == 5


async def test_a_waiting_fetch_does_not_lose_its_time_budget(
    build: Callable[..., HttpFetcher],
) -> None:
    # waiting for a slot is not counted against the fetch timeout: with limit 1 and a short
    # timeout, the second fetch still succeeds after the first has finished
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/first":
            await release.wait()
        return httpx.Response(200, headers={"content-type": "text/html"}, content=ARTICLE.encode())

    fetcher = build(Web(default=handler), per_host_concurrency=1, timeout=5.0)
    first = asyncio.create_task(fetcher.fetch("https://q.example.com/first"))
    second = asyncio.create_task(fetcher.fetch("https://q.example.com/second"))
    for _ in range(100):
        await asyncio.sleep(0)
    assert not second.done()
    release.set()
    assert (await first).ok and (await second).ok


async def test_injected_client_is_not_closed_by_the_fetcher() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(Web()), follow_redirects=False)
    fetcher = HttpFetcher(client=client, resolver=public_resolver)
    await fetcher.aclose()
    assert not client.is_closed
    await client.aclose()


async def test_own_client_is_closed_and_keeps_no_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    web = Web({URL: reply(ARTICLE, headers={"set-cookie": "meter=1; Path=/; Max-Age=3600"})})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(web), **kw)
    )
    async with HttpFetcher(resolver=public_resolver) as fetcher:
        assert (await fetcher.fetch(URL)).ok
        assert (await fetcher.fetch(URL)).ok
        assert fetcher._client.follow_redirects is False
    assert fetcher._client.is_closed
    assert len(web.page_requests()) == 2
    assert all("cookie" not in r.headers for r in web.page_requests())


async def test_nothing_is_shared_between_instances(build: Callable[..., HttpFetcher]) -> None:
    web = Web({URL: reply(ARTICLE)})
    await build(web).fetch(URL)
    await build(web).fetch(URL)
    robots = [r for r in web.requests if r.url.path == "/robots.txt"]
    assert len(robots) == 2  # each fetcher asked for itself
