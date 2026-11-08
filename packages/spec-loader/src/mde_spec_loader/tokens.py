"""Deterministic token estimator.

The rule is "one token per four characters". It is measured on UTF-8 *bytes*, so for ASCII text it
is exactly ``chars / 4`` and non-Latin scripts (Hindi, for example) are not under-counted by a
factor of two or three. No tokenizer library: the estimate must be identical everywhere.
"""

from __future__ import annotations

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimated tokens of ``text`` (ceiling of UTF-8 bytes / 4; 0 for the empty string)."""
    size = len(text.encode("utf-8"))
    return (size + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
