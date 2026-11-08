"""Errors raised by the Spec Loader."""

from __future__ import annotations


class SpecError(Exception):
    """Base class of every loader error."""


class SpecParseError(SpecError):
    """The text is not a well-formed ``APP_SPEC.md`` (bad fence, bad YAML, bad block)."""

    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message if line is None else f"line {line}: {message}")
        self.message = message
        self.line = line


class UnknownIdError(SpecError):
    """An id that is neither a block, nor an addressable item, nor a build step."""

    def __init__(self, spec_id: str) -> None:
        super().__init__(f"unknown spec id: {spec_id}")
        self.spec_id = spec_id
