"""Liveness, readiness and the dev budget route."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mde_context_api import __version__, routes
from mde_context_api.composition import AppState, build_app_state
from mde_context_api.main import create_app
from mde_context_api.testing import Api, build_state, headers, make_client, make_settings
from mde_contracts.gateway import BudgetStatus
from mde_flow.testing import InMemoryStore


async def test_healthz_is_alive_and_touches_nothing(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))  # no state: nothing may be built for /healthz
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}
    assert not app.state.holder.built


async def test_readyz_is_ready_when_the_database_answers(mem_client: httpx.AsyncClient) -> None:
    resp = await mem_client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "checks": {"database": "ok", "ledger": "ok"}}


async def test_readyz_is_503_when_the_database_is_unreachable(
    mem_client: httpx.AsyncClient, mem_state: AppState
) -> None:
    async def down() -> None:
        raise ConnectionRefusedError("connection refused: postgres://user:secret@db/mde")

    mem_state.ping = down
    resp = await mem_client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {
        "status": "not_ready",
        "checks": {"database": "unreachable", "ledger": "ok"},  # each check answers on its own
    }
    assert "secret" not in resp.text  # never the reason


async def test_readyz_is_503_when_the_state_cannot_even_be_built(tmp_path: Path) -> None:
    async def broken(settings: Any) -> AppState:
        raise RuntimeError("cannot build")

    app = create_app(make_settings(tmp_path), state_factory=broken)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"
        assert (await client.get("/healthz")).status_code == 200  # still alive


@pytest.fixture
async def unreachable(tmp_path: Path) -> AsyncIterator[AppState]:
    """A real PostgresStore pointed at a port nothing listens on (no server is needed)."""
    from mde_persistence.db import create_engine
    from mde_persistence.store import PostgresStore

    engine = create_engine(url="postgresql+psycopg://mde:mde@127.0.0.1:1/none")
    try:
        yield await build_state(make_settings(tmp_path / "projects"), PostgresStore(engine))
    finally:
        await engine.dispose()


async def test_readyz_with_a_real_unreachable_postgres_is_503_and_prompt(
    unreachable: AppState,
) -> None:
    async with make_client(unreachable) as client:
        resp = await client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["checks"] == {"database": "unreachable", "ledger": "ok"}
        assert (await client.get("/healthz")).status_code == 200


async def test_readyz_is_ready_on_every_store(state: AppState) -> None:
    async with make_client(state) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["checks"] == {"database": "ok", "ledger": "ok"}


async def test_budget_reports_the_gateway_status(api: Api) -> None:
    await api.create()
    await api.to_gate1()
    resp = await api.client.get("/v1/budget", headers=api.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "cap_micros",
        "effective_cap_micros",
        "spent_micros",
        "reserved_micros",
        "live_calls",
    }  # totals only: no by_scope, no tripwire
    status = BudgetStatus.model_validate(
        body
    )  # (a client that reads the gateway's shape still can)
    assert status.cap_micros == 6_000_000 and status.effective_cap_micros == 5_700_000
    assert status.spent_micros == 0 and status.live_calls == 0  # mock mode: nothing is billed


async def test_budget_never_lists_other_tenants_sessions(
    mem_client: httpx.AsyncClient, mem_state: AppState
) -> None:
    """N7: the gateway's ledger is keyed by ``session:<id>`` across every tenant. The context API
    shows the totals a developer needs and nothing per scope."""
    other = "session:ses_01OTHERTENANTSECRET"
    mem_state.gateway = SimpleNamespace(  # type: ignore[assignment]
        budget_status=lambda: BudgetStatus(
            cap_micros=6_000_000,
            effective_cap_micros=5_700_000,
            spent_micros=1_234_567,
            reserved_micros=89,
            by_scope={other: 1_000_000, "eval:alpha": 5, "session:ses_mine": 7},
            tripwire="must_run_only_80",
            live_calls=3,
        )
    )
    resp = await mem_client.get("/v1/budget", headers=headers(tenant="tenant-b"))
    assert resp.status_code == 200
    assert resp.json() == {
        "cap_micros": 6_000_000,
        "effective_cap_micros": 5_700_000,
        "spent_micros": 1_234_567,
        "reserved_micros": 89,
        "live_calls": 3,
    }
    assert "ses_01OTHERTENANTSECRET" not in resp.text and "by_scope" not in resp.text
    assert "tripwire" not in resp.text


async def test_budget_accepts_a_synchronous_gateway(
    mem_client: httpx.AsyncClient, mem_state: AppState
) -> None:
    fixed = BudgetStatus(
        cap_micros=10, effective_cap_micros=9, spent_micros=4, reserved_micros=1, live_calls=2
    )
    mem_state.gateway = SimpleNamespace(budget_status=lambda: fixed)  # type: ignore[assignment]
    resp = await mem_client.get("/v1/budget", headers=headers())
    assert resp.status_code == 200 and resp.json()["spent_micros"] == 4


# --------------------------------------------------------------------------- the ledger check


def real_ledger_state(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A state over a gateway with a real ledger file (and its guard file), plus a way to tamper."""
    from mde_core.settings import Settings
    from mde_llm_gateway import FileLedger, InProcessGatewayClient, create_gateway
    from mde_prompts.registry import PromptRegistry

    ledger = FileLedger(tmp_path / "ledger.jsonl")
    settings = Settings(_env_file=None, llm_mode="mock")  # type: ignore[call-arg]
    gw = create_gateway(settings, PromptRegistry(), ledger=ledger)
    gw.guard.reserve(["test:ready"], 100, purpose="probe")  # writes the ledger and its guard
    return InProcessGatewayClient(gw), ledger


