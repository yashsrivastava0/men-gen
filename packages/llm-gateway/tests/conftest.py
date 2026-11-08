"""Shared fixtures for the gateway tests (all offline, deterministic)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mde_core.clock import FakeClock
from mde_llm_gateway.budget import BudgetConfig, BudgetGuard
from mde_llm_gateway.ledger import FileLedger, Ledger, MemoryLedger
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.testing import GuardFactory, budget_config_for_tests


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def pricing() -> Pricing:
    return Pricing.load()


@pytest.fixture
def make_guard(clock: FakeClock, pricing: Pricing, tmp_path: Path) -> GuardFactory:
    """Build a guard. ``cap_usd`` is the hard cap; ``ledger`` is ``"memory"`` or ``"file"``."""

    def _make(
        cap_usd: str = "6.00",
        *,
        ledger: Ledger | str = "memory",
        bills: bool = True,
        config: BudgetConfig | None = None,
    ) -> BudgetGuard:
        if isinstance(ledger, str):
            ledger = (
                FileLedger(tmp_path / "ledger.jsonl", clock=clock)
                if ledger == "file"
                else MemoryLedger(clock=clock)
            )
        cfg = config or budget_config_for_tests(cap_usd)
        return BudgetGuard(cfg, pricing, ledger, bills=bills, clock=clock)

    return _make
