"""Shared plumbing of the search adapters (Tavily, Serper): one HTTP call, one error vocabulary.

What lives here, so each adapter only holds what is specific to its provider:

* ``JsonSearchAdapter``: the key holder, client ownership (a client we did not create is never closed),
  one ``POST`` with a total timeout, latency from ``time.monotonic()``, and the guarantee that only
  ``SearchError`` escapes ``search``.
* error mapping (``error_from_response``, ``transport_error``): HTTP status and provider message to a
  ``SearchErrorKind``; ``Retry-After`` in both of its forms; the key is redacted from every message and
  no request body is ever echoed.
* date parsing (``parse_published``): ISO, RFC 2822, "Mar 12, 2025" and relative ("3 days ago") dates.
* request helpers: query cleaning, country and language normalisation, domain normalisation, recency.
* the two data files (``countries.yaml``, ``provider_prices.yaml``), loaded once.

No environment variable, setting or file outside the package data is read here.
"""

from __future__ import annotations

import asyncio
import calendar
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Any, Self

import httpx
import yaml

from mde_research_agent.ports.search import Capabilities, SearchError, SearchRequest, SearchResult

MAX_RETRY_AFTER_S = 86_400.0
MAX_RECENCY_MONTHS = 1_200  # 100 years: beyond this "any time" is the honest reading
_MESSAGE_LIMIT = 200

# --------------------------------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------------------------------


class Secret:
    """Holds an API key so that printing, repr-ing, logging or pickling an adapter cannot leak it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(****)"

    __str__ = __repr__

    def __reduce__(self) -> tuple[Any, ...]:
        raise TypeError("a Secret cannot be pickled or copied")


def redact(text: str, secret: Secret | None) -> str:
    """``text`` with every occurrence of the key replaced, then one line, then cut to a safe length."""
    if secret is not None:
        value = secret.reveal()
        if value:
            text = text.replace(value, "[redacted]")
    one_line = " ".join(text.split())
    return one_line[:_MESSAGE_LIMIT]


def check_key(secret: Secret) -> str:
    """The key as a header-safe string, or an ``auth`` error (never quoting the key)."""
    key = secret.reveal()
    if not key.strip():
        raise SearchError("auth", "API key is empty")
    if not key.isascii() or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in key):
        raise SearchError("auth", "API key contains characters a header cannot carry")
    return key


# --------------------------------------------------------------------------------------------------
# Data files
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CountryInfo:
    code: str  # lowercase ISO 3166-1 alpha-2
    name: str  # lowercase English name, as Tavily's `country` parameter expects
    tld: str  # country-code top-level domain, no dot
    language: str  # ISO 639-1 default language of the country's consumer web


def _read_data(filename: str) -> Any:
    text = resources.files("mde_research_agent").joinpath("data", filename).read_text("utf-8")
    return yaml.safe_load(text)


@lru_cache(maxsize=1)
def load_countries() -> Mapping[str, CountryInfo]:
    """``countries.yaml`` as ``{alpha-2: CountryInfo}``; read once. Raises ``ValueError`` if malformed."""
    raw = _read_data("countries.yaml")
    if not isinstance(raw, dict):
        raise ValueError("countries.yaml must be a mapping")
    out: dict[str, CountryInfo] = {}
    for code, entry in raw.items():
        if not (isinstance(code, str) and re.fullmatch(r"[a-z]{2}", code)):
            raise ValueError(f"countries.yaml: bad alpha-2 key {code!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"countries.yaml: {code} must be a mapping")
        fields = {k: entry.get(k) for k in ("name", "tld", "language")}
        if not all(isinstance(v, str) and v for v in fields.values()):
            raise ValueError(f"countries.yaml: {code} needs name, tld and language strings")
        name = str(fields["name"])
        if name != name.lower():
            raise ValueError(f"countries.yaml: {code} name must be lowercase")
        out[code] = CountryInfo(code, name, str(fields["tld"]), str(fields["language"]))
    return MappingProxyType(out)


def country_info(code: str | None) -> CountryInfo | None:
    """The entry for a lowercase alpha-2 code (case and spaces forgiven); None when unknown."""
    if not isinstance(code, str):
        return None
    return load_countries().get(code.strip().lower())


@lru_cache(maxsize=1)
def load_prices() -> Mapping[str, Mapping[str, Any]]:
    """``provider_prices.yaml`` as ``{provider: {...}}``; read once. Money fields are checked as integers."""
    raw = _read_data("provider_prices.yaml")
    if not isinstance(raw, dict):
        raise ValueError("provider_prices.yaml must be a mapping")
    out: dict[str, Mapping[str, Any]] = {}
    for provider, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        micros = entry.get("micros_per_credit")
        if isinstance(micros, bool) or not isinstance(micros, int) or micros < 0:
            raise ValueError(f"provider_prices.yaml: {provider}.micros_per_credit must be an int")
        out[str(provider)] = MappingProxyType(dict(entry))
    return MappingProxyType(out)


def price_int(entry: Mapping[str, Any], *path: str) -> int:
    """A non-negative integer from a price entry (``path`` may go through nested mappings)."""
    value: Any = entry
    for key in path:
        value = value[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"provider_prices.yaml: {'.'.join(path)} must be a non-negative int")
    return value


# --------------------------------------------------------------------------------------------------
# Request helpers
# --------------------------------------------------------------------------------------------------


def clean_query(text: object) -> str:
    """The query with control characters and runs of whitespace collapsed to single spaces."""
    if not isinstance(text, str):
        return ""
    return " ".join("".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in text).split())


def quote_phrase(query: str) -> str:
    """``query`` as one quoted phrase; double quotes inside it are dropped so the quoting stays valid."""
    inner = " ".join(query.replace('"', " ").split())
    if not inner:
        raise SearchError("bad_request", "the query is empty")
    return f'"{inner}"'


def normalize_country_code(value: str | None) -> str | None:
    """A lowercase alpha-2 code, or None when ``value`` does not have that shape."""
    if not isinstance(value, str):
        return None
    code = value.strip().lower()
    return code if re.fullmatch(r"[a-z]{2}", code) else None


_LANGUAGE = re.compile(r"([a-z]{2,3})(?:[-_]([a-z]{2}))?")


def normalize_language(value: str | None) -> tuple[str, str | None] | None:
    """``("hi", None)`` or ``("zh", "cn")`` from "hi", "zh-CN", "en_US"; None for anything else.

    Script subtags ("hi-Latn") are not ISO 639-1 and are refused on purpose: sending "hi" for Latin-script
    Hindi would pull Devanagari pages, so the caller reports a weak locale instead.
    """
    if not isinstance(value, str):
        return None
    m = _LANGUAGE.fullmatch(value.strip().lower())
    return (m.group(1), m.group(2)) if m else None


def clamp_results(value: object, *, low: int, high: int) -> int:
    """``value`` as an int inside ``[low, high]``; anything that is not an int becomes ``low``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return low
    return max(low, min(value, high))


