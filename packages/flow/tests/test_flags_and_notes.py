"""What the flow tells the user about content it did not trust (GAP-1) and what it hands the builder
from Discover (``BuildRequest.screen_notes``, BUG-3). Run over both stores."""

from __future__ import annotations

import json

import pytest

from mde_contracts.engines import DiscoveryState
from mde_contracts.events import Notice
from mde_flow import bookkeeping, messages
from mde_flow.testing import FakeReportingResearch
from mde_flow.testing.kit import Driver, FlowKit, make_flow_kit


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run every test over each store of the matrix (conftest: memory, Postgres)."""


async def amend_extra(kit: FlowKit, d: Driver, **extra: str) -> None:
    async with kit.store.begin(d.who.tenant_id) as uow:
        rec = await uow.sessions.get(d.session_id, for_update=True)
        assert rec is not None
        state = rec.discovery.model_copy(update={"extra": {**rec.discovery.extra, **extra}})
        await uow.sessions.save(
            rec.model_copy(update={"discovery": state}), expected_version=rec.version
        )


def flagged(d: Driver) -> list[Notice]:
    return [e for e in d.of_type(Notice) if e.code == messages.N_CONTENT_FLAGGED]


# ----------------------------------------------------------------------------- GAP-1


async def test_an_injection_research_ignored_is_noted_once_in_the_event_log() -> None:
    kit = make_flow_kit()
    kit.service.research = FakeReportingResearch(injections=3)
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    (note,) = flagged(d)  # one notice for the run, not one per ignored page
    assert note.level == "info"
    assert note.text == messages.CONTENT_FLAGGED
    assert "ignored some instructions" in note.text
    assert (await d.snapshot()).state == "PLAN_REVIEW"  # the plan went on normally
    stored = await kit.service.list_events(d.who, d.session_id)  # and it is in the replayable log
    assert [e.code for e in stored if isinstance(e, Notice)].count(messages.N_CONTENT_FLAGGED) == 1


async def test_nothing_is_said_when_research_ignored_nothing() -> None:
    kit = make_flow_kit()
    kit.service.research = FakeReportingResearch(injections=0)
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert flagged(d) == []


async def test_an_engine_that_cannot_report_adds_no_notice() -> None:
    kit = make_flow_kit()  # the plain port: ``run`` only
    d = await kit.driver()
    await d.to_gate1()
    await d.approve_gate1()
    await d.run_job()
    assert flagged(d) == []


async def test_the_notice_never_repeats_what_was_ignored() -> None:
    assert "system prompt" not in messages.CONTENT_FLAGGED.lower()
    assert not any(ch.isdigit() for ch in messages.CONTENT_FLAGGED)


# ----------------------------------------------------------------------------- BUG-3


async def test_the_builder_is_given_the_screen_details_discover_kept() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    await amend_extra(kit, d, screen_notes="a gift message field on the cart; a bigger logo")
    await d.approve_gate1()
    await d.run_job()
    assert kit.builder.calls[0].screen_notes == {
        "note-1": "a gift message field on the cart",
        "note-2": "a bigger logo",
    }


async def test_the_builder_gets_an_empty_map_when_there_are_no_details() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate2()
    assert kit.builder.calls[0].screen_notes == {}


async def test_details_keyed_by_screen_are_passed_through() -> None:
    kit = make_flow_kit()
    d = await kit.driver()
    await d.to_gate1()
    notes = {"SCR-003": "a gift message field", "/cart": "show the delivery date"}
    await amend_extra(kit, d, screen_notes=json.dumps(notes))
    await d.approve_gate1()
    await d.run_job()
    assert kit.builder.calls[0].screen_notes == notes


# ----------------------------------------------------------------------------- bookkeeping


def state(**extra: str) -> DiscoveryState:
    return DiscoveryState(extra=dict(extra))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", {}),
        ("   ", {}),
        ("one", {"note-1": "one"}),
        ("one; two ;; three", {"note-1": "one", "note-2": "two", "note-3": "three"}),
        ('{"SCR-001": "x", "/b": "  "}', {"SCR-001": "x"}),
        ("{not json; really", {"note-1": "{not json", "note-2": "really"}),
        ('["a", "b"]', {"note-1": '["a", "b"]'}),
    ],
)
def test_screen_notes(raw: str, expected: dict[str, str]) -> None:
    extra = {"screen_notes": raw} if raw else {}
    assert bookkeeping.screen_notes(state(**extra)) == expected


def test_extra_keys_are_set_and_removed_without_touching_the_rest() -> None:
    s = state(keep="1", **{bookkeeping.ANSWER_TRIES: "2"})
    s2 = bookkeeping.with_extra(
        s, **{bookkeeping.ANSWER_TRIES: None, bookkeeping.ANSWER_OFFER: "1"}
    )
    assert s2.extra == {"keep": "1", bookkeeping.ANSWER_OFFER: "1"}
    assert s.extra[bookkeeping.ANSWER_TRIES] == "2"  # the original is untouched
    assert bookkeeping.without_flow_keys(s2, "nothing") is s2  # a no-op returns the same state
    assert bookkeeping.without_flow_keys(s2, bookkeeping.ANSWER_OFFER).extra == {"keep": "1"}


@pytest.mark.parametrize(("raw", "expected"), [("3", 3), ("-2", 0), ("x", 0), ("", 0)])
def test_counters_are_forgiving(raw: str, expected: int) -> None:
    assert bookkeeping.count({"k": raw}, "k") == expected
    assert bookkeeping.count({}, "k") == 0


def test_a_hold_round_trips_and_an_unreadable_one_is_still_a_hold() -> None:
    s = bookkeeping.set_hold(state(), bookkeeping.BudgetHold(kind="answer", answers=["pay.model"]))
    hold = bookkeeping.read_hold(s)
    assert hold is not None and hold.kind == "answer" and hold.answers == ["pay.model"]
    assert bookkeeping.read_hold(bookkeeping.clear_hold(s)) is None
    broken = state(**{bookkeeping.BUDGET_HOLD: "{nope"})
    unreadable = bookkeeping.read_hold(broken)
    assert unreadable is not None and unreadable.kind == "dropped"
