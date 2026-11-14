"""The run: a pipeline of stages (doc 15 8.1, doc 18 1.2), checkpointed, resumable, single writer of the graph.

Stages run in order; the steps INSIDE a stage run in parallel (Product and Design after the plan; Screens, hero records,
behaviours and locales after the Freeze). After each stage the graph is saved, so a run killed at stage k resumes from
there and ends with the same bytes as an uninterrupted one (batches replay their checkpointed patches). A gate pauses the
run (G1.5 before Screens for deep apps or when a capacity deferral is proposed; G2 at the end). Spend refusals, stop
requests, rate limits and credit stops end the run `partial` and resumable; a programming error fails loudly.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from mde_core.errors import BudgetExceeded, CircuitOpen
from mde_spec_agent.calls.base import NoCredit
from mde_spec_agent.spend import SpendRefused, StopRequested
from mde_spec_core.graph import SpecGraph

from .context import RunContext
from .store import NotFound, RunStore

StepFn = Callable[[RunContext], Awaitable[None]]


class MissingNeed(RuntimeError):
    """A builder need is missing: the run ends partial with a plain list and the pack never reaches G2."""

    def __init__(self, needs: list[str]) -> None:
        super().__init__("missing builder needs: " + ", ".join(needs))
        self.needs = needs


class PlanInvalid(RuntimeError):
    """The Plan call is invalid after its re-ask: the one step with no safe default (FN-35). Run ends partial, resumable."""


@dataclass(frozen=True)
class StepDef:
    name: str
    fn: StepFn


@dataclass(frozen=True)
class Stage:
    steps: tuple[StepDef, ...]
    gate_before: str | None = None  # "g15": the run pauses here until the gate is approved
    gate_when: Callable[[RunContext], bool] | None = None  # None = always when gate_before is set

    @property
    def name(self) -> str:
        return "+".join(s.name for s in self.steps)


@dataclass
class Outcome:
    status: str
    reason: str = ""
    stage: str = ""
    errors: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(self, store: RunStore, pipeline: list[Stage]) -> None:
        self.store = store
        self.pipeline = pipeline

    # ---- gates ---------------------------------------------------------------------------------------------
    def approve_gate(
        self, run_id: str, gate: str, payload: dict[str, object] | None = None
    ) -> None:
        rec = self.store.get_run(run_id)
        gates = dict(rec["gates"])
        gates[gate] = {"decision": "approved", **(payload or {})}
        self.store.update_run(run_id, gates=gates)
        self.store.append_event(run_id, "gate.closed", data={"gate": gate, "decision": "approved"})

    # ---- the run ---------------------------------------------------------------------------------------------
    async def run(self, ctx: RunContext) -> Outcome:
        rid = ctx.run_id
        rec = self.store.get_run(rid)
        done: list[str] = list(rec.get("done_steps", []))
        resumed = bool(done) or rec["status"] in ("partial", "paused_g15")
        if resumed:
            with contextlib.suppress(
                NotFound
            ):  # a saved graph exists after the first finished stage
                ctx.graph = SpecGraph.model_validate_json(self.store.read_text(rid, "graph.json"))
            ctx.emit("run.resumed", done=done)
        self.store.update_run(rid, status="running", partial_reason=None)
        ctx.emit(
            "run.started" if not resumed else "step.started", step="resume" if resumed else None
        )
        for stage in self.pipeline:
            if stage.name in done:
                continue
            needs_gate = stage.gate_before and (stage.gate_when is None or stage.gate_when(ctx))
            if needs_gate and stage.gate_before:
                if not self.store.get_run(rid)["gates"].get(stage.gate_before):
                    self.store.update_run(
                        rid,
                        status="paused_g15" if stage.gate_before == "g15" else "awaiting_g2",
                        step=stage.name,
                    )
                    ctx.emit("gate.opened", step=stage.name, gate=stage.gate_before)
                    ctx.save_graph()
                    return Outcome("paused", stage=stage.name)
            out = await self._run_stage(ctx, stage)
            if out is not None:
                return out
            done.append(stage.name)
            ctx.save_graph()
            self.store.update_run(rid, done_steps=done, step=stage.name)
        self.store.update_run(rid, status="awaiting_g2", step="done")
        ctx.emit("gate.opened", step="g2", gate="g2")
        return Outcome("awaiting_g2")

    async def _run_stage(self, ctx: RunContext, stage: Stage) -> Outcome | None:
        for s in stage.steps:
            ctx.emit("step.started", step=s.name)
        results = await asyncio.gather(*(s.fn(ctx) for s in stage.steps), return_exceptions=True)
        for s, r in zip(stage.steps, results, strict=True):
            if isinstance(r, BaseException):
                return self._stopped(ctx, stage, r)
            ctx.emit("step.finished", step=s.name)
        return None

    def _stopped(self, ctx: RunContext, stage: Stage, exc: BaseException) -> Outcome:
        """Map what ended a step to a plain, resumable `partial` reason (or fail loudly on a bug)."""
        mapping: list[tuple[type[BaseException], str, str]] = [
            (
                StopRequested,
                "stop",
                "stopped on request; resume continues from the last checkpoint",
            ),
            (
                NoCredit,
                "no_credit",
                "the provider has no credit left; top it up and resume",
            ),
            (
                CircuitOpen,
                "rate_limit",
                "the provider is limiting or refusing calls; resume when it recovers",
            ),
            (
                PlanInvalid,
                "plan_invalid",
                "the plan could not be written validly; resume retries it",
            ),
            (
                MissingNeed,
                "missing_need",
                "a builder need is missing, so the pack was not delivered; the list is in the run note",
            ),
        ]
        reason, msg = "", ""
        if isinstance(exc, SpendRefused):
            reason, msg = (
                exc.reason,
                "the money budget stopped the run; the pack was not delivered half-checked",
            )
        elif isinstance(exc, BudgetExceeded):
            reason, msg = "budget_cap", "the gateway budget stopped the run"
        else:
            for typ, r, m in mapping:
                if isinstance(exc, typ):
                    reason, msg = r, m
                    break
        if reason:
            self.store.update_run(
                ctx.run_id, status="partial", partial_reason=reason, step=stage.name, note=msg
            )
            ctx.emit("run.partial", step=stage.name, reason=reason, message=msg)
            return Outcome("partial", reason=reason, stage=stage.name)
        self.store.update_run(
            ctx.run_id, status="failed", step=stage.name, note=f"{type(exc).__name__}: {exc}"[:300]
        )
        ctx.emit("run.failed", step=stage.name, error=f"{type(exc).__name__}: {exc}"[:300])
        return Outcome(
            "failed", reason=type(exc).__name__, stage=stage.name, errors=[str(exc)[:300]]
        )
