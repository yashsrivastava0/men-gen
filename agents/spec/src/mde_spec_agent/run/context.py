"""RunContext: everything a step needs. Steps are plain async functions `step(ctx)`; lanes live in `mde_spec_agent.lanes`.

`ctx.batch` is the checkpointed unit of work: a batch is run once, its patch is stored under a key that carries the call
spec, prompt, schema and library versions and the route fingerprint, and a resumed run replays the stored patch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mde_spec_agent.calls.base import CallRunner
from mde_spec_agent.spend import SpendGuard
from mde_spec_core.callkit.ladder import RCodeLog
from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import SpecGraph
from mde_spec_core.profiles import ConsumerProfile

from .checkpoint import Versions, key_for
from .concurrency import Limiter
from .patch import Patch, apply_patch
from .store import RunStore


@dataclass
class RunContext:
    run_id: str
    program_id: str
    input_hash: str
    store: RunStore
    graph: SpecGraph
    config: SpecConfig
    writer: ConsumerProfile
    consumer: ConsumerProfile
    versions: Versions
    guard: SpendGuard
    limiter: Limiter
    runner: CallRunner | None = None  # None for code-only runs (the zero-cost preview)
    spec_input: Any = None
    research: Any = None
    facts: set[str] = field(default_factory=set)
    rcode: RCodeLog = field(default_factory=RCodeLog)
    offline: bool = False
    banners: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] = field(
        default_factory=dict
    )  # release plan, inventory, proposals (set by the plan step)
    flags: list[dict[str, Any]] = field(default_factory=list)  # degraded nodes, listed at G2
    derived: Any = None  # derive.pipeline.Derived: facts, profile decision, inventory, release plan, seed result
    pack: Any = None  # the assembled Pack (set by the assemble step)
    check: Any = None  # the gate-3 CheckReport
    verify: Any = None  # the injected VerifyPort for deep packs, or None

    # ---- events --------------------------------------------------------------------------------------------
    def emit(self, type_: str, *, step: str | None = None, **data: Any) -> None:
        self.store.append_event(self.run_id, type_, step=step, data=data)

    # ---- checkpointed batches --------------------------------------------------------------------------------
    async def batch(
        self,
        step: str,
        batch_id: str,
        versions: Versions,
        produce: Callable[[], Awaitable[Patch]],
    ) -> Patch:
        """Run `produce` once per (program, input hash, step, batch, versions, route). A stored result is replayed."""
        key = key_for(self.program_id, self.input_hash, step, batch_id, versions)
        hit = self.store.get_checkpoint(self.run_id, key)
        if hit is not None:
            return hit["patch"]
        async with self.limiter:
            self.guard.start_wave()  # tripwire and stop request: no new wave once reached
            patch = await produce()
        self.store.put_checkpoint(
            self.run_id, key, {"step": step, "batch": batch_id, "patch": patch}
        )
        self.emit("checkpoint", step=step, batch=batch_id)
        return patch

    def apply(self, patch: Patch) -> list[str]:
        return apply_patch(self.graph, patch)

    def save_graph(self) -> None:
        self.store.write_text(self.run_id, "graph.json", self.graph.canonical_text() + "\n")
