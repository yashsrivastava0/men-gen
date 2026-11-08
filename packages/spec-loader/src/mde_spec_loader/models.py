"""Typed results of the loader calls (all frozen, JSON-serialisable)."""

from __future__ import annotations

from typing import Literal

from mde_contracts.common import Frozen


class BlockEntry(Frozen):
    id: str
    kind: str
    section: int
    line_start: int
    line_end: int
    chars: int
    tokens: int


class SectionEntry(Frozen):
    number: int
    key: str  # entry of SECTION_ORDER
    title: str
    ids: list[str]
    tokens: int
    line_start: int


class IndexView(Frozen):
    """``index()``: sections and ids with their sizes."""

    spec_version: int
    app_id: str
    hash: str
    sections: list[SectionEntry]
    blocks: list[BlockEntry]
    total_tokens: int


class SliceTier(Frozen):
    tier: int
    block_ids: list[str]
    token_estimate: int


class Slice(Frozen):
    """The ready-made context bundle for one task (spec 7.2)."""

    id: str  # the step, screen or flow id that was asked for
    spec_tag: str  # "@spec SCR-014@v7": the header generated files carry
    text: str  # the whole bundle, ready to paste into a prompt
    block_ids: list[str]  # ids included, in order
    token_estimate: int
    tiers: list[SliceTier]
    locked: bool = False  # a hand-edited screen: skip on regeneration


class SpecDiff(Frozen):
    """Ids that differ between two versions (blocks and the items inside them)."""

    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    stale_screens: list[str] = []  # screens to regenerate: changed/added + users of changed items

    @property
    def all_ids(self) -> list[str]:
        return sorted({*self.added, *self.removed, *self.changed})

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


class SliceReport(Frozen):
    id: str
    kind: Literal["step", "screen"]
    tokens: int
    over_budget: bool


class ConsumerReport(Frozen):
    """Result of walking a spec the way the frontend engine will."""

    budget_tokens: int
    slices: list[SliceReport]
    unresolved: list[str]  # "SCR-003 -go-> SCR-099 (open_cart)"
    unscheduled: list[str]  # screens that no build step builds
    hash_ok: bool
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def max_tokens(self) -> int:
        return max((s.tokens for s in self.slices), default=0)
