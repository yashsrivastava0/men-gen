"""URL handling that is pure code: canonical form, registrable domain, public-URL check.

* ``canonical_url`` makes two spellings of one page equal (case, default ports, fragments, tracking
  parameters, parameter order, a leading ``www.``, IDN host) so search results dedupe.
* ``registrable_domain`` is the unit of "independent source": ``docs.stripe.com`` and ``stripe.com``
  are one domain, ``a.co.in`` and ``b.co.in`` are two (an offline public-suffix snapshot via ``tld``).
* ``public_url_problem`` is the basic web hygiene of R13: public http(s) URLs only. It looks at the
  URL text (names and IP literals, including the decimal, octal and IPv4-mapped spellings that hide a
  loopback address); the fetcher also checks what a name RESOLVES to at every redirect hop.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

import tld

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
_TRACKING_PREFIXES = ("utm_", "mc_", "pk_", "hsa_", "ga_")
_TRACKING_PARAMS = frozenset(
    {
        "gclid", "fbclid", "msclkid", "yclid", "dclid", "igshid", "ref", "ref_src", "ref_url",
        "referrer", "source", "src", "spm", "cmpid", "campaign", "trk", "trkcampaign", "_ga", "_gl",
        "mkt_tok", "s_cid", "cid", "ocid", "sessionid", "phpsessid", "jsessionid", "sid",
    }
)  # fmt: skip
_BLOCKED_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".localdomain",
    ".lan",
    ".home",
    ".corp",
    ".intranet",
)
_NUMERIC_HOST = re.compile(
    r"^(?:0x[0-9a-f]+|0[0-7]*|\d+)(?:\.(?:0x[0-9a-f]+|0[0-7]*|\d+)){0,3}$", re.I
)
_SCHEMES = ("http", "https")


def _ascii_host(host: str) -> str:
    host = host.strip().rstrip(".").lower()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def canonical_url(url: str) -> str:
    """Stable form of ``url`` for dedupe. Idempotent. Returns the input stripped when it is not a URL."""
    raw = url.strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if parts.scheme.lower() not in _SCHEMES or not parts.hostname:
        return raw
    scheme = parts.scheme.lower()
    host = _ascii_host(parts.hostname)
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    path = quote(unquote(parts.path), safe="/:@!$&'()*+,;=-._~") or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    query = urlencode(sorted(kept))
    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    """The lowercase ASCII host without ``www.`` ('' when ``url`` has none)."""
    try:
        host = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return ""
    host = _ascii_host(host)
    return host[4:] if host.startswith("www.") else host


def registrable_domain(url_or_host: str) -> str:
    """The registrable domain (``docs.stripe.com`` -> ``stripe.com``, ``x.gov.in`` stays ``x.gov.in``)."""
    host = (
        host_of(url_or_host)
        if "/" in url_or_host or ":" in url_or_host
        else host_of("//" + url_or_host)
    )
    if not host:
        return ""
    try:
        found = tld.get_fld(host, fix_protocol=True, fail_silently=True)
    except Exception:
        found = None
    return (found or host).lower()


def _numeric_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An IP literal in any spelling (dotted, decimal, octal, hex, IPv6, IPv4-mapped), else None."""
    bare = host.strip("[]")
    try:
        return ipaddress.ip_address(bare)
    except ValueError:
        pass
    if _NUMERIC_HOST.match(bare):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(bare))
        except OSError:
            return None
    return None


def ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for a globally routable address (IPv4-mapped IPv6 is judged by its IPv4 part)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_loopback or ip.is_link_local)


def public_url_problem(url: str) -> str | None:
    """Why ``url`` must not be fetched ('' never; ``None`` means it looks public), as a short code."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return "invalid_url"
    if parts.scheme.lower() not in _SCHEMES:
        return "scheme"
    if parts.username or parts.password:
        return "userinfo"
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return "no_host"
    if port is not None and port not in ALLOWED_PORTS:
        return "port"
    ip = _numeric_ip(host)
    if ip is not None:
        return None if ip_is_public(ip) else "private_address"
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES) or "." not in host:
        return "private_name"
    return None


def is_public_url(url: str) -> bool:
    return public_url_problem(url) is None
