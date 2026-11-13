"""Fixtures of the Screens-lane tests: the three real exports through the derivation (the baseline that stands in for the
FROZEN graph), a one-stage Orchestrator per step, and a session cache of finished runs (every test reads, none repeats)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import ResearchView, SpecInput, read_export, read_research
from mde_spec_agent.calls.registry import all_steps
from mde_spec_agent.derive import derive_graph
from mde_spec_agent.run.orchestrator import Orchestrator, Outcome, Stage, StepDef
from mde_spec_agent.testing import make_test_context
from mde_spec_core.graph import SpecGraph

ROOT = Path(__file__).resolve().parents[5]
EXPORTS = ROOT / "docs" / "compass" / "research-inputs"
RUNS = ROOT / "agents" / "research" / ".runtime" / "runs"
THREE = {
    "quick": "quick-yoga-studio-bengaluru",
    "standard": "standard-produce-marketplace-nashik",
    "thorough": "thorough-freight-fleet-india",
}
PIPELINE = (("screens", "hero", "behav"), ("copyl",), ("g2summary",))


@cache
def load_export(name: str) -> SpecInput:
    path = EXPORTS / f"{name}.json"
    if not path.exists():
        pytest.skip(f"export {name} is not in the repository")
    return read_export(json.loads(path.read_text(encoding="utf-8")))


def best_research(si: SpecInput) -> ResearchView | None:
    best = None
    if not RUNS.exists():
        return None
    for run in sorted(RUNS.iterdir()):
        p = run / "research.json"
        if not p.exists():
            continue
        try:
            v = read_research(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
        if v.is_for(si.brief_hash) and v.status == "complete" and not v.thin:
            best = v
    return best


@dataclass
class Run:
    ctx: Any
    orch: Orchestrator
    outcome: Outcome | None
    baseline: SpecGraph
    spec_input: SpecInput

    @property
    def graph(self) -> SpecGraph:
        return self.ctx.graph


def make_run(tmp: Path, key: str, *, research: bool = False) -> Run:
    si = load_export(THREE[key])
    derived = derive_graph(si)
    ctx = make_test_context(tmp, derived.graph.model_copy(deep=True))
    ctx.spec_input = si
    ctx.facts = set(derived.facts)
    ctx.research = best_research(si) if research else None
    steps = all_steps()
    pipeline = [Stage(tuple(StepDef(n, steps[n]) for n in names)) for names in PIPELINE]
    return Run(ctx, Orchestrator(ctx.store, pipeline), None, derived.graph, si)


def go(run: Run, until: int | None = None) -> Outcome:
    if until is not None:
        run.orch.pipeline = run.orch.pipeline[:until]
    run.outcome = asyncio.run(run.orch.run(run.ctx))
    return run.outcome


@pytest.fixture(scope="session")
def finished(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Run]:
    """One complete mock-mode run per export (cached for the session)."""
    cache_: dict[str, Run] = {}

    def get(key: str) -> Run:
        if key not in cache_:
            run = make_run(tmp_path_factory.mktemp(f"run-{key}"), key)
            go(run)
            assert run.outcome and run.outcome.status == "awaiting_g2", run.outcome
            cache_[key] = run
        return cache_[key]

    return get


@pytest.fixture
def fresh(tmp_path: Path) -> Callable[..., Run]:
    def make(key: str = "quick", **kw: Any) -> Run:
        return make_run(tmp_path, key, **kw)

    return make


@pytest.fixture(params=["quick", "standard", "thorough"])
def key(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def pipeline_go() -> Callable[..., Outcome]:
    return go
