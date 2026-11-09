"""Read a public web page safely (IN-06, IN-10): an SSRF guard around an injected fetcher.

The page is UNTRUSTED DATA. This module only ever turns bytes into text sections; it never follows links in the
page, never runs scripts and gives the text to no tool.

The guard, in order, for the URL and again for EVERY redirect hop (we drive redirects ourselves, the fetcher
never follows them; at most ``limits.url_max_redirects``):

1. scheme is http or https; no credentials in the URL; no backslashes, spaces or control characters; port
   is the default or one of 80, 443, 8080, 8443;
2. the host is normalised (IDNA / punycode, lower case, no trailing dot). IP literals are understood in every
   spelling an operating system would accept (dotted decimal, one-number decimal ``2130706433``, hex
   ``0x7f000001``, octal ``0177.0.0.1``, short forms ``127.1``, IPv6 in brackets, IPv4-mapped IPv6);
   a malformed number-looking host is refused; single-label and obviously internal names are refused;
3. ALL addresses the injected ``resolver`` returns for the name must be public: private, loopback, link-local,
   multicast, reserved, unspecified, carrier-grade NAT (100.64/10), cloud metadata (169.254.169.254,
   fd00:ec2::254), IPv4-mapped / 6to4 / NAT64 / Teredo forms of those, all block the URL;
4. the connection is made to the VALIDATED address (``fetcher(url, timeout, pinned_ip)``; the production fetcher
   from ``make_httpx_fetcher`` pins the IP and sets Host and SNI to the original name), so a DNS answer that
   changes between check and connect cannot reach an internal address.

Then: 10 s overall deadline, 2 MB body cap (stream, stop, refuse), content types text/html, text/plain and
application/pdf only, scripts and hidden elements dropped from HTML. A 401/403 or a bounce to a sign-in page is
``private_link`` with a friendly message. No regex runs over untrusted text here.
"""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx

from mde_compass.config import IntakeLimits
from mde_compass.intake.errors import IntakeError
from mde_compass.intake.htmltext import extract_html
from mde_compass.intake.model import ParsedDocument, Section, clean_text
from mde_compass.intake.parsers import decode_text, parse_document, parse_text

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
ALLOWED_TYPES = frozenset({"text/html", "text/plain", "application/pdf"})
MAX_ADDRESSES = 16
MAX_CONNECT_TRIES = 3
_BAD_CODECS = frozenset({"utf-7"})
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
_PRIVATE_STATUS = frozenset({401, 403, 407})
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".corp",
    ".home",
    ".lan",
    ".home.arpa",
)
_LOGIN_SEGMENTS = frozenset(
    {
        "login",
        "log-in",
        "signin",
        "sign-in",
        "sign_in",
        "sso",
        "oauth",
        "oauth2",
        "auth",
        "authorize",
        "accounts",
        "session",
        "saml",
    }
)
_LOGIN_HOST_PREFIXES = ("login.", "accounts.", "auth.", "sso.", "signin.", "id.")
_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT (also Alibaba metadata 100.100.100.200)
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local, includes the cloud metadata address 169.254.169.254
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "::/8",
        "100::/64",
        "2001::/32",  # Teredo
        "2001:db8::/32",
        "2002::/16",  # 6to4 (embeds an IPv4 address we would have to trust)
        "64:ff9b:1::/48",
        "fc00::/7",  # unique local, includes the EC2 metadata address fd00:ec2::254
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    )
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


# --- injected ports ----------------------------------------------------------------------------------


@dataclass
class FetchResponse:
    """One HTTP response, redirects NOT followed. ``headers`` keys are lower case."""

    status: int
    headers: Mapping[str, str]
    body_stream: AsyncIterator[bytes]


Resolver = Callable[[str], Awaitable[list[str]]]
Fetcher = Callable[[str, float, str], Awaitable[FetchResponse]]
"""``fetcher(url, timeout_seconds, pinned_ip)``: connect to ``pinned_ip`` (already validated), send the
original host name as Host and SNI, do NOT follow redirects, return the response with a body stream."""


