"""The situation catalog (spec section 9) as data.

``parse_situation_table`` reads the markdown table straight out of
``docs/product/context-engine-spec.md`` so the scenario suite and the eval dataset can be checked
against the spec itself, row for row, instead of against a copy that could drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "docs" / "product" / "context-engine-spec.md"
_HEADING = re.compile(r"^##\s+9\.\s+Situation catalog\s*$", re.M)
_NEXT_HEADING = re.compile(r"^##\s+", re.M)


@dataclass(frozen=True)
class Situation:
    number: int  # 1-based row number (SIT-001 ...)
    situation: str  # first column
    detection: str
    behavior: str

    @property
    def sit_id(self) -> str:
        return f"SIT-{self.number:03d}"


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def parse_situation_table(path: Path = SPEC_PATH) -> list[Situation]:
    """The rows of the section 9 table, in order (header and separator lines skipped)."""
    text = path.read_text(encoding="utf-8")
    start = _HEADING.search(text)
    if start is None:
        raise ValueError("section 9 (Situation catalog) not found in the spec")
    rest = text[start.end() :]
    nxt = _NEXT_HEADING.search(rest)
    section = rest[: nxt.start()] if nxt else rest
    rows: list[Situation] = []
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) != 3 or cells[0] == "Situation" or set(cells[0]) <= {"-", " "}:
            continue
        rows.append(Situation(len(rows) + 1, *cells))
    return rows
