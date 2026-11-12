"""FetchPort: read one public web page (or PDF text layer) and return its main text.

The fetcher is plain code, not a model: public http(s) URLs only (checked at every redirect hop,
including what a name resolves to), robots.txt respected, no logins or paywall bypass, size and time
limits, a User-Agent that names the product. HTML goes through main-text extraction (trafilatura), a
PDF through its text layer (no OCR). JavaScript-only pages give no text and are reported as
``no_content``. It never raises: a page that cannot be read comes back with ``text == ""`` and a
``reason`` code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol, runtime_checkable

SkipReason = Literal[
    "no_content",  # fetched fine but no readable text (JavaScript-only page, scanned PDF, empty)
    "paywall",  # 401/402/403 with a login or subscription wall
    "blocked",  # 403/429 or a bot wall
    "robots",  # robots.txt disallows the path
    "private",  # not a public address (URL text or DNS resolution)
    "timeout",
    "too_large",
    "unsupported",  # not html, text or pdf
    "not_found",  # 404/410
    "error",  # any other failure
]


@dataclass(frozen=True)
class FetchedPage:
    url: str  # the URL asked for
    final_url: str  # after redirects (still public)
    text: str = ""  # main text; "" when it could not be read
    status: int | None = None
    content_type: str = ""
    title: str = ""
    published: date | None = None  # from page metadata when found
    text_source: Literal["provider", "fetch"] = (
        "fetch"  # "provider": a search provider gave the text
    )
    content_hash: str = ""  # sha256 of ``text`` (NFC) once non-empty
    reason: SkipReason | None = None  # why ``text`` is empty
    bytes_read: int = 0
    retry_after: float | None = (
        None  # seconds a 429 asked us to wait (its Retry-After), when it said
    )
    attempts: int = 1  # how many times the page was asked for (1 = no retry was needed or allowed)

    @property
    def ok(self) -> bool:
        return bool(self.text) and self.reason is None


@runtime_checkable
class FetchPort(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...
