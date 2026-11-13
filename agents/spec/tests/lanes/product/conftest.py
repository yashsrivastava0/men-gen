"""Fixtures of the product lane tests: the three real exports through the derivation, a mock-mode run context whose gateway
is wrapped by a scripted double (counts calls, rewrites an answer, raises or truncates on demand), and a run of the real
`plan` and `product` steps through the real Orchestrator. Offline only."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import SpecInput, read_export
from mde_core.errors import ProviderError
from mde_spec_agent.calls.base import CallRunner
from mde_spec_agent.derive import derive_graph
from mde_spec_agent.lanes.product import STEPS
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.orchestrator import Orchestrator, Outcome, Stage, StepDef
from mde_spec_agent.testing import make_test_context

ROOT = Path(__file__).resolve().parents[5]
EXPORTS = ROOT / "docs" / "compass" / "research-inputs"
THREE = {
    "quick": "quick-yoga-studio-bengaluru",
    "standard": "standard-produce-marketplace-nashik",
    "thorough": "thorough-freight-fleet-india",
    "delhi": "delhi-restaurant-delivery",
    "pune": "pune-clinic-appointments",
    "hospital": "hospital-chain-enterprise",
    "bike": "synthetic-small-bike-repair-porto",
    "school": "synthetic-medium-school-parents-dakar",
    "site": "synthetic-big-site-safety-central-america",
}
LANE_SRC = ROOT / "agents" / "spec" / "src" / "mde_spec_agent" / "lanes" / "product"


@cache
def load_input(key: str) -> SpecInput:
    path = EXPORTS / f"{THREE[key]}.json"
    if not path.exists():
        pytest.skip(f"export {key} is not in the repository")
    return read_export(json.loads(path.read_text(encoding="utf-8")))


class ScriptedGateway:
    """Wraps the context's mock-mode gateway. `rewrite(spec_id, input, n)` may return a function that changes the answer of
    the n-th call of that kind for that input; `fail` and `truncate_over` raise like a provider would."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[
            tuple[str, int, str]
        ] = []  # (spec id, items in the call, a label of the batch)
        self.rewrite: Callable[[str, Any, int], Callable[[Any], Any] | None] | None = None
        self.raise_on: Callable[[str, Any, int], BaseException | None] | None = None
        self.truncate_over: dict[str, int] = {}
        self._seen: dict[tuple[str, str], int] = {}

    @staticmethod
    def items_of(inp: Any) -> int:
        b = getattr(inp, "baseline", None)
        if b is None:
            return 0
        return sum(
            len(getattr(b, f, []))
            for f in ("entities", "stories", "rules", "integrations", "flows")
        )

    @staticmethod
    def label_of(inp: Any) -> str:
        return str(
            getattr(inp, "module_id", "")
            or ",".join(getattr(inp, "flow_ids", []) or getattr(inp, "integration_ids", []) or [])
        )

    def count(self, spec_id: str) -> int:
        return sum(1 for s, *_ in self.calls if s == spec_id)

    async def run(self, spec: Any, input: Any, ctx: Any) -> Any:
        items = self.items_of(input)
        label = self.label_of(input)
        n = self._seen.get((spec.id, label), 0) + 1
        self._seen[(spec.id, label)] = n
        self.calls.append((spec.id, items, label))
        limit = self.truncate_over.get(spec.id)
        if limit is not None and items > limit:
            raise ProviderError("output was truncated at the token limit")
        if self.raise_on is not None and (exc := self.raise_on(spec.id, input, n)) is not None:
            raise exc
        result = await self.inner.run(spec, input, ctx)
        if self.rewrite is not None and (fn := self.rewrite(spec.id, input, n)) is not None:
            result = result.model_copy(update={"output": fn(result.output)})
        return result

    async def budget_status(self) -> Any:
        return await self.inner.budget_status()


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self._n = 0

    def ctx(self, key: str, *, si: SpecInput | None = None, research: Any = None) -> RunContext:
        self._n += 1
        ctx = make_test_context(self.tmp_path / f"c{self._n}", program=f"prog-{key}")
        ctx.spec_input = si or load_input(key)
        ctx.research = research
        assert ctx.runner is not None
        ctx.runner._gateway = ScriptedGateway(ctx.runner._gateway)
        return ctx

    @staticmethod
    def gateway(ctx: RunContext) -> ScriptedGateway:
        assert ctx.runner is not None
        gw = ctx.runner._gateway
        assert isinstance(gw, ScriptedGateway)
        return gw

    @staticmethod
    def pipeline(*names: str) -> list[Stage]:
        return [Stage((StepDef(n, STEPS[n]),)) for n in names]

    def run(self, ctx: RunContext, *names: str) -> Outcome:
        orch = Orchestrator(ctx.store, self.pipeline(*(names or ("plan", "product"))))
        return asyncio.run(orch.run(ctx))

    @staticmethod
    def calls_of(ctx: RunContext, spec_id: str) -> list[Any]:
        assert ctx.runner is not None
        return [r for s, r in ctx.runner.calls if s == spec_id]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


@pytest.fixture(params=list(THREE))
def key(request: pytest.FixtureRequest) -> str:
    return request.param


@cache
def baseline(key: str):
    return derive_graph(load_input(key))


def profile_of(key: str) -> str:
    return baseline(key).decision.profile


def is_compact(key: str) -> bool:
    return profile_of(key) == "compact"


__all__ = [
    "CallRunner",
    "Env",
    "ScriptedGateway",
    "baseline",
    "is_compact",
    "load_input",
    "profile_of",
]
