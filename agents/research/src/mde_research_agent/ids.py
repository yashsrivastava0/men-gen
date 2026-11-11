"""Prefixed ULIDs for runs, events and calls, without touching the shared ``mde_core`` prefix list.

``mde_core.ids.new_id`` only accepts its own closed set of prefixes, so Research builds ``<prefix>_<ULID>``
itself from the shared ULID primitive. ``Ids`` is injectable: production uses real ULIDs, tests a counter so
runs are reproducible.
"""

from __future__ import annotations

import itertools
from typing import Protocol

from mde_core.ids import new_ulid

PREFIXES = frozenset({"rrn", "evt", "call", "pg", "qry", "atm"})


class Ids(Protocol):
    def new(self, prefix: str) -> str: ...


class UlidIds:
    def new(self, prefix: str) -> str:
        if prefix not in PREFIXES:
            raise ValueError(f"unknown id prefix: {prefix!r}")
        return f"{prefix}_{new_ulid()}"


class CounterIds:
    """Deterministic ids for tests and replay: ``rrn_00000000000000000000000001``."""

    def __init__(self) -> None:
        self._n = itertools.count(1)

    def new(self, prefix: str) -> str:
        if prefix not in PREFIXES:
            raise ValueError(f"unknown id prefix: {prefix!r}")
        return f"{prefix}_{next(self._n):026d}"
