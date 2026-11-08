"""End-to-end happy path through the service with the scripted engines: hi -> spec_ready."""

from __future__ import annotations

import pytest

from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    GateEvent,
    PlanReady,
    ProgressEvent,
    SpecReady,
    StateChanged,
)
from mde_core.hashing import sha256_hex
from mde_flow.testing.kit import make_flow_kit


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run the happy path over each store of the matrix (conftest: memory, Postgres)."""


async def test_hi_to_spec_ready_through_both_gates() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    assert [type(e) for e in d.events] == [AssistantMessage]

    gate1 = await d.to_gate1()
    assert gate1.gate == "brief" and not gate1.single_button
    assert gate1.buttons == ["Looks right", "Change something"]
    snap = await d.snapshot()
    assert snap.state == "BRIEF_REVIEW" and snap.gate is not None

    events = await d.approve_gate1()
    assert any(isinstance(e, StateChanged) and e.to_state == "PLANNING" for e in events)
    assert (await d.snapshot()).state == "PLANNING"
    assert kit.frontend.notifications == []

    assert await d.run_job()
    await d.refresh()
    assert any(isinstance(e, ProgressEvent) for e in d.events)
    assert any(isinstance(e, PlanReady) for e in d.events)
    gate2 = d.last_gate("plan")
    assert isinstance(gate2, GateEvent)
    assert (await d.snapshot()).state == "PLAN_REVIEW"
    assert kit.frontend.notifications == []  # not before Gate 2 approval

    events = await d.approve_gate2()
    ready = [e for e in events if isinstance(e, SpecReady)]
    assert len(ready) == 1
    assert ready[0].hash == gate2.hash and ready[0].version == gate2.version
    assert kit.frontend.notifications == ready
    snap = await d.snapshot()
    assert snap.state == "HANDED_OFF" and snap.handed_off_version == gate2.version

    spec = await kit.service.get_spec(d.who, d.session_id)
    assert spec is not None and spec.status == "handed_off" and spec.git_sha == ready[0].git_sha
    assert sha256_hex(spec.spec_text) == spec.hash
    assert len(kit.fake_repo.commits) == 1
    assert any(isinstance(e, CardEvent) for e in d.events)
