"""Resume determinism: a killed batch, then a resume, ends with the identical canonical graph text."""

from __future__ import annotations

import pytest

from mde_spec_agent.lanes.screens.mock_hero import mock as hero_mock
from mde_spec_agent.lanes.screens.scrn import mock as scrn_mock


@pytest.mark.parametrize("victim", ["scrn", "hero"])
def test_killed_batch_then_resume_gives_identical_bytes(
    fresh, finished, pipeline_go, monkeypatch, victim
):
    reference = finished("standard").graph.canonical_text()
    run = fresh("standard")
    mod = scrn_mock if victim == "scrn" else hero_mock
    real = mod.handle
    state = {"calls": 0, "armed": True, "after_kill": 0}

    def handle(inp):
        if state["armed"]:
            state["calls"] += 1
            if state["calls"] == 2:
                state["armed"] = False
                raise RuntimeError("process killed")
        else:
            state["after_kill"] += 1
        return real(inp)

    monkeypatch.setattr(mod, "handle", handle)
    out = pipeline_go(run)
    assert out.status in (
        "failed",
        "partial",
    )  # a crash fails loudly; the store keeps every finished batch
    run.ctx.store.update_run(
        run.ctx.run_id, status="partial"
    )  # the operator marks it resumable after a restart
    out = pipeline_go(run)
    assert out.status == "awaiting_g2"
    assert run.graph.canonical_text() == reference
    assert state["after_kill"] >= 1
