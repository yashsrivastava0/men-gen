"""Engine request inputs that are derived from stored state (pure functions).

* ``references``: the apps the user named ("like X") from the Discover state, for Research job R2.
* ``brand_url``: a site the user **gave or confirmed**, for Research job R3. Never guessed: a link
  found only in a default, in something research found, or in an uploaded document does not count
  (an upload is untrusted text; its links are data, not the user's choice of site).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from typing import Final
from urllib.parse import urlsplit

from mde_contracts.slots import SlotRecord

MAX_REFERENCES: Final = 10
MAX_REFERENCE_CHARS: Final = 120
MAX_URL_CHARS: Final = 2048

# Where a brand link may live: the brand's assets, or the name card when the user pasted a site.
BRAND_SLOTS: Final = ("brand.assets", "brand.name")
_USER_STATES: Final = frozenset({"volunteered", "confirmed"})
_USER_SOURCES: Final = frozenset({"tap", "text"})  # typed or tapped by the user, this session
_STANDING_SOURCES: Final = frozenset({"org_profile", "memory"})  # the user's own, if confirmed
_URL: Final = re.compile(r"(?:https?://|www\.)[^\s<>\"'`]+", re.IGNORECASE)
_TRAILING: Final = ".,;:!?)]}>"
_PRIVATE_SUFFIXES: Final = (".local", ".localhost", ".internal", ".lan", ".home", ".corp")


def references(names: Sequence[str]) -> list[str]:
    """The named apps, trimmed, de-duplicated (case-insensitively), at most ``MAX_REFERENCES``."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = " ".join(raw.split())[:MAX_REFERENCE_CHARS].strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
        if len(out) == MAX_REFERENCES:
            break
    return out


def clean_url(raw: str) -> str | None:
    """A plain public ``http(s)`` site, or None. No credentials, no query or fragment, no IP
    literal, no single-label or private host name, no odd port."""
    text = raw.strip().rstrip(_TRAILING)
    if text.lower().startswith("www."):
        text = "https://" + text
    if not text or len(text) > MAX_URL_CHARS:
        return None
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return None
    if "." not in host or host.endswith(_PRIVATE_SUFFIXES) or port not in (None, 80, 443):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None  # an address, not a brand site
    if not all(label and len(label) <= 63 for label in host.split(".")):
        return None
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{host}{path}"


def _texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]  # pyright: ignore[reportUnknownVariableType]
    return []


def _given_by_the_user(rec: SlotRecord) -> bool:
    if rec.state not in _USER_STATES:
        return False
    if rec.source in _USER_SOURCES:
        return True
    return rec.source in _STANDING_SOURCES and rec.state == "confirmed"


def brand_url(slots: Mapping[str, SlotRecord]) -> str | None:
    """The first site the user gave or confirmed in ``brand.assets`` or ``brand.name``."""
    for slot_id in BRAND_SLOTS:
        rec = slots.get(slot_id)
        if rec is None or not _given_by_the_user(rec):
            continue
        for text in _texts(rec.value):
            for match in _URL.finditer(text):
                url = clean_url(match.group(0))
                if url is not None:
                    return url
    return None
