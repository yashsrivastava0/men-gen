"""What the person chose at the structure check (G1.5), read from a run record.

One place, so every re-derivation of a run (the run itself, a restart, the views) uses the same choices and so
the same release plan: ``everything_in_one_release`` and the moves of features between the first release and later.
"""

from __future__ import annotations

from typing import Any

Move = tuple[str, str]

MOVE_TARGETS = ("in", "later")


def moves_in_force(rec: dict[str, Any]) -> tuple[Move, ...]:
    """The moves that apply to this run, in the order they were asked. After approval the gate record holds them all;
    before it (the person asked to change the split and the gate is open again) they are the recorded change requests."""
    g15 = (rec.get("gates") or {}).get("g15") or {}
    raw = g15.get("moves") if isinstance(g15, dict) else None
    if raw is None:
        raw = [m for ch in rec.get("g15_changes", []) for m in ch.get("moves", [])]
    out: list[Move] = []
    for m in raw:
        if isinstance(m, dict) and "item" in m and "to" in m:
            out.append((str(m["item"]), str(m["to"])))
    return tuple(out)


def everything_in_one_release(rec: dict[str, Any]) -> bool:
    g15 = (rec.get("gates") or {}).get("g15") or {}
    return bool(g15.get("everything_in_one_release")) if isinstance(g15, dict) else False
