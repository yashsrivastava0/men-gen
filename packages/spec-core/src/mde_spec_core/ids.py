"""IDs, namespaces, aliases, tombstones (doc 15 3.6, V-32).

Compass-origin ids (`SCR-029`) are carried; Spec-minted ids live in their own namespace
(`SCR-S012`) so a later Compass id can never collide. Ids are minted once, never reused;
a rename or merge keeps the old id as an alias.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Kinds whose ids Compass issues (carried) and Spec may also mint.
KINDS = (
    "APP",
    "ROL",
    "MOD",
    "SCR",
    "FLW",
    "ENT",
    "FTR",
    "OP",
    "RUL",
    "US",
    "PER",
    "INT",
    "BLK",
    "N",
    "CP",
    "T",
    "CF",
    "ACC",
    "JRN",
    "ASSET",
    "HERO",
)

_ID = re.compile(r"^(?P<kind>[A-Z]+)-(?P<spec>S)?(?P<num>\d{1,6})$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ParsedId:
    kind: str
    number: int
    spec_minted: bool


def parse_id(value: str) -> ParsedId | None:
    m = _ID.match(value)
    if not m:
        return None
    return ParsedId(m["kind"], int(m["num"]), m["spec"] == "S")


def is_valid_key(value: str) -> bool:
    """Region and action keys (they become testIDs): lower snake case, never ends in a widget word."""
    return bool(_KEY.match(value))


def format_id(kind: str, number: int, *, spec_minted: bool) -> str:
    return f"{kind}-{'S' if spec_minted else ''}{number:03d}"


@dataclass
class IdRegistry:
    """Mints Spec ids per kind, remembers every id ever used, aliases and tombstones."""

    used: set[str] = field(default_factory=set)
    aliases: dict[str, str] = field(default_factory=dict)  # old id -> current id
    tombstones: set[str] = field(default_factory=set)
    _next: dict[str, int] = field(default_factory=dict)

    def carry(self, value: str) -> str:
        """Register an id that came from Compass. Reuse of a used id is an error (never fixed by code)."""
        if value in self.used:
            raise ValueError(f"duplicate id {value}")
        self.used.add(value)
        return value

    def mint(self, kind: str) -> str:
        n = self._next.get(kind, 0) + 1
        while format_id(kind, n, spec_minted=True) in self.used:
            n += 1
        self._next[kind] = n
        value = format_id(kind, n, spec_minted=True)
        self.used.add(value)
        return value

    def alias(self, old: str, new: str) -> None:
        if old == new:
            return
        self.aliases[old] = new
        self.used.add(old)

    def tombstone(self, value: str) -> None:
        self.tombstones.add(value)
        self.used.add(value)

    def resolve(self, value: str) -> str | None:
        """Follow aliases to the current id; None when the id is dead (tombstoned) or unknown."""
        seen: set[str] = set()
        cur = value
        while cur in self.aliases:
            if cur in seen:
                return None
            seen.add(cur)
            cur = self.aliases[cur]
        if cur in self.tombstones or cur not in self.used:
            return None
        return cur


def namespaced_copy_key(screen_id: str, key: str) -> str:
    """Copy keys are namespaced by screen id by code (G-5): `scr_s012.pay_action`."""
    return f"{screen_id.lower().replace('-', '_')}.{key}"
