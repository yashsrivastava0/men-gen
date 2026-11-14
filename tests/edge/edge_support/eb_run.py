"""Shared helpers of the MD, FE, CH, OP, UX, FN and BG edge cases (offline, deterministic, no live call).

* ``Rig`` builds a Spec RunContext whose REAL gateway sits on a ``ScriptedProvider``: by default every call answers with its
  own mock handler's output (the gateway's mock mode), and a case queues a fault (invalid JSON, cut-off output, a refusal,
  429s, 402s, a slow answer) for one call id. The real Gateway, SpendGuard, CallRunner, Orchestrator and lane steps run.
* ``ScriptedGateway`` is the thinner double of the lane tests (rewrites an answer, raises or truncates on demand).
* graph helpers: synthetic presets, pack assembly, validators.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import SpecInput, read_export
from mde_core.clock import FakeClock
from mde_core.settings import load_settings
from mde_llm_gateway import InProcessGatewayClient, StaticRegistry, create_gateway
from mde_llm_gateway.providers.base import ProviderRequest
from mde_llm_gateway.providers.mock import resolve_handler
from mde_llm_gateway.providers.scripted import ScriptedProvider
from mde_spec_agent.calls.registry import all_calls, all_steps
from mde_spec_agent.compose import build_context, gateway_config
from mde_spec_agent.config import SpecSettings, spec_budget_config
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.orchestrator import Orchestrator, Outcome, Stage, StepDef
from mde_spec_agent.run.store import RunStore
from mde_spec_agent.spend import SpendRecord

ROOT = Path(__file__).resolve().parents[3]
EXPORTS = ROOT / "docs" / "compass" / "research-inputs"
KEYS = {
    "quick": "quick-yoga-studio-bengaluru",
    "standard": "standard-produce-marketplace-nashik",
    "thorough": "thorough-freight-fleet-india",
    "hospital": "hospital-chain-enterprise",
}
FIXTURES = ROOT / "tests" / "edge" / "fixtures"


@cache
def load_input(key: str) -> SpecInput:
    path = EXPORTS / f"{KEYS.get(key, key)}.json"
    if not path.exists():
        pytest.skip(f"export {key} is not in the repository")
    return read_export(json.loads(path.read_text(encoding="utf-8")))


def fixture_text(case_id: str, name: str) -> str:
    return (FIXTURES / case_id / name).read_text(encoding="utf-8")


def mock_answer(req: ProviderRequest) -> Any:
    """What the gateway's mock mode would answer: the call spec's own mock handler on the request input."""
    return resolve_handler(req.spec.mock_handler)(req.input)


class Rig:
    """A RunContext on a real Gateway over a ScriptedProvider whose default answer is the mock handler's."""

    def __init__(
        self,
        tmp_path: Path,
        key: str = "standard",
        *,
        spec_input: SpecInput | None = None,
        research: Any = None,
        cap_micros: int | None = None,
        tag: str = "r",
        settings: SpecSettings | None = None,
        synth: str | None = None,
        compass_path: Path | None = None,
        extra_specs: list[Any] | None = None,
    ) -> None:
        self.tmp_path = tmp_path / tag
        self.tmp_path.mkdir(parents=True, exist_ok=True)
        self.clock = FakeClock()
        self.provider = ScriptedProvider(clock=self.clock)
        self.provider.set_default("*", mock_answer)
        core = load_settings(llm_mode="mock")
        self.settings = settings or SpecSettings()
        specs = [c.spec for c in all_calls()] + list(extra_specs or [])
        self.gateway = InProcessGatewayClient(
            create_gateway(
                core,
                StaticRegistry(specs),
                provider=self.provider,
                clock=self.clock,
                config=gateway_config(offline=True),
                budget_config=spec_budget_config(
                    core.llm_budget_usd, self.settings.spec_live_budget_usd
                ),
            )
        )
        self.store = RunStore(self.tmp_path / "rt")
        self.input_hash = "h-" + key
        fields: dict[str, Any] = {"offline": True}
        if synth:
            fields["synth"] = synth
        else:
            fields["compass_path"] = str(compass_path or EXPORTS / f"{KEYS.get(key, key)}.json")
        self.run = self.store.create_run(f"prog-{key}", self.input_hash, fields)
        self.record = SpendRecord(
            self.tmp_path / "spend-record.json",
            cap_micros=cap_micros if cap_micros is not None else self.settings.cap_micros,
            tripwire_micros=self.settings.tripwire_micros
            if cap_micros is None
            else max(1, cap_micros - 1),
        )
        from mde_spec_core.graph import SpecGraph

        self.ctx: RunContext = build_context(
            store=self.store,
            run_id=self.run.run_id,
            program_id=f"prog-{key}",
            input_hash=self.input_hash,
            graph=SpecGraph(),
            core=core,
            settings=self.settings,
            record=self.record,
            offline=True,
            gateway=self.gateway,
            clock=self.clock,
        )
        self.ctx.spec_input = spec_input or (None if synth else load_input(key))
        self.ctx.research = research

    # ---- driving ------------------------------------------------------------------------------------------
    @staticmethod
    def pipeline(*names: str) -> list[Stage]:
        from mde_spec_agent.run import steps_code

        steps = {
            "intake": steps_code.intake,
            "join": steps_code.join,
            "freeze": steps_code.freeze,
            "assemble": steps_code.assemble_and_check,
            **all_steps(),
        }
        return [Stage((StepDef(n, steps[n]),)) for n in names]

    def go(self, *names: str) -> Outcome:
        orch = Orchestrator(self.store, self.pipeline(*(names or ("plan", "product"))))
        return asyncio.run(orch.run(self.ctx))

    def full(self, *, verify: Any = None) -> Outcome:
        """The whole pipeline (intake ... g2summary); a G1.5 gate, when it opens, is approved and the run continues."""
        from mde_spec_agent.run.pipeline import build_pipeline

        orch = Orchestrator(self.store, build_pipeline(verify=verify))
        self.orch = orch
        out = asyncio.run(orch.run(self.ctx))
        while out.status == "paused":
            orch.approve_gate(self.ctx.run_id, "g15", {"consent": "all"})
            out = asyncio.run(orch.run(self.ctx))
        return out

    NO_INTAKE = (
        "plan", "product", "design", "join", "freeze", "gloss", "screens", "hero", "behav",
        "copyl", "assemble", "g2summary",
    )  # fmt: skip

    def full_from_input(self) -> Outcome:
        """Every step except `intake`: the plan step derives the graph from `ctx.spec_input` (a mutated input stays mutated)."""
        return self.go(*self.NO_INTAKE)

    def resume_full(self) -> Outcome:
        """A new process picks the whole pipeline up again (in-memory plan and flags are gone)."""
        self.ctx.plan.clear()
        self.ctx.flags.clear()
        return self.full()

    def resume(self, *names: str) -> Outcome:
        """A new process picks the run up: the in-memory plan and flags are gone, the ledger of re-asks is not."""
        self.ctx.plan.clear()
        self.ctx.flags.clear()
        return self.go(*names)

    def calls(self, spec_id: str) -> list[Any]:
        return [r for s, r in (self.ctx.runner.calls if self.ctx.runner else []) if s == spec_id]

    def provider_calls(self, spec_id: str) -> list[ProviderRequest]:
        return self.provider.calls_for(spec_id)