async def system_resolver(host: str) -> list[str]:
    """Production resolver: every A and AAAA record through the event loop's resolver."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]).partition("%")[0] for info in infos))


def make_httpx_fetcher(
    *, transport: httpx.AsyncBaseTransport | None = None, user_agent: str = "CompassReader/1.0"
) -> Fetcher:
    """Production fetcher: connects to the validated IP, Host and SNI are the original name, no redirects,
    no proxy or environment settings, certificates verified against the original name.

    ``transport`` is for tests (``httpx.MockTransport``).
    """

    async def fetch(url: str, timeout_s: float, pinned_ip: str) -> FetchResponse:
        parts = urlsplit(url)
        host = parts.hostname or ""
        netloc = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        if parts.port is not None:
            netloc += f":{parts.port}"
        target = parts._replace(netloc=netloc, fragment="").geturl()
        host_header = f"{host}:{parts.port}" if parts.port is not None else host
        client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout_s,
            transport=transport,
            trust_env=False,
            headers={"User-Agent": user_agent},
        )
        try:
            request = client.build_request(
                "GET",
                target,
                headers={"Host": host_header, "Accept": "text/html,text/plain,application/pdf"},
                extensions={"sni_hostname": host},
            )
            response = await client.send(request, stream=True)
        except BaseException:
            await client.aclose()
            raise

        async def body() -> AsyncIterator[bytes]:
            try:
                async for part in response.aiter_bytes():
                    yield part
            finally:
                await response.aclose()
                await client.aclose()

        headers = {k.lower(): v for k, v in response.headers.items()}
        return FetchResponse(response.status_code, headers, body())

    return fetch


# --- address and URL validation ------------------------------------------------------------------------

_DIGITS = {16: "0123456789abcdefABCDEF", 8: "01234567", 10: "0123456789"}
_NUMBERISH = frozenset("0123456789abcdefABCDEFxX.")


def _part_number(part: str) -> int | None:
    base, digits = 10, part
    if part[:2].lower() == "0x":
        base, digits = 16, part[2:]
    elif len(part) > 1 and part[0] == "0":
        base, digits = 8, part[1:]
    if len(digits) > 16:
        return None  # longer than any real address part (and keeps int() cheap on hostile input)
    if digits == "":
        return 0 if base == 16 else None
    if any(c not in _DIGITS[base] for c in digits):
        return None
    return int(digits, base)


def parse_loose_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """``inet_aton`` semantics: 1 to 4 parts, each decimal, octal (leading 0) or hex (0x)."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or "" in parts:
        return None
    numbers: list[int] = []
    for part in parts:
        value = _part_number(part)
        if value is None:
            return None
        numbers.append(value)
    *lead, last = numbers
    if any(n > 255 for n in lead) or last >= 256 ** (5 - len(parts)):
        return None
    total = last
    for i, n in enumerate(lead):
        total += n << (24 - 8 * i)
    return ipaddress.IPv4Address(total)


def ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for anything that is not a plain public unicast address (see the module docstring)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip_is_blocked(ip.ipv4_mapped)
        if ip in _NAT64:
            return ip_is_blocked(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if any(ip in net for net in _NETS):
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


@dataclass(frozen=True)
class ValidatedUrl:
    url: str  # normalised: scheme://host[:port]/path?query (no fragment, punycode host)
    scheme: str
    host: str
    port: int | None
    literal_ip: str | None = None


def _normalise_name(host: str) -> str:
    name = host.rstrip(".").lower()
    if not name or len(name) > 253:
        raise ValueError("bad host")
    try:
        ascii_name = name.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("bad host") from exc
    if len(ascii_name) > 253 or any(not 0 < len(label) <= 63 for label in ascii_name.split(".")):
        raise ValueError("bad host")
    return ascii_name


def display_url(url: str) -> str:
    """The URL as shown to the user and stored as the source name: no credentials, query or fragment."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname or ""
    except ValueError:
        return "that link"
    shown = f"{parts.scheme}://{host}{parts.path}"
    return clean_text(shown)[:200] if host else "that link"


def validate_url(url: str) -> ValidatedUrl:
    """Check one URL (no DNS yet). Raises ``IntakeError('blocked_url')`` with a plain reason."""
    shown = display_url(url)

    def refuse(reason: str) -> IntakeError:
        return IntakeError("blocked_url", shown, reason)

    text = url.strip()
    if not text or len(text) > 2048:
        raise refuse("empty or too long")
    if "\\" in text or any(ord(c) < 0x21 or ord(c) == 0x7F for c in text):
        raise refuse("illegal characters")
    try:
        parts: SplitResult = urlsplit(text)
        port = parts.port
        host = parts.hostname
    except ValueError as exc:
        raise refuse("unparsable") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise refuse("only http and https links are allowed")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise refuse("credentials in a link are not allowed")
    if not host or "%" in host:
        raise refuse("no host")
    if port is not None and port not in ALLOWED_PORTS:
        raise refuse("unusual port")

    literal: str | None = None
    if ":" in host:  # IPv6 literal (urlsplit stripped the brackets)
        try:
            ip6 = ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise refuse("bad IPv6 address") from exc
        if ip_is_blocked(ip6):
            raise refuse("address is not public")
        literal, name = str(ip6), f"[{ip6}]"
    else:
        try:
            name = _normalise_name(
                host
            )  # IDNA: full-width dots and look-alikes become plain ASCII first
        except ValueError as exc:
            raise refuse("bad host name") from exc
        ip4 = parse_loose_ipv4(name)
        if ip4 is not None:
            if ip_is_blocked(ip4):
                raise refuse("address is not public")
            literal, name = str(ip4), str(ip4)
        else:
            if name[:1].isdigit() and all(c in _NUMBERISH for c in name):
                raise refuse("malformed address")
            if "." not in name or name == "localhost" or name.endswith(_BLOCKED_SUFFIXES):
                raise refuse("not a public host name")
    netloc = name if port is None else f"{name}:{port}"
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return ValidatedUrl(
        url=f"{parts.scheme.lower()}://{netloc}{path}{query}",
        scheme=parts.scheme.lower(),
        host=name,
        port=port,
        literal_ip=literal,
    )


async def resolve_public(host: str, resolver: Resolver, shown: str) -> list[str]:
    """Resolve ``host`` and return its addresses, refusing if ANY of them is not public."""
    try:
        raw = await resolver(host)
    except IntakeError:
        raise
    except Exception as exc:
        raise IntakeError("fetch_failed", shown, "name lookup failed") from exc
    if not raw:
        raise IntakeError("fetch_failed", shown, "name did not resolve")
    addresses: list[str] = []
    for item in list(raw)[:MAX_ADDRESSES]:
        try:
            ip = ipaddress.ip_address(str(item).partition("%")[0])
        except ValueError as exc:
            raise IntakeError("blocked_url", shown, "bad address from the resolver") from exc
        if ip_is_blocked(ip):
            raise IntakeError("blocked_url", shown, "the name points to a private address")
        addresses.append(str(ip))
    return addresses


# --- fetching ---------------------------------------------------------------------------------------------


def _looks_like_login(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if host.startswith(_LOGIN_HOST_PREFIXES):
        return True
    segments = [s.lower() for s in parts.path.split("/") if s]
    return any(s in _LOGIN_SEGMENTS for s in segments)


async def _close(stream: AsyncIterator[bytes]) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await cast("Any", aclose)()
    except Exception:  # closing a stream we are abandoning must never mask the real outcome
        return


async def _read_body(resp: FetchResponse, cap: int, shown: str) -> bytes:
    length = resp.headers.get("content-length", "").strip()
    if length.isdigit() and int(length) > cap:
        await _close(resp.body_stream)
        raise IntakeError("too_large", shown, "page is larger than 2 MB")
    buf = bytearray()
    try:
        async for part in resp.body_stream:
            buf += part
            if len(buf) > cap:
                raise IntakeError("too_large", shown, "page is larger than 2 MB")
    finally:
        await _close(resp.body_stream)
    return bytes(buf)


def _mime(headers: Mapping[str, str]) -> tuple[str, str]:
    raw = headers.get("content-type", "")
    mime, _, params = raw.partition(";")
    charset = ""
    for param in params.split(";"):
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset":
            charset = value.strip().strip("\"'").lower()
    return mime.strip().lower(), charset


def _decode(body: bytes, charset: str) -> str:
    """Decode with the declared charset when it is an ordinary text codec; UTF-7 and exotic codecs are ignored."""
    if charset and charset not in ("utf-8", "utf8"):
        try:
            codec = codecs.lookup(charset)
        except LookupError:
            return decode_text(body)
        if codec.name not in _BAD_CODECS:
            try:
                return body.decode(codec.name, errors="replace")
            except (LookupError, UnicodeError):
                pass
    return decode_text(body)


async def _fetch(
    fetcher: Fetcher, target: ValidatedUrl, addresses: list[str], timeout_s: float, shown: str
) -> FetchResponse:
    last: Exception | None = None
    for ip in addresses[:MAX_CONNECT_TRIES]:
        try:
            return await fetcher(target.url, timeout_s, ip)
        except IntakeError:
            raise
        except Exception as exc:
            last = exc
    raise IntakeError("fetch_failed", shown, "could not connect") from last


async def _read(
    url: str, limits: IntakeLimits, resolver: Resolver, fetcher: Fetcher
) -> ParsedDocument:
    shown = display_url(url)
    current = url
    for _hop in range(limits.url_max_redirects + 1):
        target = validate_url(current)
        addresses = (
            [target.literal_ip]
            if target.literal_ip
            else await resolve_public(target.host, resolver, shown)
        )
        resp = await _fetch(fetcher, target, addresses, limits.url_timeout_s, shown)
        status = resp.status
        if status in _REDIRECT_STATUS:
            location = resp.headers.get("location", "").strip()
            await _close(resp.body_stream)
            if not location:
                raise IntakeError("fetch_failed", shown, "redirect without a target")
            current = urljoin(target.url, location)
            if _looks_like_login(current):
                raise IntakeError("private_link", shown)
            continue
        if status in _PRIVATE_STATUS:
            await _close(resp.body_stream)
            raise IntakeError("private_link", shown)
        if not 200 <= status < 300:
            await _close(resp.body_stream)
            raise IntakeError("fetch_failed", shown, f"status {status}")
        mime, charset = _mime(resp.headers)
        if mime not in ALLOWED_TYPES:
            await _close(resp.body_stream)
            raise IntakeError("unsupported", shown, f"content type {mime or '(none)'}")
        body = await _read_body(resp, limits.url_max_bytes, shown)
        return _to_document(body, mime, charset, shown, limits)
    raise IntakeError("too_many_redirects", shown)


def _to_document(
    body: bytes, mime: str, charset: str, shown: str, limits: IntakeLimits
) -> ParsedDocument:
    if mime == "application/pdf":
        try:
            doc = parse_document("page.pdf", body, limits=limits)
        except IntakeError as exc:
            raise IntakeError(exc.code, shown, exc.detail) from exc
        return ParsedDocument(kind="url", pages=doc.pages, sections=doc.sections, notes=doc.notes)
    text = _decode(body, charset)
    if mime == "text/plain":
        plain = parse_text(text)
        doc = ParsedDocument(kind="url", pages=plain.pages, sections=plain.sections)
    else:
        page = extract_html(text)
        if page.has_password_field and page.text_chars < 600:
            raise IntakeError("private_link", shown)
        sections: list[Section] = page.sections
        doc = ParsedDocument(
            kind="url", pages=1 if sections else 0, sections=sections, notes=page.notes
        )
    if not doc.sections:
        doc.notes.append("no_readable_text")
    return doc


async def read_url(
    url: str,
    *,
    limits: IntakeLimits,
    resolver: Resolver | None = None,
    fetcher: Fetcher | None = None,
) -> ParsedDocument:
    """Fetch and parse one public page. Raises ``IntakeError`` (``blocked_url``, ``private_link``,
    ``too_large``, ``unsupported``, ``too_many_redirects``, ``fetch_failed`` ...). Offline callers inject both
    ports; production passes nothing and gets ``system_resolver`` and ``make_httpx_fetcher()``.
    """
    shown = display_url(url)
    try:
        async with asyncio.timeout(limits.url_timeout_s):
            return await _read(
                url, limits, resolver or system_resolver, fetcher or make_httpx_fetcher()
            )
    except TimeoutError as exc:
        raise IntakeError("fetch_failed", shown, "timed out") from exc
