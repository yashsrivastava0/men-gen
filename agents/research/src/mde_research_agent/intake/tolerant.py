"""Tolerant readers for loosely typed JSON: a wrong type is an empty value, never an exception.

An export comes from another team's product and changes between versions, so every reader here answers the
same way for a missing key, a ``null`` and a value of the wrong shape: empty. Nothing here knows a Compass
field name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mde_research_agent.domain.input import Tier


def tier_of(raw: Any) -> Tier:
    text = str(raw or "").strip().lower()
    return "locked" if text == "locked" else "open" if text == "open" else "default"


def is_slot(value: Any) -> bool:
    """A Brief slot: ``{"value": ..., "tier"|"provenance": ...}`` (the value plus how sure we are of it)."""
    return (
        isinstance(value, Mapping)
        and "value" in value
        and ("tier" in value or "provenance" in value)
    )


def slot(value: Any) -> tuple[Any, Tier, float | None]:
    """``(value, tier, confidence)`` of a slot-wrapped value; a plain value is ``default`` tier."""
    if is_slot(value):
        conf = value.get("confidence")
        return (
            value.get("value"),
            tier_of(value.get("tier")),
            float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None,
        )
    return value, "default", None


def slot_value(value: Any) -> Any:
    return slot(value)[0]


def m(value: Any) -> Mapping[str, Any]:
    """``value`` when it is a mapping, else an empty one."""
    return value if isinstance(value, Mapping) else {}


def s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def strs(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(s(v) for v in value if s(v))
    return ()


def rows(value: Any, *required: str) -> list[Mapping[str, Any]]:
    """The mappings of a list that carry every ``required`` key (a non-list, or a list of other things, is empty)."""
    if not isinstance(value, (list, tuple)):
        return []
    return [r for r in value if isinstance(r, Mapping) and all(r.get(k) for k in required)]


def to_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default