async def test_readyz_checks_the_ledger_and_a_deleted_ledger_is_not_ready(tmp_path: Path) -> None:
    client_, ledger = real_ledger_state(tmp_path)
    state = await build_app_state(
        make_settings(tmp_path / "projects"),
        store=InMemoryStore(),
        fake_builder=True,
        gateway=client_,
    )
    try:
        async with make_client(state) as client:
            ok = await client.get("/readyz")
            assert ok.status_code == 200
            assert ok.json()["checks"] == {"database": "ok", "ledger": "ok"}
            ledger.path.unlink()  # the budget would silently reset: that must not look healthy
            bad = await client.get("/readyz")
            assert bad.status_code == 503
            assert bad.json() == {
                "status": "not_ready",
                "checks": {"database": "ok", "ledger": "failed"},
            }
            assert "ledger.jsonl" not in bad.text and "deleted" not in bad.text  # never the reason
    finally:
        await state.aclose()


async def test_a_ledger_check_that_raises_is_not_ready_and_a_slow_one_times_out(
    mem_client: httpx.AsyncClient, mem_state: AppState, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> str | None:
        raise OSError("disk on fire")

    mem_state.ledger_problem = boom
    resp = await mem_client.get("/readyz")
    assert resp.status_code == 503 and resp.json()["checks"]["ledger"] == "failed"
    assert "fire" not in resp.text

    release = threading.Event()

    def slow() -> str | None:
        release.wait(5)
        return None

    monkeypatch.setattr(routes, "LEDGER_CHECK_SECONDS", 0.05)
    mem_state.ledger_problem = slow
    try:
        resp = await mem_client.get("/readyz")
        assert resp.status_code == 503 and resp.json()["checks"]["ledger"] == "failed"
    finally:
        release.set()


async def test_an_http_gateway_has_no_ledger_of_ours_to_check(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        gateway_mode="http",
        gateway_url="http://gateway.invalid",
        gateway_service_token="svc-token-123456",
    )
    state = await build_app_state(settings, store=InMemoryStore(), fake_builder=True)
    try:
        assert state.ledger_problem is None
        async with make_client(state) as client:
            resp = await client.get("/readyz")
        assert resp.status_code == 200 and resp.json()["checks"] == {"database": "ok"}
    finally:
        await state.aclose()


async def test_readyz_never_shows_the_budget_to_an_unauthenticated_caller(
    mem_client: httpx.AsyncClient, mem_state: AppState
) -> None:
    hot = BudgetStatus(
        cap_micros=10,
        effective_cap_micros=9,
        spent_micros=9,
        reserved_micros=1,
        tripwire="refuse_95",
        live_calls=7,
    )
    mem_state.gateway = SimpleNamespace(budget_status=lambda: hot)  # type: ignore[assignment]
    resp = await mem_client.get("/readyz")  # no credentials at all
    assert resp.status_code == 200
    body = resp.text.lower()
    assert "tripwire" not in body and "refuse" not in body and "budget" not in body
    assert resp.json()["checks"].keys() == {"database", "ledger"}
    assert not any(ch.isdigit() for ch in resp.text)  # no spend, no cap, no count
    assert (await mem_client.get("/v1/budget")).status_code == 401  # the numbers need credentials
