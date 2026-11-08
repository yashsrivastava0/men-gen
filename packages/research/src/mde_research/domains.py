"""URL and domain helpers: registrable domains, allow and block rules, safe source urls.

No network access and no public-suffix download: a short built-in list of multi-part public
suffixes is enough for the independence check (two sources count as independent only when their
registrable domains differ).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

# Public suffixes with more than one label that appear in practice (not exhaustive by design).
MULTI_PART_SUFFIXES = frozenset(
    {
        "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "ltd.uk", "plc.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "asn.au",
        "co.in", "net.in", "org.in", "gov.in", "ac.in", "nic.in", "res.in", "firm.in", "gen.in",
        "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp",
        "com.br", "net.br", "org.br", "gov.br", "edu.br",
        "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn",
        "com.sg", "edu.sg", "gov.sg", "org.sg",
        "com.hk", "org.hk", "gov.hk", "edu.hk",
        "co.nz", "org.nz", "govt.nz", "ac.nz",
        "co.za", "org.za", "gov.za", "ac.za",
        "com.mx", "org.mx", "gob.mx",
        "com.tr", "org.tr", "gov.tr", "edu.tr",
        "com.sa", "gov.sa", "edu.sa",
        "co.il", "org.il", "gov.il", "ac.il",
        "com.pk", "gov.pk", "edu.pk",
        "com.bd", "gov.bd", "edu.bd",
        "com.ng", "gov.ng", "edu.ng",
        "co.kr", "or.kr", "go.kr", "ac.kr",
        "com.my", "gov.my", "edu.my",
        "com.ar", "gov.ar", "edu.ar",
        "com.ua", "gov.ua", "edu.ua",
    }
)  # fmt: skip

MAX_URL_CHARS = 500
_HOST_LABEL = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")


def host_of(url: str) -> str | None:
    """Lower-cased host without port or trailing dot; ``None`` when the url has no host."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.casefold().rstrip(".") or None


def registrable_domain(host: str) -> str:
    """The registrable domain (eTLD+1) of ``host`` using the built-in suffix list."""
    host = host.casefold().rstrip(".")
    if _is_ip(host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in MULTI_PART_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def normalize_rule(rule: str) -> str:
    """A domain rule as a bare host: no scheme, path, ``www.`` or ``*.`` prefix."""
    value = rule.strip().casefold()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0].split(":", 1)[0]
    value = value.removeprefix("*.").removeprefix(".").removeprefix("www.")
    return value.rstrip(".")


def domain_matches(host: str, rule: str) -> bool:
    """True when ``host`` is the rule's domain or one of its subdomains."""
    base = normalize_rule(rule)
    host = host.casefold().removeprefix("www.")
    return bool(base) and (host == base or host.endswith(f".{base}"))


def domain_allowed(host: str, allowed: Iterable[str], blocked: Iterable[str]) -> bool:
    """Blocked domains always lose; a non-empty allow list means only those domains are used."""
    if any(domain_matches(host, rule) for rule in blocked):
        return False
    allow = [rule for rule in allowed if normalize_rule(rule)]
    return not allow or any(domain_matches(host, rule) for rule in allow)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _public_host(host: str) -> bool:
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
        return False
    if _is_ip(host):
        ip = ipaddress.ip_address(host.strip("[]"))
        return ip.is_global
    labels = host.split(".")
    return len(labels) >= 2 and all(_HOST_LABEL.match(label) for label in labels)


def safe_http_url(url: str) -> str | None:
    """A cleaned http(s) url suitable to store as a source, or ``None``.

    Rejects other schemes, credentials in the url, private or loopback hosts, over-long urls and
    control characters. The fragment is dropped.
    """
    candidate = url.strip()
    if (
        not candidate
        or len(candidate) > MAX_URL_CHARS
        or any(not c.isprintable() for c in candidate)
    ):
        return None
    if any(c in candidate for c in " <>\"'`\\"):
        return None
    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return None
    host = parts.hostname.casefold().rstrip(".") if parts.hostname else ""
    if not host or not _public_host(host):
        return None
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def url_key(url: str) -> str:
    """Comparison key: scheme-insensitive host and path, no fragment, no trailing slash."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().casefold()
    host = (parts.hostname or "").casefold().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"
