"""The rich side of the Brief compile (ADR 0016): what one per-section compile call takes and returns.

``engine/ports.py`` (frozen for this work) keeps the basic ``BriefCompiler`` port (one whole-ledger request, items
that fill empty slots, placements with a topic and consumers). The v2 compile needs more on the wire: the facts of ONE
section group, the Brief items that already exist (so an item is not repeated and a fact can point at a real id),
per-fact ``kind`` and ``refs``, single list ELEMENTS to add to a filled list slot, and drafted typical details. Those
types live here, shared by the adapter (``calls/compile_brief``) and the engine (``interview_compile``), and the
adapter implements both ports. A compiler that only has ``compile_brief`` (a test double, the unavailable
placeholder) still works: ``from_basic`` turns its answer into a ``SectionResult``.

Nothing here decides anything: these are plain carriers plus the deterministic floor.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field

from mde_compass.catalog.fact_kinds import kind_for
from mde_compass.domain.brief import Brief
from mde_compass.domain.common import Strict
from mde_compass.domain.interview import Fact
from mde_compass.engine.ports import (
    BriefCompilation,
    CompiledItem,
    NotUsedFact,
    TurnContext,
)

ALL = "all"
NO_TARGETS = "_none"
"""A section name that matches no slot: the call has no target to fill and only places and types the facts."""
TYPICAL_SECTION = "typical"
"""A pseudo section: the call drafts typical details (assumptions) instead of filling Brief values."""
MAX_TYPICAL = 12

CompileTask = Literal["fill", "typical"]


class SectionRequest(Strict):
    """One compile call over ONE group of facts (``cmp.compile_brief`` with ``sections``)."""

    language: str = "en"
    task: CompileTask = "fill"
    group: str = ""  # the compile group id (logs and tests)
    sections: list[str] = Field(
        default_factory=list
    )  # Brief sections or slot groups the call may fill
    ledger: str = ""  # the facts of this call, ``context_pack.full_ledger`` lines
    project: str = ""  # the project card: what the project is, from the slots
    existing: str = ""  # Brief items that already exist: ``FTR-001 | scope | Order tracking`` lines
    slot_state: dict[str, str] = Field(
        default_factory=dict
    )  # slot id -> ``assumed`` | user-given state
    assumptions: list[str] = Field(
        default_factory=list
    )  # what is already assumed (typical task: do not repeat)
    needs_table: str = ""
    asked_list: str = ""
    qa_compact: str = ""


class DetailPlacement(Strict):
    """A fact that fits no Brief value: kept as a typed detail, with the records it belongs to."""

    fact_id: str
    topic: str = ""
    consumers: list[str] = Field(default_factory=list)
    kind: str = ""  # a ``catalog/fact_kinds.yaml`` key, else derived by code
    refs: list[str] = Field(default_factory=list)  # ids or exact names of apps, roles or features


class TypicalDetail(Strict):
    """A detail that apps of this kind usually need and the person did not mention (becomes an assumption)."""

    claim: str
    reason: str = ""
    kind: str = "other"
    risky: bool = False
    what_changes: str = ""


class SectionResult(Strict):
    items: list[CompiledItem] = Field(default_factory=list)  # whole values for targets
    additions: list[CompiledItem] = Field(
        default_factory=list
    )  # one list ELEMENT each, for a list target
    placements: list[DetailPlacement] = Field(default_factory=list)
    not_used: list[NotUsedFact] = Field(default_factory=list)
    typical: list[TypicalDetail] = Field(default_factory=list)
    summary: str = ""
    pitch: str = ""
    used_fallback: bool = False
    unplaced: list[str] = Field(
        default_factory=list
    )  # facts of the request the model mentioned nowhere


@runtime_checkable
class SectionCompiler(Protocol):
    async def compile_section(self, ctx: TurnContext, request: SectionRequest) -> SectionResult: ...


ReviewKind = Literal[
    "vague", "conflicting", "undefined", "unrealistic", "dependency", "plausibility"
]
"""What a review finding says: ``vague`` / ``undefined`` are unclear words (a non-blocking open question);
``conflicting`` / ``unrealistic`` / ``dependency`` / ``plausibility`` are things that do not add up (an assumption the
person decides, ADR 0016)."""


class ReviewFinding(Strict):
    """A finding of ``cmp.scan_ambiguity`` v2: it points at the Brief, it never changes a value."""

    slot_id: str | None = None
    text: str
    kind: ReviewKind
    fact_ids: list[str] = Field(default_factory=list)


@runtime_checkable
class ReviewScanner(Protocol):
    async def scan_review(self, ctx: TurnContext, brief: Brief) -> list[ReviewFinding]: ...


def floor_result(facts: Sequence[Fact], *, used_fallback: bool = True) -> SectionResult:
    """The deterministic floor: every fact placed for everyone, typed by the kinds file, no value invented."""
    return SectionResult(
        placements=[
            DetailPlacement(
                fact_id=f.id,
                topic=f.topics[0] if f.topics else f.type,
                consumers=[ALL],
                kind=kind_for(f.type, f.topics),
            )
            for f in facts
        ],
        used_fallback=used_fallback,
    )


def from_basic(compilation: BriefCompilation, facts: Sequence[Fact]) -> SectionResult:
    """A basic ``BriefCompilation`` as a ``SectionResult``. Facts it did not mention are ``unplaced`` (the basic
    port makes no promise to account for them)."""
    if compilation.used_fallback and not compilation.placements:
        return floor_result(facts)  # a basic compiler that fell back placed nothing: the floor does
    by_id = {f.id: f for f in facts}
    placements = [
        DetailPlacement(
            fact_id=p.fact_id,
            topic=p.topic,
            consumers=list(p.consumers),
            kind=kind_for(by_id[p.fact_id].type, by_id[p.fact_id].topics)
            if p.fact_id in by_id
            else "",
        )
        for p in compilation.placements
    ]
    mentioned = (
        {p.fact_id for p in compilation.placements}
        | {n.fact_id for n in compilation.not_used}
        | {i for item in compilation.items for i in item.fact_ids}
    )
    return SectionResult(
        items=list(compilation.items),
        placements=placements,
        not_used=list(compilation.not_used),
        summary=compilation.summary,
        pitch=compilation.pitch,
        used_fallback=compilation.used_fallback,
        unplaced=[f.id for f in facts if f.id not in mentioned and not compilation.used_fallback],
    )


__all__ = [
    "ALL",
    "MAX_TYPICAL",
    "NO_TARGETS",
    "TYPICAL_SECTION",
    "CompileTask",
    "DetailPlacement",
    "ReviewFinding",
    "ReviewKind",
    "ReviewScanner",
    "SectionCompiler",
    "SectionRequest",
    "SectionResult",
    "TypicalDetail",
    "floor_result",
    "from_basic",
]
