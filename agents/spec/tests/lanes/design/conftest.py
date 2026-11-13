"""Fixtures of the design lane tests: the three real exports as baseline graphs, a mock-mode run context, a one-stage
Orchestrator, and a scripted gateway wrapper (counts calls, fails or truncates on demand). Offline only."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import SpecInput, read_export
from mde_compass_intake.spec_input import Setting, Trust
from mde_core.errors import ProviderError
from mde_spec_agent.derive import derive_graph
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.orchestrator import Orchestrator, Outcome, Stage, StepDef
from mde_spec_agent.testing import make_test_context

ROOT = Path(__file__).resolve().parents[5]
EXPORTS = ROOT / "docs" / "compass" / "research-inputs"
THREE = {
    "quick": "quick-yoga-studio-bengaluru",
    "standard": "standard-produce-marketplace-nashik",
    "thorough": "thorough-freight-fleet-india",
}
PROFILE = {"quick": "compact", "standard": "standard", "thorough": "deep"}
COUNT = {"quick": 1, "standard": 3, "thorough": 3}


@cache
def _derived(key: str):
    path = EXPORTS / f"{THREE[key]}.json"
    if not path.exists():
        pytest.skip(f"export {key} is not in the repository")
    si = read_export(json.loads(path.read_text(encoding="utf-8")))
    return si, derive_graph(si)


class ScriptedGateway:
    """Wraps the context's mock-mode gateway: counts calls per spec id, can fail or truncate a spec on demand."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[tuple[str, int]] = []
        self.fail: set[str] = set()
        self.truncate_over: dict[
            str, int
        ] = {}  # spec id -> max items before the provider says "truncated"

    def count(self, spec_id: str) -> int:
        return sum(1 for s, _ in self.calls if s == spec_id)

    async def run(self, spec: Any, input: Any, ctx: Any) -> Any:
        items = len(getattr(input, "items", []) or [])
        self.calls.append((spec.id, items))
        if spec.id in self.fail:
            raise ProviderError("scripted provider failure")
        limit = self.truncate_over.get(spec.id)
        if limit is not None and items > limit:
            raise ProviderError("output was truncated at the token limit")
        return await self.inner.run(spec, input, ctx)

    async def budget_status(self) -> Any:
        return await self.inner.budget_status()


class Env:
    """What a test gets: build contexts, run one stage, read a baseline."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self._n = 0

    def baseline(self, key: str):
        si, d = _derived(key)
        return si, d.graph.model_copy(deep=True)

    def ctx(
        self,
        key: str,
        *,
        si: SpecInput | None = None,
        languages: list[str] | None = None,
        scripted: bool = True,
    ) -> RunContext:
        base_si, graph = self.baseline(key)
        if languages is not None:
            graph.meta.languages = list(languages)
        self._n += 1
        ctx = make_test_context(self.tmp_path / f"c{self._n}", graph, program=f"prog-{key}")
        ctx.spec_input = si or base_si
        if scripted:
            assert ctx.runner is not None
            gw = ScriptedGateway(ctx.runner._gateway)
            ctx.runner._gateway = gw
        return ctx

    @staticmethod
    def gateway(ctx: RunContext) -> ScriptedGateway:
        assert ctx.runner is not None
        gw = ctx.runner._gateway
        assert isinstance(gw, ScriptedGateway)
        return gw

    @staticmethod
    async def run_stage(ctx: RunContext, name: str, fn: Callable[..., Any]) -> Outcome:
        """A one-stage pipeline through the real Orchestrator."""
        return await Orchestrator(ctx.store, [Stage((StepDef(name, fn),))]).run(ctx)

    @staticmethod
    def with_brand(si: SpecInput, *values: str, grade: str = "locked") -> SpecInput:
        return si.model_copy(
            update={
                "brand": Setting(
                    values=tuple(values), trust=Trust(grade=grade, quote_ids=("FCT-001",))
                )
            }
        )


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


@pytest.fixture(params=["quick", "standard", "thorough"])
def key(request: pytest.FixtureRequest) -> str:
    return request.param