def effective_recency_months(request: SearchRequest) -> int | None:
    """Positive recency in months (capped at ``MAX_RECENCY_MONTHS``); None when no recency is wanted."""
    months = request.recency_months
    if isinstance(months, bool) or not isinstance(months, int) or months <= 0:
        return None
    return min(months, MAX_RECENCY_MONTHS)


_DOMAIN = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?"
)


def normalize_domains(domains: Iterable[str], *, limit: int) -> tuple[str, ...]:
    """Bare, lowercase, de-duplicated host names from URLs or host names; invalid entries are dropped.

    ``https://www.Example.com/a?b`` becomes ``example.com`` (a leading ``www.`` is dropped so the whole
    site is meant). Only characters that are safe inside a ``site:`` operator survive.
    """
    out: list[str] = []
    for raw in domains:
        if not isinstance(raw, str):
            continue
        text = raw.strip().lower()
        text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)
        text = re.split(r"[/?#]", text, maxsplit=1)[0].rsplit("@", 1)[-1]
        text = text.split(":", 1)[0].strip(".")
        text = text.removeprefix("*.").removeprefix("www.")
        try:
            text = text.encode("idna").decode("ascii")
        except UnicodeError:
            continue
        if not _DOMAIN.fullmatch(text) or text in out:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


def subtract_months(day: date, months: int) -> date:
    """``day`` minus whole calendar months, the day of month clamped to the target month's length."""
    total = day.year * 12 + (day.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# --------------------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------------------

_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:$|[T\s])")
_RELATIVE = re.compile(
    r"(\d{1,4}|an?|one)\s+(second|sec|minute|min|hour|hr|day|week|month|year)s?\s+ago", re.I
)
_MONTH_DAY_YEAR = re.compile(r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I)
_DAY_MONTH_YEAR = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})", re.I)


