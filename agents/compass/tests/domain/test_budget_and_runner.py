"""The Compass budget clamp/guard and the call runner's fallback behavior."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from mde_compass.budget import CompassBudget
from mde_compass.calls.base import CallRunner, CompassCallSpec
from mde_compass.config import BudgetPolicy, load_budget_policy
from mde_core.errors import BudgetExceeded, ProviderError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "2.00"),
        ("5.00", "2.00"),
        ("1.25", "1.25"),
        ("-3", "2.00"),
        ("abc", "2.00"),
        ("NaN", "2.00"),
        ("0", "2.00"),
    ],
)
def test_live_cap_is_clamped(raw: str | None, expected: str) -> None:
    env = {} if raw is None else {"COMPASS_LIVE_BUDGET_USD": raw}
    assert load_budget_policy(env).cap_usd == Decimal(expected)


def test_effective_stop_and_per_session_cap_fail_closed() -> None:
    policy = BudgetPolicy()
    assert policy.effective_stop_micros == 1_900_000 and policy.per_session_micros == 250_000
    budget = CompassBudget(policy, mode="live")
    budget.check("ses_1", estimate_micros=100_000)
    budget.record("ses_1", 240_000)
    with pytest.raises(BudgetExceeded):
        budget.check("ses_1", estimate_micros=20_000)  # per-session 0.25 would be crossed
    budget.check("ses_2", estimate_micros=100_000)  # another session is fine
    budget.record("ses_2", 1_600_000)
    with pytest.raises(BudgetExceeded):
        budget.check("ses_3", estimate_micros=300_000)  # total would pass 1.90
    with pytest.raises(BudgetExceeded):
        budget.check("ses_3", estimate_micros=-1)  # fail closed on nonsense
    snap = budget.snapshot()
    assert snap["spent_usd"] == "1.8400" and snap["cap_usd"] == "2.00"
    budget.reconcile(2_000_000)
    assert budget.spent_micros == 2_000_000


class In(BaseModel):
    text: str


class Out(BaseModel):
    answer: str


def _spec(validators=()) -> CompassCallSpec[In, Out]:
    fake = SimpleNamespace(id="cmp.test", version=1)
    return CompassCallSpec(
        spec=fake,
        lane="writer",
        timeout_s=5,
        fallback=lambda i: Out(answer="fallback:" + i.text),
        validators=tuple(validators),
    )  # type: ignore[arg-type]


class FakeGateway:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result, self.error, self.calls = result, error, 0

    async def run(self, spec, inp, ctx):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _ok(answer: str = "model") -> SimpleNamespace:
    return SimpleNamespace(
        output=Out(answer=answer),
        usage=SimpleNamespace(cost_micros=1200, input_tokens=10, output_tokens=5),
        repaired=False,
        cache_hit=False,
    )


async def test_runner_returns_model_output_and_logs() -> None:
    logged: list = []

    async def sink(session_id, entry):
        logged.append((session_id, entry))

    budget = CompassBudget(mode="live")
    runner = CallRunner(FakeGateway(_ok()), guard=budget, sink=sink)  # type: ignore[arg-type]
    res = await runner.run(_spec(), In(text="x"), session_id="ses_1", tenant_id="t")
    assert res.output.answer == "model" and not res.used_fallback and res.cost_micros == 1200
    assert budget.spent_micros == 1200 and logged[0][1].outcome == "ok"


@pytest.mark.parametrize("failure", [ProviderError("boom"), TimeoutError()])
async def test_runner_falls_back_on_gateway_failure(failure: Exception) -> None:
    runner = CallRunner(FakeGateway(error=failure))  # type: ignore[arg-type]
    res = await runner.run(_spec(), In(text="x"), session_id="ses_1", tenant_id="t")
    assert res.used_fallback and res.output.answer == "fallback:x"


async def test_runner_falls_back_when_a_validator_rejects() -> None:
    runner = CallRunner(FakeGateway(_ok("bad")))  # type: ignore[arg-type]
    res = await runner.run(
        _spec([lambda i, o: ["answer is bad"]]), In(text="y"), session_id="ses_1", tenant_id="t"
    )
    assert res.used_fallback and "answer is bad" in res.reason


async def test_runner_never_calls_the_gateway_when_the_budget_is_spent() -> None:
    budget = CompassBudget(BudgetPolicy(per_session_cap_usd=Decimal("0.000001")), mode="live")
    budget.record("ses_1", 5)
    gateway = FakeGateway(_ok())
    runner = CallRunner(gateway, guard=budget)  # type: ignore[arg-type]
    res = await runner.run(_spec(), In(text="z"), session_id="ses_1", tenant_id="t")
    assert res.used_fallback and gateway.calls == 0
