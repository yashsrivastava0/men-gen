import asyncio
import multiprocessing as mp

import pytest

from mde_spec_agent.spend import (
    Prices,
    SpendGuard,
    SpendRecord,
    SpendRefused,
    StopRequested,
    worst_case_micros,
)
from mde_spec_agent.spend.testing import spend_worker

PRICES = Prices("0.10", "0.50", 100_000, "5.0")
CAP = 5_000_000


def rec(tmp_path, cap=CAP, trip=4_500_000):
    return SpendRecord(tmp_path / "spend-record.json", cap_micros=cap, tripwire_micros=trip)


def test_worst_case_is_integer_and_has_the_long_prompt_surcharge():
    assert worst_case_micros(1000, 1000, PRICES) == 100 + 500  # 0.10/M * 1000 tok = 100 micros
    below = worst_case_micros(100_000, 1000, PRICES)
    above = worst_case_micros(100_001, 1000, PRICES)
    assert above > below * 4  # the whole request is billed at 5x
    assert isinstance(above, int)


def test_reserve_refuses_before_the_cap_is_passed(tmp_path):
    r = rec(tmp_path, cap=1000, trip=900)
    a = r.reserve(600, label="a")
    with pytest.raises(SpendRefused) as e:
        r.reserve(500, label="b")
    assert e.value.reason == "budget_cap"
    r.commit(a, 100)
    r.reserve(500, label="b")  # fits now: 100 spent + 500 reserved


def test_commit_books_actual_and_failure_books_worst_case(tmp_path):
    r = rec(tmp_path)
    a = r.reserve(1000, label="a")
    r.commit(a, 400)
    b = r.reserve(1000, label="b")
    r.book_worst_case(b)
    assert r.spent_micros() == 1400


def test_tripwire_blocks_new_waves_but_not_the_state_in_flight(tmp_path):
    r = rec(tmp_path, cap=5000, trip=4500)
    a = r.reserve(4600, label="big")
    r.commit(a, 4600)
    with pytest.raises(SpendRefused) as e:
        r.can_start_wave()
    assert e.value.reason == "budget_tripwire"


def test_orphan_reservation_counts_as_spent(tmp_path):
    r = rec(tmp_path)
    r.reserve(700, label="x", pid=999_999)
    assert r.recover_orphans(is_alive=lambda pid: False) == 1
    assert r.spent_micros() == 700 and r.snapshot()["reserved_micros"] == 0


def test_corrupt_record_never_reads_as_zero(tmp_path):
    r = rec(tmp_path)
    r.path.write_text("{not json")
    with pytest.raises(SpendRefused):
        r.snapshot()


def test_scripted_gateway_that_tries_to_overspend_is_refused(tmp_path):
    """The no-bypass test of doc 19 5: a scripted gateway keeps asking; the guard stops it at the cap."""
    r = rec(tmp_path, cap=2_000_000, trip=1_800_000)
    guard = SpendGuard(r, PRICES)
    calls = {"n": 0}

    async def scripted():
        calls["n"] += 1
        return 500_000  # each call really costs 0.50 USD

    async def go():
        for _ in range(20):
            await guard.call(
                scripted,
                cost_of=lambda c: c,
                input_tokens=1000,
                max_output_tokens=1_000_000,
                label="x",
            )

    # a call really costs 0.50 USD; its worst case is just above that; the 4th reservation passes the 2.00 cap
    with pytest.raises(SpendRefused):
        asyncio.run(go())
    assert r.spent_micros() == 1_500_000 and calls["n"] == 3


def test_failed_call_books_worst_case_and_stop_is_honoured(tmp_path):
    r = rec(tmp_path)
    guard = SpendGuard(r, PRICES)

    async def boom():
        raise TimeoutError

    with pytest.raises(TimeoutError):
        asyncio.run(
            guard.call(
                boom, cost_of=lambda c: 0, input_tokens=1000, max_output_tokens=1000, label="t"
            )
        )
    assert r.spent_micros() == worst_case_micros(1000, 1000, PRICES)
    guard.request_stop()
    with pytest.raises(StopRequested):
        guard.start_wave()


def test_two_processes_cannot_both_spend_the_last_money(tmp_path):
    path = tmp_path / "spend-record.json"
    ps = [
        mp.get_context("spawn").Process(target=spend_worker, args=(str(path), 200))
        for _ in range(3)
    ]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
    r = SpendRecord(path, cap_micros=1000, tripwire_micros=900)
    assert r.spent_micros() == 1000  # exactly the cap, never above
