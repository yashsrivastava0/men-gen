"""Prefixed ULIDs (``ses_...``, ``evt_...``, ``call_...``): sortable, URL-safe, no dependency."""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32

PREFIXES = frozenset(
    {
        "tnt",  # tenant
        "ses",  # session
        "evt",  # event
        "call",  # llm call
        "req",  # request
        "crd",  # card instance
        "brf",  # brief version
        "rsn",  # research note
        "spv",  # spec version
        "apr",  # approval
        "job",  # queue job
        "run",  # plan run
        "upl",  # upload
        "app",  # app id
    }
)

_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _encode(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(now_ms: int | None = None) -> str:
    """Return a 26-char ULID. Monotonic within one process even inside one millisecond."""
    global _last_ms, _last_rand
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    with _lock:
        if ms <= _last_ms:
            ms = _last_ms
            _last_rand += 1
            if _last_rand >= 1 << 80:  # pragma: no cover - astronomically unlikely
                _last_ms += 1
                ms = _last_ms
                _last_rand = int.from_bytes(os.urandom(10), "big")
        else:
            _last_rand = int.from_bytes(os.urandom(10), "big")
        _last_ms = ms
        rand = _last_rand
    return _encode(ms, 10) + _encode(rand, 16)


def new_id(prefix: str) -> str:
    """Return ``<prefix>_<ulid>``. The prefix must be one of :data:`PREFIXES`."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix!r}")
    return f"{prefix}_{new_ulid()}"


def id_prefix(value: str) -> str:
    """Return the prefix of a prefixed id (``ses`` for ``ses_01...``)."""
    head, sep, tail = value.partition("_")
    if not sep or head not in PREFIXES or len(tail) != 26:
        raise ValueError(f"not a prefixed id: {value!r}")
    return head


def is_id(value: str, prefix: str | None = None) -> bool:
    try:
        got = id_prefix(value)
    except ValueError:
        return False
    return prefix is None or got == prefix


class SequentialIds:
    """Deterministic id source for tests: ``ses_TEST0000000000000000000001`` ..."""

    def __init__(self) -> None:
        self._n = 0

    def __call__(self, prefix: str) -> str:
        if prefix not in PREFIXES:
            raise ValueError(f"unknown id prefix: {prefix!r}")
        self._n += 1
        return f"{prefix}_TEST{self._n:022d}"
