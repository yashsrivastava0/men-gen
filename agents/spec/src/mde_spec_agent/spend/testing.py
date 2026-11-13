"""Importable helpers for spend tests (a spawned process must be able to import its worker)."""

from __future__ import annotations

from .record import SpendRecord, SpendRefused


def spend_worker(path: str, n: int) -> None:
    r = SpendRecord(path, cap_micros=1000, tripwire_micros=900)
    for _ in range(n):
        try:
            res = r.reserve(10, label="p")
            r.commit(res, 10)
        except SpendRefused:
            return
