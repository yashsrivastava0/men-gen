"""Plain data shared by the parsers, the chunker and the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from mde_compass.domain.claims import SourceKind
from mde_core.redact import strip_hidden_chars


@dataclass(frozen=True)
class Section:
    """One readable (or unreadable) piece of a document, with where it came from.

    ``table`` sections hold one row per line; ``header`` is the header row repeated when a table is split.
    ``row_numbers`` (aligned with the lines of ``text``; ``header_row`` is the header's own row) and ``sheet`` / ``last_col`` let the chunker build
    ``sheet Roles!A1:D9`` pointers.
    """

    pointer: str
    heading: str = ""
    text: str = ""
    unreadable: bool = False
    table: bool = False
    header: str = ""
    sheet: str = ""
    last_col: str = ""
    row_numbers: tuple[int, ...] = ()
    header_row: int = 0


@dataclass
class ParsedDocument:
    kind: SourceKind
    pages: int = 0
    sections: list[Section] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def clean_text(text: str) -> str:
    """Strip zero-width and control characters and unify line endings."""
    return strip_hidden_chars(text.replace("\r\n", "\n").replace("\r", "\n"))
