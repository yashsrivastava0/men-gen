"""Text and value hygiene for proposals: every string a model proposes is cleaned, length-capped and
secret-scanned before it can reach the spec, so the rendered file always reads back as written.

``Reject`` carries a stable ``code`` (what kind of problem) and a plain ``reason`` (logged with the
dropped proposal).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from mde_contracts.calls import KeyValue
from mde_core.redact import strip_hidden_chars
from mde_spec_builder.validators.leaks import has_secret

RejectCode = Literal[
    "invalid",  # a value is missing, malformed or too long
    "unknown_target",  # an id the proposal refers to does not exist
    "duplicate",  # the item already exists
    "limit",  # a cap would be exceeded (screens, fields, ...)
    "secret",  # the text looks like a secret
    "research",  # bad or excluded research reference
    "protected",  # the item is part of the baseline's critical path
    "breaks_validator",  # applying it would make a validator fail
    "unsupported",  # the proposal asks for something we never do (real payments, ...)
]

SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
ROUTE = re.compile(r"^/[A-Za-z0-9_\-\[\]/]*$")
_SPACE = re.compile(r"\s+")
_WORDS = re.compile(r"[A-Za-z0-9]+")


class Reject(Exception):
    """A proposal (or part of it) is not acceptable. Never reaches the caller of the pipeline."""

    def __init__(self, code: RejectCode, reason: str) -> None:
        super().__init__(reason)
        self.code: RejectCode = code
        self.reason = reason


def clean(text: str | None, limit: int, what: str, *, required: bool = True) -> str:
    """Whitespace collapsed, hidden characters dropped, length-capped and secret-scanned."""
    if text is None:
        if required:
            raise Reject("invalid", f"{what} is missing")
        return ""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise Reject("invalid", f"{what} has characters that cannot be written") from None
    cleaned = _SPACE.sub(" ", strip_hidden_chars(text)).strip()
    if not cleaned:
        if required:
            raise Reject("invalid", f"{what} is empty")
        return ""
    if len(cleaned) > limit:
        raise Reject("invalid", f"{what} is longer than {limit} characters")
    if has_secret(cleaned):
        raise Reject("secret", f"{what} looks like a secret")
    return cleaned


def snake(text: str | None, limit: int, what: str) -> str:
    value = clean(text, limit, what)
    if not SNAKE.match(value):
        raise Reject("invalid", f"{what} must be lower-case words joined by underscores")
    return value


def route(text: str | None) -> str:
    value = clean(text, 80, "route")
    if not ROUTE.match(value) or "//" in value or (len(value) > 1 and value.endswith("/")):
        raise Reject("invalid", "route must look like /name or /name/[id]")
    return value


def pairs(items: Sequence[KeyValue] | None, what: str, *, limit: int = 12) -> dict[str, str]:
    """Key/value pairs as a mapping; a repeated key is an error."""
    if not items:
        raise Reject("invalid", f"{what} needs at least one key/value pair")
    if len(items) > limit:
        raise Reject("limit", f"{what} has more than {limit} pairs")
    out: dict[str, str] = {}
    for item in items:
        key = clean(item.key, 60, f"{what} key")
        if key in out:
            raise Reject("invalid", f"{what} repeats the key {key}")
        out[key] = item.value
    return out


def strings(items: Sequence[str] | None, limit: int, what: str, *, max_items: int) -> list[str]:
    """A non-empty list of cleaned, de-duplicated strings."""
    if not items:
        raise Reject("invalid", f"{what} is missing")
    if len(items) > max_items:
        raise Reject("limit", f"{what} has more than {max_items} entries")
    return list(dict.fromkeys(clean(item, limit, what) for item in items))


def words(text: str) -> list[str]:
    return _WORDS.findall(text)


def pascal(text: str) -> str:
    parts = words(text)
    return "".join(w[:1].upper() + w[1:] for w in parts)


def camel(text: str) -> str:
    joined = pascal(text)
    return joined[:1].lower() + joined[1:]


def snake_case(text: str) -> str:
    return "_".join(w.lower() for w in words(text))
