"""Stable item ids: assigned once per session, never reused (ADR 0013).

Deletions leave a tombstone, renames add an alias. The ledger travels inside the Brief so the guarantee
holds across versions and after undo.
"""

from __future__ import annotations

import re

from pydantic import Field

from mde_compass.domain.common import Strict

ID_PREFIXES: tuple[str, ...] = ("APP", "ROL", "FTR", "ASM", "SRC", "RR", "Q", "CHG")
ITEM_ID_RE = re.compile(r"^(?P<prefix>[A-Z]{1,3})-(?P<n>\d{3,})$")


def format_id(prefix: str, n: int) -> str:
    return f"{prefix}-{n:03d}"


def parse_id(value: str) -> tuple[str, int] | None:
    m = ITEM_ID_RE.match(value)
    return (m.group("prefix"), int(m.group("n"))) if m else None


class IdLedger(Strict):
    """Counters (only ever increase), tombstones (deleted ids) and aliases (old id -> current id)."""

    counters: dict[str, int] = Field(default_factory=dict)
    tombstones: list[str] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)

    def allocate(self, prefix: str) -> str:
        if prefix not in ID_PREFIXES:
            raise ValueError(f"unknown id prefix {prefix!r}")
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return format_id(prefix, n)

    def note_existing(self, item_id: str) -> None:
        """Make sure the counter is past an id that arrived from elsewhere (import, fixture)."""
        parsed = parse_id(item_id)
        if parsed and parsed[1] > self.counters.get(parsed[0], 0):
            self.counters[parsed[0]] = parsed[1]

    def tombstone(self, item_id: str) -> None:
        if item_id not in self.tombstones:
            self.tombstones.append(item_id)

    def alias(self, old_id: str, new_id: str) -> None:
        self.aliases[old_id] = new_id

    def resolve(self, item_id: str) -> str:
        seen: set[str] = set()
        while item_id in self.aliases and item_id not in seen:
            seen.add(item_id)
            item_id = self.aliases[item_id]
        return item_id

    def is_deleted(self, item_id: str) -> bool:
        return item_id in self.tombstones
