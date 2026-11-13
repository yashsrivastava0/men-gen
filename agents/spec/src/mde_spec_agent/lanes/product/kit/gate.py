"""Patches with removals, id minting that cannot collide, and gate 1 after every call (doc 18 3.1).

* ``apply_ext`` is ``apply_patch`` plus the `remove` extension a merge needs (the framework patch can only add or replace).
* ``Minter`` mints Spec ids from a range of its own per batch, so batches that run side by side never mint the same id and
  a resumed run (which replays stored patches) cannot collide with a batch that still has to run.
* ``Gate1`` applies a patch to a COPY of the graph, runs the gate-1 validators and returns what the patch made worse:
  those messages are the call's validator errors (the runner turns them into the one targeted re-ask).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from mde_spec_agent.derive.seed import research_adapter
from mde_spec_agent.lanes.product.kit.research import as_view
from mde_spec_agent.run.patch import Patch, apply_patch
from mde_spec_core.errors_out import plain_words
from mde_spec_core.graph import NODE_TYPES, SpecGraph
from mde_spec_core.ids import IdRegistry, format_id, parse_id
from mde_spec_core.validators.base import Finding, run_all
from mde_spec_core.validators.context import make_context

BATCH_SPAN = 1000  # ids one batch may mint per kind


def apply_removals(graph: SpecGraph, patch: Patch) -> None:
    """The `remove` extension of a patch (a merge removes the folded screen): ids that are gone."""
    for table, ids in sorted((patch.get("remove") or {}).items()):
        if table not in NODE_TYPES:
            raise ValueError(f"unknown table {table}")
        store: dict[str, Any] = getattr(graph, table)
        for nid in sorted(ids):
            store.pop(nid, None)


def apply_ext(graph: SpecGraph, patch: Patch) -> list[str]:
    """Tables and the rest as ``apply_patch`` does, then the removals of the `remove` extension."""
    touched = apply_patch(graph, patch)
    apply_removals(graph, patch)
    return touched


# ------------------------------------------------------------------------------------------------ ids
def all_ids(graph: SpecGraph) -> set[str]:
    ids: set[str] = set(graph.hero)
    for kind in NODE_TYPES:
        ids |= set(getattr(graph, kind))
    for n in graph.all_nodes():
        ids |= set(n.aliases)
    return ids


def registry_from_graph(graph: SpecGraph) -> IdRegistry:
    """Every id the graph knows (live ids and aliases) is used; the registry mints above them."""
    return IdRegistry(used=all_ids(graph))


def highest_minted(ids: Collection[str], kind: str) -> int:
    best = 0
    for i in ids:
        p = parse_id(i)
        if p and p.kind == kind and p.spec_minted:
            best = max(best, p.number)
    return best


@dataclass
class Minter:
    """Mints ids of one batch in the Spec namespace from the batch's own range of numbers."""

    registry: IdRegistry
    base: dict[str, int] = field(default_factory=dict)
    minted: list[str] = field(default_factory=list)

    @classmethod
    def for_batch(cls, graph: SpecGraph, ordinal: int) -> Minter:
        ids = all_ids(graph)
        reg = IdRegistry(used=set(ids))
        base: dict[str, int] = {}
        for kind in ("OP", "RUL", "US", "FLW", "ENT", "PER", "SCR", "INT"):
            start = highest_minted(ids, kind) + ordinal * BATCH_SPAN
            reg._next[kind] = start
            base[kind] = start
        return cls(reg, base)

    def mint(self, kind: str) -> str:
        if (
            kind not in self.base
        ):  # a kind the batch did not plan for: its own range above everything used
            start = highest_minted(self.registry.used, kind)
            self.registry._next[kind] = start
            self.base[kind] = start
        nid = self.registry.mint(kind)
        p = parse_id(nid)
        if p is None or p.number > self.base[kind] + BATCH_SPAN:
            raise ValueError(f"batch ran out of {kind} ids ({nid})")
        self.minted.append(nid)
        return nid

    def fork(self) -> Minter:
        reg = IdRegistry(
            used=set(self.registry.used),
            aliases=dict(self.registry.aliases),
            tombstones=set(self.registry.tombstones),
            _next=dict(self.registry._next),
        )
        return Minter(reg, dict(self.base), list(self.minted))


def spec_id(kind: str, number: int) -> str:
    return format_id(kind, number, spec_minted=True)


# ------------------------------------------------------------------------------------------------ gate 1
_NEW_ID = re.compile(r"^[A-Z]+-S\d+$")


def _key(f: Finding) -> tuple[str, str, str]:
    return (f.code, f.where, f.why)


class Gate1:
    """Gate 1 on a copy of the graph snapshot a batch was built from."""

    def __init__(
        self,
        graph: SpecGraph,
        *,
        research: Any = None,
        quotes: Mapping[str, str] | None = None,
        brand_names: Collection[str] = (),
    ) -> None:
        self.graph = graph
        self.kw: dict[str, Any] = {
            "research": research_adapter(view) if (view := as_view(research)) is not None else None,
            "quotes": dict(quotes or {}),
            "brand_names": set(brand_names),
        }
        self._before: set[tuple[str, str, str]] | None = None

    def _run(self, g: SpecGraph) -> list[Finding]:
        return run_all(make_context(g, **self.kw), gate=1)

    def before(self) -> set[tuple[str, str, str]]:
        if self._before is None:
            self._before = {_key(f) for f in self._run(self.graph.model_copy(deep=True))}
        return self._before

    def findings(self, patch: Patch) -> list[Finding]:
        after = self.graph.model_copy(deep=True)
        apply_ext(after, patch)
        before = self.before()
        return [f for f in self._run(after) if f.severity == "blocker" and _key(f) not in before]

    def errors(self, patch: Patch) -> list[str]:
        return [plain_words(f) for f in self.findings(patch)][:20]
