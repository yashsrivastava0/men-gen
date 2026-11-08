"""The file hash rule (normative).

``header.hash`` = sha256 (UTF-8, LF newlines) of the full text of ``APP_SPEC.md`` with the value of
the front-matter ``hash:`` line blanked to the empty string (``hash: ""``). The rendered file
embeds that hash as ``hash: "<64 hex>"``. ``verify`` recomputes it by blanking again.

Nothing else in the file depends on the hash, and the file holds no timestamps, so the same spec
always renders to the same bytes and the same hash.
"""

from __future__ import annotations

import re

from mde_core.hashing import sha256_hex
from mde_spec_loader.errors import SpecParseError

_HASH_LINE = re.compile(r'^hash: "([0-9a-f]{0,64})"[ \t]*$', re.MULTILINE)
BLANK_HASH_LINE = 'hash: ""'


def normalize_newlines(text: str) -> str:
    """Drop a leading BOM and turn CRLF into LF (files checked out with CRLF still verify)."""
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n")


def front_matter_end(text: str) -> int | None:
    """Offset just after the closing ``---`` line of the front matter, or None if there is none."""
    if not text.startswith("---\n"):
        return None
    pos = 4
    while pos <= len(text):
        nl = text.find("\n", pos)
        line = text[pos:] if nl == -1 else text[pos:nl]
        if line == "---":
            return pos + 3
        if nl == -1:
            return None
        pos = nl + 1
    return None


def _hash_match(text: str) -> re.Match[str] | None:
    end = front_matter_end(text)
    if end is None:
        return None
    return _HASH_LINE.search(text, 0, end)


def embedded_hash(text: str) -> str | None:
    """The hash value written in the front matter (``""`` when blanked), or None if absent."""
    match = _hash_match(normalize_newlines(text))
    return None if match is None else match.group(1)


def blank_hash(text: str) -> str:
    """``text`` with the front-matter hash value replaced by the empty string."""
    text = normalize_newlines(text)
    match = _hash_match(text)
    if match is None:
        return text
    return text[: match.start()] + BLANK_HASH_LINE + text[match.end() :]


def compute_spec_hash(text: str) -> str:
    """sha256 of the file text with the hash blanked (the value ``header.hash`` must carry)."""
    return sha256_hex(blank_hash(text))


def embed_hash(text: str) -> str:
    """Return ``text`` with its front-matter hash set to ``compute_spec_hash(text)``."""
    text = normalize_newlines(text)
    match = _hash_match(text)
    if match is None:
        raise SpecParseError("no front-matter hash line to fill in")
    digest = compute_spec_hash(text)
    return text[: match.start()] + f'hash: "{digest}"' + text[match.end() :]


def verify_hash(text: str, expected: str | None = None) -> bool:
    """True if the embedded hash matches the recomputed one (and ``expected``, when given)."""
    embedded = embedded_hash(text)
    if not embedded:
        return False
    if embedded != compute_spec_hash(text):
        return False
    return expected is None or expected == embedded