def _month_number(token: str) -> int | None:
    word = token.lower()
    if word == "sept":
        return 9
    if len(word) < 3:
        return None
    for index, name in enumerate(_MONTHS, start=1):
        if name.startswith(word):
            return index
    return None


def default_today() -> date:
    return datetime.now(UTC).date()


def parse_published(value: object, today: date) -> date | None:
    """A provider date string as a ``date``; relative dates are measured from ``today``.

    Understands "2025-03-12" (with or without a time), "Mon, 12 Mar 2025 10:00:00 GMT", "Mar 12, 2025",
    "12 March 2025", "3 days ago", "an hour ago", "yesterday", "today". Anything else, and any date more
    than a day in the future, is None.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    try:
        parsed = _parse_published(text, today)
    except (ValueError, OverflowError):
        return None
    if parsed is None or parsed > today + timedelta(days=1):
        return None
    return parsed


def _parse_published(text: str, today: date) -> date | None:
    if m := _ISO_DATE.match(text):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    lowered = text.lower()
    if lowered in {"today", "just now", "now"}:
        return today
    if lowered == "yesterday":
        return today - timedelta(days=1)
    if m := _RELATIVE.fullmatch(text):
        return _relative(m.group(1).lower(), m.group(2).lower(), today)
    if m := _MONTH_DAY_YEAR.fullmatch(text):
        month = _month_number(m.group(1))
        return date(int(m.group(3)), month, int(m.group(2))) if month else None
    if m := _DAY_MONTH_YEAR.fullmatch(text):
        month = _month_number(m.group(2))
        return date(int(m.group(3)), month, int(m.group(1))) if month else None
    if any(ch.isdigit() for ch in text) and ":" in text:
        return parsedate_to_datetime(text).date()  # RFC 2822; raises ValueError when it is not one
    return None


def _relative(amount: str, unit: str, today: date) -> date:
    count = int(amount) if amount.isdigit() else 1
    if unit in {"second", "sec", "minute", "min", "hour", "hr"}:
        return today
    if unit == "day":
        return today - timedelta(days=count)
    if unit == "week":
        return today - timedelta(weeks=count)
    if unit == "month":
        return subtract_months(today, count)
    return subtract_months(today, 12 * count)


# --------------------------------------------------------------------------------------------------
# Response helpers
# --------------------------------------------------------------------------------------------------


def text_of(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


# --------------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------------

_RETRY_SECONDS = re.compile(r"\d{1,9}(?:\.\d+)?")
_QUOTA_HINTS = (
    "not enough credit",
    "insufficient credit",
    "out of credit",
    "credits exhausted",
    "usage limit",
    "plan limit",
    "pay-as-you-go limit",
)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` value: delay-seconds ("120", "1.5") or an HTTP-date.

    Never negative, never beyond ``MAX_RETRY_AFTER_S``; None when absent or unreadable.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if _RETRY_SECONDS.fullmatch(text):
        return _bounded_seconds(float(text))
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return _bounded_seconds((when - (now or datetime.now(UTC))).total_seconds())


def _bounded_seconds(seconds: float) -> float | None:
    if not math.isfinite(seconds):
        return None
    return max(0.0, min(seconds, MAX_RETRY_AFTER_S))


def provider_message(response: httpx.Response, secret: Secret | None) -> str:
    """A short, redacted explanation from an error body.

    Only the fields providers use for a human message are read (``message``, ``error``,
    ``detail.error``, ``detail[].msg``); a validation error's echoed ``input`` is never touched, so no
    request content comes back through here.
    """
    try:
        data = response.json()
    except ValueError:
        return ""
    parts: list[str] = []
    if isinstance(data, dict):
        for key in ("message", "error", "detail"):
            _collect_message(data.get(key), parts)
    else:
        _collect_message(data, parts)
    return redact("; ".join(parts), secret)


def _collect_message(value: object, parts: list[str]) -> None:
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, dict):
        for key in ("error", "message", "msg"):
            if isinstance(value.get(key), str):
                parts.append(value[key])
                return
    elif isinstance(value, list):
        for item in value[:3]:
            if isinstance(item, dict) and isinstance(item.get("msg"), str):
                parts.append(item["msg"])


def error_from_response(label: str, response: httpx.Response, secret: Secret | None) -> SearchError:
    """The ``SearchError`` for a non-2xx response from provider ``label``."""
    status = response.status_code
    detail = provider_message(response, secret)
    suffix = f": {detail}" if detail else ""
    text = f"{label} answered HTTP {status}{suffix}"
    lowered = detail.lower()
    hinted = any(hint in lowered for hint in _QUOTA_HINTS)
    if hinted and (status in {400, 402, 403} or (status == 429 and "credit" in lowered)):
        return SearchError("quota", text, status=status)
    if status in {401, 403}:
        return SearchError("auth", text, status=status)
    if status in {402, 432, 433}:
        return SearchError("quota", text, status=status)
    if status in {429, 434}:
        retry = parse_retry_after(response.headers.get("retry-after"))
        return SearchError("rate_limited", text, status=status, retry_after=retry)
    if status == 408:
        return SearchError("timeout", text, status=status)
    if status in {400, 413, 422}:
        return SearchError("bad_request", text, status=status)
    return SearchError("http_error", text, status=status)


def transport_error(label: str, exc: BaseException) -> SearchError:
    """The ``SearchError`` for a failure below HTTP (timeout, connection, protocol)."""
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return SearchError("timeout", f"{label} did not answer in time")
    if isinstance(exc, httpx.InvalidURL):
        return SearchError("bad_request", f"{label} base_url is not a valid URL")
    return SearchError("network", f"{label} request failed ({type(exc).__name__})")


# --------------------------------------------------------------------------------------------------
# The adapter base
# --------------------------------------------------------------------------------------------------


class JsonSearchAdapter:
    """Key holder, client ownership and one JSON ``POST``; subclasses supply ``_search``.

    Subclasses set ``name`` and ``capabilities`` and implement ``_auth_headers`` and ``_search``. The
    constructor reads nothing from the environment; the key lives in a ``Secret`` and reaches a header
    only while a request is being built.
    """

    name: str
    capabilities: Capabilities
    _label = "search provider"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None,
        timeout_s: float,
        cost_per_call_micros: int | None,
        today: Callable[[], date] | None,
        base_url: str,
    ) -> None:
        if isinstance(timeout_s, bool) or not timeout_s > 0:
            raise ValueError("timeout_s must be positive")
        if cost_per_call_micros is not None and (
            isinstance(cost_per_call_micros, bool)
            or not isinstance(cost_per_call_micros, int)
            or cost_per_call_micros < 0
        ):
            raise ValueError("cost_per_call_micros must be a non-negative integer (micro-USD)")
        self._secret = Secret(api_key)
        self._client = client
        self._owns_client = client is None
        self._timeout_s = float(timeout_s)
        self._cost_override = cost_per_call_micros
        self._today: Callable[[], date] = today or default_today
        self._base_url = base_url.rstrip("/")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self._base_url!r}, timeout_s={self._timeout_s!r})"

    __str__ = __repr__

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client this adapter created. A client passed in is the caller's to close."""
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            await client.aclose()

    async def search(self, request: SearchRequest) -> SearchResult:
        try:
            return await self._search(request)
        except SearchError:
            raise
        except Exception as exc:  # the port promises that only SearchError escapes
            raise SearchError(
                "bad_response", f"{self._label} adapter failed unexpectedly ({type(exc).__name__})"
            ) from exc

    # -- for subclasses ------------------------------------------------------------------------

    def _auth_headers(self, key: str) -> dict[str, str]:
        raise NotImplementedError

    async def _search(self, request: SearchRequest) -> SearchResult:
        raise NotImplementedError

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # trust_env off: nothing in the process environment (proxy variables included) changes
            # where the key is sent; pass your own client when a proxy is needed.
            self._client = httpx.AsyncClient(timeout=self._timeout_s, trust_env=False)
        return self._client

    async def _post_json(self, path: str, body: Mapping[str, Any]) -> tuple[Any, int]:
        """POST ``body`` as JSON; return ``(parsed JSON, latency in ms)`` or raise ``SearchError``."""
        key = check_key(self._secret)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(self._auth_headers(key))
        client = self._get_client()
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await client.post(
                    f"{self._base_url}{path}",
                    json=body,
                    headers=headers,
                    timeout=self._timeout_s,
                    follow_redirects=False,  # a redirect must never carry the key to another host
                )
        except (httpx.HTTPError, httpx.InvalidURL, TimeoutError) as exc:
            raise transport_error(self._label, exc) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        if not 200 <= response.status_code < 300:
            raise error_from_response(self._label, response, self._secret)
        try:
            return response.json(), latency_ms
        except ValueError as exc:
            raise SearchError(
                "bad_response", f"{self._label} sent a body that is not valid JSON"
            ) from exc
