"""The R-ask step: ONE re-ask per NODE in the whole run, batched per call kind (doc 18 3.2; FN-03,
FN-04).

``plan_reasks`` is pure: it reads the ledger and returns batches plus the items that have no re-ask
left (R-flag them). Recording the spend is a separate, explicit step (``ReaskLedger.record``) so a
planned wave that is never sent costs nothing, and a Gate 3 re-ask of a node that already used its
re-ask in Gate 1 is refused by the same ledger.

All failed items of one kind in one wave go in as few calls as the item limit allows, so the number
of re-asks does not grow with fusion. Items of one node stay together when the limit allows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mde_spec_core.canon import canonical_json
from mde_spec_core.config import SpecConfig

REASK_HEADER = "Some items failed a check. Return only these items, corrected."
REASK_FOOTER = "Return only these items. Do not change anything else."


@dataclass(frozen=True)
class FailedItem:
    node_id: str  # the node the item belongs to (the unit of the one-re-ask rule)
    call_kind: str  # items of one kind are batched together
    item: Any  # the failed item as the model wrote it
    validator_message: str
    schema_fragment: Any = ""  # str or JSON-able; the part of the schema for this item
    allowed_values: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ReaskBatch:
    call_kind: str
    items: tuple[FailedItem, ...]

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(i.node_id for i in self.items))


@dataclass(frozen=True)
class ReaskPlan:
    batches: tuple[ReaskBatch, ...]
    flagged: tuple[
        FailedItem, ...
    ]  # no re-ask left: R-flag (library default, assumed, listed at G2)

    @property
    def call_count(self) -> int:
        return len(self.batches)

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(n for b in self.batches for n in b.node_ids))


@dataclass
class ReaskLedger:
    """Which nodes already spent their re-ask. Survives in the checkpoint (``to_dict``)."""

    used: dict[str, int] = field(default_factory=dict)

    def count(self, node_id: str) -> int:
        return self.used.get(node_id, 0)

    def has_used(self, node_id: str, config: SpecConfig | None = None) -> bool:
        limit = config.repair.max_reasks_per_node if config else 1
        return self.count(node_id) >= limit

    def mark(self, node_id: str) -> None:
        self.used[node_id] = self.count(node_id) + 1

    def record(self, plan: ReaskPlan) -> None:
        """Commit a plan that was actually sent: each of its nodes spent one re-ask."""
        for node_id in plan.node_ids:
            self.mark(node_id)

    @property
    def reasked_nodes(self) -> frozenset[str]:
        return frozenset(n for n, c in self.used.items() if c > 0)

    def to_dict(self) -> dict[str, int]:
        return dict(sorted(self.used.items()))

    @classmethod
    def from_dict(cls, data: Mapping[str, int]) -> ReaskLedger:
        return cls({str(k): int(v) for k, v in data.items()})


def plan_reasks(
    failed_items: Iterable[FailedItem],
    node_state: ReaskLedger,
    config: SpecConfig,
    *,
    item_limits: Mapping[str, int] | None = None,
    default_item_limit: int | None = None,
) -> ReaskPlan:
    """Batch the failed items of one wave per call kind; items whose node has no re-ask left are
    flagged instead. ``item_limits`` maps a call kind to its items-per-call limit; without a limit
    a kind goes in one call.
    """
    max_per_node = config.repair.max_reasks_per_node
    eligible: dict[str, dict[str, list[FailedItem]]] = {}  # kind -> node -> items (insertion order)
    flagged: list[FailedItem] = []
    for it in failed_items:
        if node_state.count(it.node_id) >= max_per_node:
            flagged.append(it)
        else:
            eligible.setdefault(it.call_kind, {}).setdefault(it.node_id, []).append(it)

    batches: list[ReaskBatch] = []
    for kind in sorted(eligible):
        limit = (item_limits or {}).get(kind, default_item_limit)
        limit = max(1, limit) if limit else None
        current: list[FailedItem] = []
        for node_items in eligible[kind].values():
            group = list(node_items)
            if limit and current and len(current) + len(group) > limit:
                batches.append(ReaskBatch(kind, tuple(current)))
                current = []
            while limit and len(group) > limit:  # one node bigger than a call: split it
                batches.append(ReaskBatch(kind, tuple(group[:limit])))
                group = group[limit:]
            current.extend(group)
        if current:
            batches.append(ReaskBatch(kind, tuple(current)))
    return ReaskPlan(tuple(batches), tuple(flagged))


def build_reask_payload(
    item: Any,
    validator_message: str,
    schema_fragment: Any,
    allowed_values: Sequence[str] | None,
) -> str:
    """The re-ask text: the item, the validator's message WORD FOR WORD, the schema fragment, the
    allowed values, and "return only these items". The message is inserted unchanged.
    """
    lines = [
        REASK_HEADER,
        "",
        "ITEM:",
        _text(item),
        "",
        "VALIDATOR MESSAGE:",
        validator_message,
        "",
        "SCHEMA FRAGMENT:",
        _text(schema_fragment),
    ]
    if allowed_values is not None:
        lines += ["", "ALLOWED VALUES:", *[f"- {v}" for v in allowed_values]]
    lines += ["", REASK_FOOTER]
    return "\n".join(lines)


def build_batch_payload(batch: ReaskBatch) -> str:
    """One payload for a whole batch (several items of one call kind), item blocks in order."""
    blocks = [
        build_reask_payload(i.item, i.validator_message, i.schema_fragment, i.allowed_values)
        for i in batch.items
    ]
    return "\n\n---\n\n".join(blocks)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else canonical_json(value)