@cache
def reference_graph_text(key: str, steps: tuple[str, ...]) -> str:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        rig = Rig(Path(d), key)
        out = rig.go(*steps)
        assert out.status in {"awaiting_g2", "paused"}, out
        return rig.ctx.graph.canonical_text()


class ScriptedGateway:
    """Wraps the gateway of a context: counts calls, rewrites answers, raises on demand (the lane tests' double)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[tuple[str, Any]] = []
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

    def count(self, spec_id: str) -> int:
        return sum(1 for s, _ in self.calls if s == spec_id)

    async def run(self, spec: Any, input: Any, ctx: Any) -> Any:
        from mde_core.errors import ProviderError

        label = str(getattr(input, "module_id", "") or "")
        n = self._seen.get((spec.id, label), 0) + 1
        self._seen[(spec.id, label)] = n
        self.calls.append((spec.id, input))
        limit = self.truncate_over.get(spec.id)
        if limit is not None and self.items_of(input) > limit:
            raise ProviderError("output was truncated at the token limit")
        if self.raise_on is not None and (exc := self.raise_on(spec.id, input, n)) is not None:
            raise exc
        result = await self.inner.run(spec, input, ctx)
        if self.rewrite is not None and (fn := self.rewrite(spec.id, input, n)) is not None:
            result = result.model_copy(update={"output": fn(result.output)})
        return result

    async def budget_status(self) -> Any:
        return await self.inner.budget_status()


def wrap_gateway(ctx: RunContext) -> ScriptedGateway:
    assert ctx.runner is not None
    gw = ScriptedGateway(ctx.runner._gateway)
    ctx.runner._gateway = gw
    return gw


# ---- graph level -----------------------------------------------------------------------------------------------
def pack_of(graph: Any, **opts: Any) -> Any:
    from mde_spec_core.pack import PackOptions, assemble

    return assemble(graph, PackOptions(**opts) if opts else None)


def findings_of(
    graph: Any, pack: Any = None, *, only: set[str] | None = None, **ctx: Any
) -> list[Any]:
    from mde_spec_core.validators.base import run_all
    from mde_spec_core.validators.context import make_context

    return run_all(make_context(graph, pack=pack, **ctx), gate=3, only=only)


def codes(findings: list[Any]) -> set[str]:
    return {f.code for f in findings}


def nodes_of(graph: Any) -> dict[str, Any]:
    """The graph's nodes without the per-call input hash (which differs when a batch was split)."""
    data = json.loads(graph.canonical_text())

    def clean(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k != "input_hash"}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x

    return clean(data)


