"""Shared helpers of the nine validators."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from mde_contracts.calls import Severity, ValidatorFinding

MAX_TARGETS = 12  # a finding lists at most this many ids (the message says how many in total)


def finding(
    validator: str,
    message: str,
    *target_ids: str,
    severity: Severity = "error",
) -> ValidatorFinding:
    return ValidatorFinding(
        validator=validator,
        severity=severity,
        target_ids=list(dict.fromkeys(target_ids))[:MAX_TARGETS],
        message=message,
    )


def duplicates(ids: Iterable[str]) -> list[str]:
    """Ids that occur more than once, in order of first occurrence."""
    counts = Counter(ids)
    return [i for i, n in counts.items() if n > 1]


def id_list(ids: Iterable[str], limit: int = 6) -> str:
    """``SCR-001, SCR-002 and 3 more`` for messages."""
    items = list(ids)
    shown = ", ".join(items[:limit])
    return shown if len(items) <= limit else f"{shown} and {len(items) - limit} more"
