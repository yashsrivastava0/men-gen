"""Test-only helpers (never imported by compose.py): a mock-mode gateway runner in a temp directory."""

from __future__ import annotations

from pathlib import Path

from mde_core.settings import Settings
from mde_spec_agent.calls.base import CallRunner
from mde_spec_agent.compose import build_context
from mde_spec_agent.config import SpecSettings
from mde_spec_agent.run.store import RunStore
from mde_spec_agent.spend import SpendRecord
from mde_spec_core.graph import SpecGraph


def mock_core() -> Settings:
    from mde_core.settings import load_settings

    return load_settings(llm_mode="mock")


def make_test_context(
    tmp_path: Path, graph: SpecGraph | None = None, program: str = "prog", input_hash: str = "h1"
):
    """A RunContext whose gateway runs every call's MOCK handler (offline), with a fresh store and spend record."""
    store = RunStore(tmp_path / "rt")
    j = store.create_run(program, input_hash, {"offline": True})
    settings = SpecSettings()
    rec = SpendRecord(
        tmp_path / "spend-record.json",
        cap_micros=settings.cap_micros,
        tripwire_micros=settings.tripwire_micros,
    )
    return build_context(
        store=store,
        run_id=j.run_id,
        program_id=program,
        input_hash=input_hash,
        graph=graph or SpecGraph(),
        core=mock_core(),
        settings=settings,
        record=rec,
        offline=True,
    )


__all__ = ["CallRunner", "make_test_context", "mock_core"]
