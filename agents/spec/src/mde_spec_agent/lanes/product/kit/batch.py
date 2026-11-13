"""Batches: one checkpointed unit of model work that returns a patch (doc 15 8.4, doc 18 4.2).

A batch is a list of work items (an entity, a story, a flow, an integration need). It runs one call; on `Truncated` the
unfinished items are redone in smaller batches (`split_on_truncation`), and a single item that still truncates takes its
deterministic fallback and is flagged. Patches are built by pure functions, applied by the step in sorted batch order
after all batches finished, so a resumed run ends with the same bytes as an uninterrupted one.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from mde_spec_agent.calls.base import CallInput, RunResult, SpecCallSpec, Truncated
from mde_spec_agent.run.checkpoint import Versions
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch
from mde_spec_core.callkit.fusion import items_per_call
from mde_spec_core.callkit.ladder import FixKind
from mde_spec_core.callkit.split import split_on_truncation

from .gate import Gate1, Minter

T = TypeVar("T")
BuildPatch = Callable[[Any, Any, Minter, bool], Patch]  # (input, output, minter, flagged) -> patch


def versions_of(ctx: RunContext, cs: SpecCallSpec[Any, Any], prompt: str, schema: str) -> Versions:
    """The checkpoint key's versions for one call (call spec id and version, prompt, schema; library and route stay)."""
    return dataclasses.replace(
        ctx.versions, call_spec=f"{cs.id}@{cs.spec.version}", prompt=prompt, schema=schema
    )


def with_gate(
    cs: SpecCallSpec[Any, Any], gate: Gate1, build: BuildPatch, minter: Minter
) -> SpecCallSpec[Any, Any]:
    """The call spec with gate 1 added as its LAST validator (it runs on the patch the output would make)."""

    def gate_validator(inp: Any, out: Any) -> list[str]:
        try:
            patch = build(inp, out, minter.fork(), False)
        except (
            Exception
        ) as exc:  # a patch that cannot even be built is a validation failure, never a crash
            return [f"the answer cannot be applied: {type(exc).__name__}: {exc}"[:300]]
        return gate.errors(patch)

    return dataclasses.replace(cs, validators=(*cs.validators, gate_validator))


def items_in_batch(ctx: RunContext, avg_item_tokens: float, kind_cap: int) -> int:
    return items_per_call(ctx.writer, avg_item_tokens, kind_cap, ctx.config)


def chunked(items: Sequence[T], size: int) -> list[list[T]]:
    size = max(1, size)
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


@dataclass
class Attempt(Generic[T]):
    """What a batch needs to run: the call, the items, how to build input and patch, and which nodes a re-ask is about."""

    ctx: RunContext
    cs: SpecCallSpec[Any, Any]
    step: str
    gate: Gate1
    make_input: Callable[[Sequence[T]], CallInput]
    build: BuildPatch
    node_ids: Callable[[Sequence[T]], list[str]]
    minter: Minter
    strict: Callable[[RunResult[Any]], None] | None = (
        None  # raises when a flagged result must not be used (the plan)
    )
    on_flagged: Callable[[], Patch] | None = (
        None  # a patch to use instead of the fallback's (the fused call)
    )

    async def run(self, items: Sequence[T]) -> Patch:
        inp = self.make_input(items)
        cs = with_gate(self.cs, self.gate, self.build, self.minter)
        runner = self.ctx.runner
        assert runner is not None
        try:
            res: RunResult[Any] = await runner.run(
                cs, inp, node_ids=self.node_ids(items), step=self.step
            )
        except Truncated:
            if len(items) <= 1:
                # one item that still does not fit: its deterministic fallback, flagged like any failed call
                out = self.cs.fallback(inp)
                res = RunResult(output=out, used_fallback=True, flagged=True, reason="truncated")
            else:
                parts = split_on_truncation(list(items), (), 2)
                patches = [await self.run(p) for p in parts]
                return merge_patch_list(patches)
        if res.flagged and self.strict is not None:
            self.strict(res)
        if res.flagged and self.on_flagged is not None:
            return self.on_flagged()
        patch = self.build(inp, res.output, self.minter, res.flagged)
        if res.reasked:
            patch["reasked"] = True
        if res.flagged:
            patch.setdefault("flags", [])
            for nid in self.node_ids(items):
                patch["flags"].append(
                    {"node": nid, "call": self.cs.id, "reason": (res.reason or "fallback")[:300]}
                )
            patch["degraded_call"] = True
        return patch


def merge_patch_list(patches: Sequence[Patch]) -> Patch:
    """Sub-batches of one truncated batch: their tables are disjoint, extras are appended in order."""
    out: Patch = {"tables": {}, "replace": True}
    for p in patches:
        for t, nodes in p.get("tables", {}).items():
            out["tables"].setdefault(t, {}).update(nodes)
        for k in ("locale_copy", "glossary", "hero", "tokens", "meta", "remove"):
            if p.get(k):
                cur = out.setdefault(k, {})
                if k == "remove":
                    for t, ids in p[k].items():
                        cur[t] = sorted(set(cur.get(t, [])) | set(ids))
                elif k == "locale_copy":
                    for loc, kv in p[k].items():
                        cur.setdefault(loc, {}).update(kv)
                else:
                    cur.update(p[k])
        for k in ("flags", "rcode"):
            if p.get(k):
                out.setdefault(k, []).extend(p[k])
        if p.get("degraded_call"):
            out["degraded_call"] = True
        if p.get("reasked"):
            out["reasked"] = True
        for k, v in p.items():
            if k not in out and k not in ("tables", "replace"):
                out[k] = v
    return out


async def run_batch(
    ctx: RunContext,
    step: str,
    batch_id: str,
    versions: Versions,
    attempt: Attempt[Any],
    items: Sequence[Any],
) -> Patch:
    """One checkpointed batch (`ctx.batch`): produce it once, replay it from the store on a resume."""

    async def produce() -> Patch:
        return await attempt.run(items)

    return await ctx.batch(step, batch_id, versions, produce)


async def gather_batches(coros: Sequence[Awaitable[Patch]]) -> list[Patch]:
    """Run all batches side by side; the caller applies the patches in sorted batch order afterwards."""
    import asyncio

    return list(await asyncio.gather(*coros))


def apply_patches(ctx: RunContext, ordered: Sequence[tuple[str, Patch]]) -> list[str]:
    """Apply patches in sorted batch order: tables, removals, flags, R-code rows. Returns the touched ids."""
    from .gate import apply_removals

    touched: list[str] = []
    for _batch, patch in sorted(ordered, key=lambda x: x[0]):
        touched += ctx.apply(patch)
        apply_removals(ctx.graph, patch)
        ctx.flags.extend(patch.get("flags", []))
        for row in patch.get("rcode", []):
            ctx.rcode.record(
                FixKind(row["kind"]), row["node_id"], row["what"], row["before"], row["after"]
            )
    return touched
