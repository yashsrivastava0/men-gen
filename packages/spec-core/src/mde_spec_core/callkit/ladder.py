"""The R-code step of the repair ladder: log and banner only (doc 18 3.2, 3.3; FN-05, FN-06).

The six mechanical fixes that cannot change meaning are named here as ``FixKind``; their
implementations come later (lead). This module holds what every implementation shares: the
``RCodeLog`` that records every change (what, node, before, after) so it is counted on the G2 page
and never silent, and the ``weak_route_banner`` rule (more than the configured share of NODES needed
a re-ask for one step: a banner, never a stop).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from typing import Any

from mde_spec_core.canon import canonical_json
from mde_spec_core.config import SpecConfig


class FixKind(StrEnum):
    SORT_BYTES = "sort_bytes"  # keys, line endings, order
    MINT_MISSING_ID = "mint_missing_id"  # a duplicate id is never fixed by code
    INSTANTIATE_LIBRARY_DEFAULT = "instantiate_library_default"  # missing standard state etc.
    EXACT_SYNONYM = "exact_synonym"  # unknown enum through an exact-synonym table
    DROP_UNKNOWN_OPTIONAL = "drop_unknown_optional"
    DOWNGRADE_LOCKED_TO_DEFAULT = "downgrade_locked_to_default"  # Locked claim without evidence


@dataclass(frozen=True)
class RCodeChange:
    kind: FixKind
    node_id: str
    what: str
    before: str  # canonical text of the old value ("" when absent)
    after: str  # canonical text of the new value ("" when removed)

    def as_row(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "node_id": self.node_id,
            "what": self.what,
            "before": self.before,
            "after": self.after,
        }


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else canonical_json(value)


@dataclass
class RCodeLog:
    changes: list[RCodeChange] = field(default_factory=list)

    def record(
        self, kind: FixKind, node_id: str, what: str, before: Any, after: Any
    ) -> RCodeChange:
        change = RCodeChange(FixKind(kind), node_id, what, _text(before), _text(after))
        self.changes.append(change)
        return change

    def extend(self, other: RCodeLog) -> None:
        self.changes.extend(other.changes)

    def __len__(self) -> int:
        return len(self.changes)

    def counts(self) -> dict[str, int]:
        """Changes per fix kind (every kind present, zeros included) for the G2 page."""
        c = Counter(ch.kind for ch in self.changes)
        return {k.value: c.get(k, 0) for k in FixKind}

    @property
    def nodes_touched(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(ch.node_id for ch in self.changes))

    def rows(self) -> list[dict[str, str]]:
        return [ch.as_row() for ch in self.changes]


@dataclass(frozen=True)
class WeakRouteBanner:
    step: str
    reasked_nodes: int
    total_nodes: int
    share: float
    code: str = "weak_route"

    @property
    def message(self) -> str:
        return (
            f"this route needed many repairs for step {self.step} "
            f"({self.reasked_nodes} of {self.total_nodes} nodes); "
            "consider a stronger route for it"
        )


def weak_route_banner(
    reasked_nodes: int | Collection[str], total_nodes: int, step: str, config: SpecConfig
) -> WeakRouteBanner | None:
    """A banner if MORE than the configured share (15%) of nodes needed a re-ask for this step.

    ``reasked_nodes`` is a count or a collection of node ids (distinct ids are counted). The
    comparison is exact (fractions, no float rounding): 15 of 100 is no banner, 16 of 100 is.
    """
    n = reasked_nodes if isinstance(reasked_nodes, int) else len(set(reasked_nodes))
    if total_nodes <= 0 or n <= 0:
        return None
    threshold = Fraction(str(config.checks.weak_route_banner_share))
    if Fraction(n, total_nodes) > threshold:
        return WeakRouteBanner(step, n, total_nodes, n / total_nodes)
    return None
