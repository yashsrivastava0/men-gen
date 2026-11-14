"""The code-only skeleton (no lanes registered) runs end to end on real exports and synthetic programs: intake -> join ->
freeze -> (G1.5) -> assemble + gate 3. Offline, zero cost."""

import asyncio
from pathlib import Path

import pytest

from mde_spec_agent.compose import build_context
from mde_spec_agent.config import SpecSettings
from mde_spec_agent.run.orchestrator import Orchestrator
from mde_spec_agent.run.pipeline import build_pipeline
from mde_spec_agent.run.store import RunStore
from mde_spec_agent.spend import SpendRecord
from mde_spec_agent.testing import mock_core
from mde_spec_core.graph import SpecGraph

INPUTS = Path(__file__).resolve().parents[4] / "docs" / "compass" / "research-inputs"


def make(tmp_path, **fields):
    store = RunStore(tmp_path / "rt")
    j = store.create_run("prog", "h1", {"offline": True, **fields})
    s = SpecSettings()
    rec = SpendRecord(
        tmp_path / "spend.json", cap_micros=s.cap_micros, tripwire_micros=s.tripwire_micros
    )
    ctx = build_context(
        store=store,
        run_id=j.run_id,
        program_id="prog",
        input_hash="h1",
        graph=SpecGraph(),
        core=mock_core(),
        settings=s,
        record=rec,
        offline=True,
    )
    return store, ctx


def go(store, ctx):
    orch = Orchestrator(store, build_pipeline())
    out = asyncio.run(orch.run(ctx))
    while out.status == "paused":
        orch.approve_gate(ctx.run_id, "g15", {"consent": "test"})
        out = asyncio.run(orch.run(ctx))
    return out


@pytest.mark.parametrize(
    ("stem", "profile"),
    [
        ("quick-yoga-studio-bengaluru", "compact"),
        ("standard-produce-marketplace-nashik", "standard"),
    ],
)
def test_real_exports_run_to_g2(tmp_path, stem, profile):
    store, ctx = make(tmp_path, compass_path=str(INPUTS / f"{stem}.json"))
    out = go(store, ctx)
    assert out.status == "awaiting_g2", (out, store.get_run(ctx.run_id).get("note"))
    assert ctx.graph.meta.profile == profile and ctx.pack is not None
    assert (store.run_dir(ctx.run_id) / "pack" / "MANIFEST.json").is_file()
    assert (store.run_dir(ctx.run_id) / "evidence" / "self-check.json").is_file()


def test_thorough_pauses_at_g15_then_finishes(tmp_path):
    store, ctx = make(tmp_path, compass_path=str(INPUTS / "thorough-freight-fleet-india.json"))
    orch = Orchestrator(store, build_pipeline())
    out = asyncio.run(orch.run(ctx))
    assert (
        out.status == "paused" and store.get_run(ctx.run_id)["status"] == "paused_g15"
    )  # a hard gate for deep
    orch.approve_gate(ctx.run_id, "g15", {"consent": "test"})
    out = asyncio.run(orch.run(ctx))
    assert out.status == "awaiting_g2"


def test_synthetic_programs_run_to_g2(tmp_path):
    store, ctx = make(tmp_path, synth="program")
    out = go(store, ctx)
    assert out.status == "awaiting_g2" and len(ctx.graph.screens) == 140


def test_identical_bytes_across_two_runs(tmp_path):
    a = make(tmp_path / "a", synth="small")
    b = make(tmp_path / "b", synth="small")
    go(*a)
    go(*b)
    assert a[1].pack.content_hash == b[1].pack.content_hash and a[1].pack.files == b[1].pack.files
