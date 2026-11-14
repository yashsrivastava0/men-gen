"""The call runner under provider trouble: a 429 narrows the run (once per event), a clean window widens it again, and a
cut-off answer is recognised by the gateway's typed flag, not by its wording. A fake gateway stands in for the real one;
the edge suite (tests/edge: MD-03, MD-05, BG-09) runs the same behaviour through the real gateway."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from mde_contracts.gateway import CallSpec
from mde_core.clock import FakeClock
from mde_core.errors import CircuitOpen, GatewayError, ProviderError, StructuredOutputError
from mde_spec_agent.calls.base import (
    CallInput,
    CallRunner,
    SpecCallSpec,
    Truncated,
    is_truncation,
)
from mde_spec_agent.run.concurrency import Limiter, observe_provider_error, watching
from mde_spec_agent.run.orchestrator import Stage, StepDef, _stage_done
from mde_spec_agent.spend import Prices, SpendGuard, SpendRecord
from mde_spec_core.config import default_config

CFG = default_config()


class In(CallInput):
    text: str = "x"


class Out(BaseModel):
    text: str


SPEC = SpecCallSpec(
    spec=CallSpec(
        id="t.echo",
        version=1,
        input_model=In,
        output_model=Out,
        system_template="s.jinja",
        user_template="u.jinja",
        template_root=Path("."),
        route="fast",
        reasoning_effort="low",
        max_output_tokens=100,
        mock_handler="x:y",
    ),
    kind="ECHO",
    timeout_s=5.0,
    fallback=lambda inp: Out(text="fallback"),
)


class FakeGateway:
    """Plays a script: an exception is raised, anything else is returned as a result. `observe` makes the gateway
    report the errors it would have seen (and absorbed) to its `on_provider_error` hook, as the real one does."""

    def __init__(self, *script: Any, observe: list[ProviderError] | None = None) -> None:
        self.script = list(script)
        self.observe = observe or []
        self.calls = 0

    async def run(self, spec: Any, inp: Any, ctx: Any) -> Any:
        self.calls += 1
        for err in self.observe:
            observe_provider_error(err)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(output=item, usage=None, mode="mock", repaired=False)

    async def budget_status(self) -> Any:  # pragma: no cover - part of the port, unused here
        raise NotImplementedError


def runner(tmp_path: Path, gateway: FakeGateway, limiter: Limiter | None) -> CallRunner:
    record = SpendRecord(tmp_path / "rec.json", cap_micros=10**9, tripwire_micros=10**9 - 1)
    return CallRunner(
        gateway,  # type: ignore[arg-type]
        SpendGuard(record, Prices("0", "0")),
        run_id="run_t",
        limiter=limiter,
    )


def err429() -> ProviderError:
    return ProviderError("slow down", status=429, retryable=True)


# ----------------------------------------------------------------------------------------------- the limiter
def test_a_rate_limit_halves_once_per_event_not_once_per_response():
    lim = Limiter(8, CFG)
    lim.k = 8
    start = lim.epoch
    assert lim.note_rate_limit(since=start) is True and lim.k == 4
    # the retries of the same call and the calls that were already in flight belong to the same event
    assert lim.note_rate_limit(since=start) is False and lim.k == 4
    # a call that started after the halving and still gets a 429 is a new event
    assert lim.note_rate_limit(since=lim.epoch) is True and lim.k == 2
    # never below the configured minimum
    for _ in range(5):
        lim.note_rate_limit()
    assert lim.k == CFG.concurrency.min


async def test_one_clean_window_earns_one_worker_up_to_the_computed_width():
    clock = FakeClock()
    lim = Limiter(3, CFG, clock=clock)  # starts at min(initial, 3)
    lim.k = 1
    await lim.call_ok()
    assert lim.k == 1  # no window has passed yet
    clock.advance(CFG.concurrency.clean_window_seconds - 1)
    await lim.call_ok()
    assert lim.k == 1
    clock.advance(1)
    await lim.call_ok()
    assert lim.k == 2  # one window, one step (additive increase)
    await lim.call_ok()
    assert lim.k == 2  # the window restarts after an increase
    for _ in range(5):
        clock.advance(CFG.concurrency.clean_window_seconds)
        await lim.call_ok()
    assert lim.k == 3  # never above the width computed from the provider's limits


async def test_a_rate_limit_restarts_the_clean_window():
    clock = FakeClock()
    lim = Limiter(8, CFG, clock=clock)
    lim.k = 8
    clock.advance(CFG.concurrency.clean_window_seconds - 1)
    lim.note_rate_limit()
    clock.advance(2)  # a window since the START but not since the 429
    await lim.call_ok()
    assert lim.k == 4


def test_the_observer_lowers_the_limiter_of_the_call_in_progress_and_only_for_429():
    a, b = Limiter(8, CFG), Limiter(8, CFG)
    a.k = b.k = 8
    observe_provider_error(err429())  # outside any call: nobody to tell
    assert (a.k, b.k) == (8, 8)
    with watching(a):
        observe_provider_error(ProviderError("boom", status=503, retryable=True))
        assert a.k == 8  # a 5xx is not a rate limit
        observe_provider_error(err429())
        assert a.k == 4
        with watching(b):  # another call: its own watch
            observe_provider_error(err429())
        assert (a.k, b.k) == (4, 4)
        observe_provider_error(err429())  # back in the first call: same event as its first 429
        assert a.k == 4
    observe_provider_error(err429())  # the watch ended with the call
    assert (a.k, b.k) == (4, 4)
    with watching(None):  # no limiter: a no-op, not an error
        observe_provider_error(err429())


# ------------------------------------------------------------------------------------- the runner uses it
async def test_a_429_that_the_gateway_absorbs_still_narrows_the_run(tmp_path):
    lim = Limiter(8, CFG)
    lim.k = 8
    gw = FakeGateway(Out(text="ok"), observe=[err429(), err429()])  # two 429s, then the answer
    res = await runner(tmp_path, gw, lim).run(SPEC, In())
    assert res.output.text == "ok" and not res.flagged  # the call succeeded ...
    assert lim.k == 4 and lim.epoch == 1  # ... and the run narrowed ONCE for the two responses


async def test_a_429_that_survives_the_retries_narrows_and_stops_the_run(tmp_path):
    lim = Limiter(8, CFG)
    lim.k = 8
    gw = FakeGateway(err429())  # no observer call: a remote gateway cannot call back
    with pytest.raises(CircuitOpen):
        await runner(tmp_path, gw, lim).run(SPEC, In())
    assert lim.k == 4


async def test_both_reports_of_the_same_surviving_429_are_one_halving(tmp_path):
    lim = Limiter(8, CFG)
    lim.k = 8
    gw = FakeGateway(err429(), observe=[err429()])  # the observer AND the raised error
    with pytest.raises(CircuitOpen):
        await runner(tmp_path, gw, lim).run(SPEC, In())
    assert lim.k == 4 and lim.epoch == 1


async def test_a_credit_stop_is_not_a_rate_limit(tmp_path):
    lim = Limiter(8, CFG)
    lim.k = 8
    gw = FakeGateway(ProviderError("no credit", status=402, retryable=False))
    with pytest.raises(CircuitOpen):
        await runner(tmp_path, gw, lim).run(SPEC, In())
    assert lim.k == 8


async def test_clean_calls_after_a_window_widen_the_run_again(tmp_path):
    clock = FakeClock()
    lim = Limiter(8, CFG, clock=clock)
    lim.k = 2
    r = runner(tmp_path, FakeGateway(Out(text="a"), Out(text="b")), lim)
    await r.run(SPEC, In())
    assert lim.k == 2
    clock.advance(CFG.concurrency.clean_window_seconds)
    await r.run(SPEC, In())
    assert lim.k == 3


async def test_a_runner_without_a_limiter_behaves_as_before(tmp_path):
    res = await runner(tmp_path, FakeGateway(Out(text="ok")), None).run(SPEC, In())
    assert res.output.text == "ok"
    with pytest.raises(CircuitOpen):
        await runner(tmp_path, FakeGateway(err429()), None).run(SPEC, In())


# ------------------------------------------------------------------------------------------- truncation
def cut_off(message: str = "spec.x: output still invalid after validation (1 problem(s))") -> Any:
    return StructuredOutputError(
        message,
        details={"errors": ["the response was cut off"], "repaired": False, "truncated": True},
    )


def test_the_typed_flag_is_enough_whatever_the_message_says():
    assert is_truncation(cut_off("a message with none of the usual words"))
    plain = StructuredOutputError("invalid", details={"truncated": False, "errors": []})
    assert not is_truncation(plain)
    assert not is_truncation(GatewayError("boom"))


def test_a_plain_error_that_says_truncated_is_still_honoured_as_the_second_check():
    assert is_truncation(ProviderError("output was truncated at the token limit"))


async def test_a_cut_off_answer_from_the_real_gateway_error_is_reported_as_truncated(tmp_path):
    with pytest.raises(Truncated):
        await runner(tmp_path, FakeGateway(cut_off()), None).run(SPEC, In(), node_ids=["N1"])


async def test_an_invalid_answer_is_not_mistaken_for_truncation(tmp_path):
    bad = StructuredOutputError("invalid", details={"truncated": False, "errors": ["x"]})
    res = await runner(tmp_path, FakeGateway(bad, bad), None).run(SPEC, In(), node_ids=["N1"])
    assert res.used_fallback and res.flagged and res.reasked


# ------------------------------------------------------------------------------------------- stages
def _stage(*names: str) -> Stage:
    async def noop(ctx: Any) -> None:  # pragma: no cover
        return None

    return Stage(tuple(StepDef(n, noop) for n in names))


def test_a_stage_is_done_under_its_own_name_or_when_every_step_was_done_alone():
    both = _stage("product", "design")
    assert not _stage_done(both, [])
    assert _stage_done(both, ["product+design"])
    assert _stage_done(
        both, ["product", "design"]
    )  # a step-by-step run, resumed with the grouped pipeline
    assert not _stage_done(both, ["product"])  # one of two: the stage still has work to do
    assert _stage_done(_stage("plan"), ["plan"])
