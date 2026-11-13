"""One batch of a lane call: run it, and when the output is TRUNCATED redo only the unfinished items in smaller batches
(a fused call splits back into its parts, doc 18 4.2). A single item that still truncates falls back, flagged."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from typing import Any, TypeVar

from mde_spec_agent.calls.base import RunResult, SpecCallSpec, Truncated
from mde_spec_agent.run.context import RunContext
from mde_spec_core.callkit.split import split_on_truncation

T = TypeVar("T")


async def run_split(
    ctx: RunContext,
    cs: SpecCallSpec[Any, Any],
    items: Sequence[T],
    make_input: Callable[[list[T]], Any],
    key: Callable[[T], Hashable],
    step: str,
    *,
    split: Callable[[list[T]], list[list[T]]] | None = None,
) -> list[tuple[list[T], RunResult[Any]]]:
    """Results in item order: one (items, result) pair per call that was finally made."""
    assert ctx.runner is not None, "a model step needs a call runner"
    batch = list(items)
    inp = make_input(batch)
    try:
        res = await ctx.runner.run(cs, inp, node_ids=[str(key(i)) for i in batch], step=step)
        return [(batch, res)]
    except Truncated as exc:
        parts = (
            split(batch)
            if split is not None
            else split_on_truncation(batch, (), 2, key=key)
            if len(batch) > 1
            else []
        )
        if not parts or (len(parts) == 1 and len(parts[0]) == len(batch)):
            res = RunResult(
                output=cs.fallback(inp),
                used_fallback=True,
                flagged=True,
                reason=f"truncated and cannot be split further: {exc}"[:300],
            )
            return [(batch, res)]
        out: list[tuple[list[T], RunResult[Any]]] = []
        for part in parts:
            out += await run_split(ctx, cs, part, make_input, key, step, split=split)
        return out


async def gather_batches(coros: list[Any]) -> list[Any]:
    """Run every batch to the end, then raise the first failure in batch order (no orphan task keeps spending)."""
    import asyncio

    results = await asyncio.gather(*coros, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return list(results)
