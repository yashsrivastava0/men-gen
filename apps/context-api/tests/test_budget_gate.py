"""The budget gate (GAP-5): the flow's ``BudgetGate`` over the gateway's budget status, its wiring
in the composition root, and the whole pause over HTTP (spec 9: "pause before the Plan phase;
explain; ask an approver")."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mde_context_api import budget
from mde_context_api.budget import (
    TRIPWIRE_MESSAGE,
    GatewayBudgetGate,
    NoopBudgetGate,
    budget_gate_for,
)
from mde_context_api.composition import build_app_state
from mde_context_api.testing import Api, make_client, make_settings
from mde_contracts.gateway import BudgetStatus
from mde_core.money import usd_to_micros
from mde_core.settings import Settings
from mde_flow import messages
from mde_flow.testing import InMemoryStore
from mde_llm_gateway import BudgetConfig, Gateway, InProcessGatewayClient, create_gateway
from mde_prompts.registry import PromptRegistry

HEADROOM = 50_000


def gateway(session_cap: int = 500_000) -> Gateway:
    config = BudgetConfig(
        cap_micros=usd_to_micros("6.00"),
        scope_caps_micros={
            "session": session_cap,
            "research": 300_000,
            "eval": 1_500_000,
            "test": 300_000,
        },
    )
    settings = Settings(_env_file=None, llm_mode="mock")  # type: ignore[call-arg]
    return create_gateway(settings, PromptRegistry(), budget_config=config)


def status(**kw: Any) -> BudgetStatus:
    fields: dict[str, Any] = {
        "cap_micros": 6_000_000,
        "effective_cap_micros": 5_700_000,
        "spent_micros": 0,
        "reserved_micros": 0,
    }
    return BudgetStatus(**{**fields, **kw})


def fixed(value: BudgetStatus) -> Any:
    async def read() -> BudgetStatus:
        return value

    return read


async def verdict(gate: GatewayBudgetGate, session: str = "ses_1"):  # type: ignore[no-untyped-def]
    return await gate.check(session_id=session, tenant_id="tenant-a")


# ----------------------------------------------------------------------------- the verdicts


async def test_a_fresh_budget_lets_planning_start() -> None:
    gate = GatewayBudgetGate.for_gateway(gateway())
    v = await verdict(gate)
    assert v.ok and v.reason == "ok" and v.message is None


async def test_the_sessions_own_cap_pauses_that_session_only() -> None:
    gw = gateway(session_cap=100_000)
    gate = GatewayBudgetGate.for_gateway(gw, plan_reserve_micros=HEADROOM)
    gw.guard.reserve(["session:ses_a"], 60_000, purpose="plan")  # leaves 40k: less than one plan
    spent = await verdict(gate, "ses_a")
    assert not spent.ok and spent.reason == "cap_reached"
    assert spent.message == messages.BUDGET_REACHED
    assert (await verdict(gate, "ses_b")).ok  # another session's cap is untouched


async def test_a_session_with_exactly_one_plan_of_headroom_may_start() -> None:
    gw = gateway(session_cap=100_000)
    gate = GatewayBudgetGate.for_gateway(gw, plan_reserve_micros=HEADROOM)
    gw.guard.reserve(["session:ses_a"], 50_000, purpose="plan")  # 50k left: exactly one plan
    assert (await verdict(gate, "ses_a")).ok


@pytest.mark.parametrize("tripwire", ["refuse_95", "must_run_only_80"])
async def test_the_tripwire_pauses_everything(tripwire: str) -> None:
    gate = GatewayBudgetGate(fixed(status(tripwire=tripwire)))
    v = await verdict(gate)
    assert not v.ok and v.reason == "tripwire" and v.message == TRIPWIRE_MESSAGE


async def test_the_warning_tripwire_does_not_pause() -> None:
    assert (await verdict(GatewayBudgetGate(fixed(status(tripwire="warn_50"))))).ok


async def test_the_whole_budget_without_a_plans_headroom_pauses() -> None:
    nearly = status(effective_cap_micros=1_000_000, spent_micros=900_000, reserved_micros=60_000)
    v = await verdict(GatewayBudgetGate(fixed(nearly), plan_reserve_micros=HEADROOM))
    assert not v.ok and v.reason == "cap_reached"
    enough = status(effective_cap_micros=1_000_000, spent_micros=900_000, reserved_micros=50_000)
    assert (await verdict(GatewayBudgetGate(fixed(enough), plan_reserve_micros=HEADROOM))).ok


async def test_what_the_user_is_told_never_contains_a_number() -> None:
    for text in (messages.BUDGET_REACHED, TRIPWIRE_MESSAGE):
        assert not re.search(r"\d", text) and "$" not in text and "USD" not in text


# ----------------------------------------------------------------------------- which gate


async def test_an_in_process_gateway_gets_the_full_gate() -> None:
    gw = gateway(session_cap=100_000)
    gate = budget_gate_for(InProcessGatewayClient(gw), plan_reserve_micros=HEADROOM)
    assert isinstance(gate, GatewayBudgetGate)
    gw.guard.reserve(["session:ses_a"], 90_000, purpose="plan")
    assert not (await gate.check(session_id="ses_a", tenant_id="t")).ok


async def test_another_client_with_a_status_gets_the_global_part_only() -> None:
    sync = SimpleNamespace(budget_status=lambda: status(tripwire="refuse_95"))
    gate = budget_gate_for(sync)  # type: ignore[arg-type]
    assert isinstance(gate, GatewayBudgetGate)
    assert not (await gate.check(session_id="s", tenant_id="t")).ok

    async def read() -> BudgetStatus:
        return status()

    asynchronous = SimpleNamespace(budget_status=read)
    gate2 = budget_gate_for(asynchronous)  # type: ignore[arg-type]
    assert (await gate2.check(session_id="s", tenant_id="t")).ok


async def test_a_client_without_a_status_never_pauses_planning() -> None:
    gate = budget_gate_for(SimpleNamespace())  # type: ignore[arg-type]
    assert isinstance(gate, NoopBudgetGate)
    assert (await gate.check(session_id="s", tenant_id="t")).ok


# ----------------------------------------------------------------------------- the composition


async def test_the_composition_root_wires_the_gate_with_the_configured_reserve(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path, budget_plan_reserve_usd="1.10")  # more than a session cap
    state = await build_app_state(settings, store=InMemoryStore(), fake_builder=True)
    try:
        assert isinstance(state.flow.budget_gate, GatewayBudgetGate)
        v = await state.flow.budget_gate.check(session_id="ses_1", tenant_id="t")
        assert not v.ok  # the default session cap (USD 1.00, D-124) cannot hold a USD 1.10 plan
    finally:
        await state.aclose()
    default = await build_app_state(
        make_settings(tmp_path), store=InMemoryStore(), fake_builder=True
    )
    try:
        assert default.flow.budget_gate is not None
        assert (await default.flow.budget_gate.check(session_id="ses_1", tenant_id="t")).ok
    finally:
        await default.aclose()


def test_the_default_reserve_is_five_cents() -> None:
    assert usd_to_micros("0.05") == budget.DEFAULT_PLAN_RESERVE_MICROS


# ----------------------------------------------------------------------------- over HTTP


async def test_the_pause_and_an_approvers_resume_over_http(tmp_path: Path) -> None:
    gw = gateway(session_cap=10)  # ten micro-dollars: nothing can be planned
    settings = make_settings(tmp_path / "projects")
    state = await build_app_state(
        settings, store=InMemoryStore(), fake_builder=True, gateway=InProcessGatewayClient(gw)
    )
    try:
        async with make_client(state) as client:
            api = Api(client)
            await api.create("enterprise")
            await api.to_gate1()
            resp = await api.approve("brief")  # the requester is the only Gate 1 approver
            body = resp.json()
            assert body["state"] == "PAUSED" and body["job"] is None
            notices = [e for e in body["events"] if e["type"] == "notice"]
            assert [n["code"] for n in notices] == ["budget_cap_reached"]
            assert "Someone who can approve it needs to raise the limit" in notices[0]["text"]
            assert "Waiting for: client_product_owner, delivery_lead." in notices[0]["text"]
            assert not re.search(r"\d", notices[0]["text"])
            snap = await api.snapshot()
            assert snap["state"] == "PAUSED" and snap["paused_for"] == "budget"

            lead = api.as_user("dana", ["delivery_lead"])
            out = await lead.ui("resume")  # an approver may try; the budget is still out
            assert [e.code for e in out if e.type == "notice"] == ["budget_cap_reached"]  # type: ignore[attr-defined]
            assert (await api.snapshot())["state"] == "PAUSED"

            stranger = api.as_user("eve", ["intern"])
            assert (await stranger.post(_resume(stranger))).status_code == 404

            gw.guard.config.scope_caps_micros["session"] = 500_000  # type: ignore[index]  # raised
            out = await lead.ui("resume")
            snap = await api.snapshot()
            assert snap["state"] == "PLANNING" and snap["paused_for"] is None
            await state.flow.run_next_job()
            assert (await api.snapshot())["state"] == "PLAN_REVIEW"

            denied = await lead.post(_resume(lead))  # nothing is held any more: not theirs to do
            assert denied.status_code == 403
    finally:
        await state.aclose()


def _resume(api: Api) -> dict[str, Any]:
    return {"type": "ui_action", "event_id": api.eid(), "action": "resume"}


async def test_the_pause_is_the_same_on_a_plain_client(api: Api) -> None:
    """With the default (generous) caps nothing pauses: the run starts as before."""
    await api.create("enterprise")
    await api.to_gate1()
    resp = await api.approve("brief")
    assert resp.json()["state"] == "PLANNING"