def source_mentions(*words: str) -> bool:
    """True when any Spec or spec-core source file contains one of the words (case-insensitive): a behaviour the docs
    promise has no implementation at all when nothing mentions it."""
    roots = [
        ROOT / "agents" / "spec" / "src",
        ROOT / "packages" / "spec-core" / "src",
        ROOT / "agents" / "verify" / "src",
    ]
    low = [w.lower() for w in words]
    for root in roots:
        for p in root.rglob("*.py"):
            text = p.read_text(encoding="utf-8").lower()
            if any(w in text for w in low):
                return True
    return False


# ---- the HTTP API over the real service (mock mode, private data folder) ---------------------------------------------
def api_client(tmp_path: Path, monkeypatch: Any, **spec_settings: Any) -> Any:
    """``with api_client(tmp_path, monkeypatch) as client``: the real app over the real service in mock mode."""
    from contextlib import contextmanager

    from fastapi.testclient import TestClient

    from mde_spec_agent.api import create_app
    from mde_spec_agent.config import load_spec_settings
    from mde_spec_agent.service import SpecService

    monkeypatch.setenv("MDE_ENV_FILE", "")
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path / "no-research"))

    @contextmanager
    def build() -> Any:
        settings = load_spec_settings(spec_data_dir=tmp_path / "data", **spec_settings)
        core = load_settings(llm_mode="mock", openai_api_key=None, anthropic_api_key=None)
        service = SpecService(settings, core, research_runs_dir=tmp_path / "no-research")
        with TestClient(create_app(service, settings=settings)) as client:
            client.service = service  # type: ignore[attr-defined]
            yield client

    return build()


def wait_for(
    client: Any, run_id: str, until: tuple[str, ...], timeout: float = 120.0
) -> dict[str, Any]:
    import time

    end = time.monotonic() + timeout
    snap: dict[str, Any] = {}
    while time.monotonic() < end:
        snap = client.get(f"/api/runs/{run_id}").json()
        if snap["status"] in until or snap["status"] in ("failed",):
            assert snap["status"] in until, (snap["status"], snap.get("note"))
            return snap
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} stayed {snap.get('status')} ({snap.get('note')})")
