"""B3/B4: the section writers (C9a, C9b, C9c) and the screen-brief batches (C10).

Each writer gets only the context it needs (``context``), runs through the ``LlmRunner`` (so a
gateway failure is the fallback "no proposals"), and returns its ``SpecProposals`` in a fixed order.
Applying them is the engine's job (``proposals.validate_and_apply``), always in this order:
C9a, C9b, C9c, then C10 batch by batch. C10 is sequential by default; ``concurrency`` runs a few
batches at once but never changes the order in which results are applied.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from mde_contracts.calls import (
    BriefContext,
    C9aInput,
    C9aOutput,
    C9bInput,
    C9bOutput,
    C9cInput,
    C9cOutput,
    C10Input,
    C10Output,
    SpecProposals,
)
from mde_contracts.gateway import CallSpec
from mde_contracts.research import BrandFinding, ResearchSuggestion
from mde_contracts.spec import AppSpec, ScreenBlock
from mde_spec_builder import context as ctx
from mde_spec_builder.llm import LlmRunner, run_all

MAX_PROPOSALS_PER_CALL = 40
MAX_PROPOSALS_PER_BATCH = 60
MAX_NEW_SCREENS = 8

_A_KINDS = {"feature", "pattern"}
_B_KINDS = {"feature", "integration", "compliance"}
_C_KINDS = {"brand", "pattern"}


@dataclass(frozen=True)
class SectionSpecs:
    c9a: CallSpec[C9aInput, C9aOutput] | None
    c9b: CallSpec[C9bInput, C9bOutput] | None
    c9c: CallSpec[C9cInput, C9cOutput] | None
    c10: CallSpec[C10Input, C10Output] | None


def _of(items: Sequence[ResearchSuggestion], kinds: set[str]) -> list[ResearchSuggestion]:
    return [s for s in items if s.kind in kinds]


async def run_section_writers(
    runner: LlmRunner,
    specs: SectionSpecs,
    spec: AppSpec,
    brief: BriefContext,
    research: Sequence[ResearchSuggestion],
    brand: BrandFinding | None,
    *,
    which: Sequence[str],
    max_screens: int,
    concurrency: int,
) -> list[tuple[str, SpecProposals | None]]:
    """C9a/C9b/C9c (those named in ``which``) on the baseline, results in a/b/c order."""
    jobs: list[tuple[str, Callable[[], Awaitable[SpecProposals | None]]]] = []
    if "C9a" in which and specs.c9a is not None:
        a_spec = specs.c9a
        a_input = ctx.c9a_input(
            brief,
            spec,
            _of(research, _A_KINDS),
            room=min(max_screens - len(spec.screens), MAX_NEW_SCREENS),
        )
        jobs.append(("C9a", lambda: runner.call(a_spec, a_input, step="screens")))
    if "C9b" in which and specs.c9b is not None:
        b_spec = specs.c9b
        b_input = ctx.c9b_input(brief, spec, _of(research, _B_KINDS))
        jobs.append(("C9b", lambda: runner.call(b_spec, b_input, step="data")))
    if "C9c" in which and specs.c9c is not None:
        c_spec = specs.c9c
        c_input = ctx.c9c_input(
            brief,
            spec,
            list(brand.colors) if brand is not None else [],
            list(brand.tone_words) if brand is not None else [],
            _of(research, _C_KINDS),
        )
        jobs.append(("C9c", lambda: runner.call(c_spec, c_input, step="design")))
    results = await run_all([job for _, job in jobs], concurrency)
    return [(name, out) for (name, _), out in zip(jobs, results, strict=True)]


async def run_screen_briefs(
    runner: LlmRunner,
    call_spec: CallSpec[C10Input, C10Output] | None,
    spec: AppSpec,
    brief: BriefContext,
    research: Sequence[ResearchSuggestion],
    *,
    only: set[str] | None,
    batch_size: int,
    retries: int,
    concurrency: int,
    limit: int | None,
    on_batch: Callable[[int, int], Awaitable[None]],
) -> list[SpecProposals | None]:
    """C10 over batches of 3 to 4 screens (only the screens in ``only`` when given). Returns one
    result per batch in batch order, ``None`` where the fallback applied."""
    if call_spec is None:
        return []
    batches: list[list[ScreenBlock]] = []
    for batch in ctx.screen_batches(spec, batch_size):
        picked = [s for s in batch if only is None or s.id in only]
        if picked:
            batches.append(picked)
    if limit is not None:
        batches = batches[:limit]
    done = 0
    total = len(batches)

    def job(batch: list[ScreenBlock]) -> Callable[[], Awaitable[SpecProposals | None]]:
        async def run() -> SpecProposals | None:
            nonlocal done
            out = await runner.call(
                call_spec,
                ctx.c10_input(brief, spec, batch, research[:3]),
                step="briefs",
                retries=retries,
            )
            done += 1
            await on_batch(done, total)
            return out

        return run

    return await run_all([job(b) for b in batches], concurrency)
