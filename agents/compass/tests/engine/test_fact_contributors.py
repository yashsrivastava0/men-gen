"""Enterprise: who said it travels with the fact into the slot store (BUG-22).

A second contributor's conflicting statement must mark the slot ``disputed`` (never an automatic merge); the same
person correcting themselves, or a session without contributors, replaces the value as before."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.interview import Fact
from mde_compass.engine.fact_projector import project
from mde_compass.engine.facts import contributor_of, with_contributor
from mde_compass.engine.slots import SlotStore
from mde_core.clock import FakeClock

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
CATALOG = load_catalog()
SLOT = "locales"  # brief path experience.locales (scenario SB-IV-07)


def mk(n: int, value: object, speaker: str | None = None) -> Fact:
    fact = Fact.model_validate(
        {
            "id": f"FCT-{n:03d}",
            "type": "requirement",
            "statement": f"the app locales are {value}",
            "quote": f"the app should be in {value}",
            "origin": "stated",
            "created_at": NOW + timedelta(seconds=n),
            "effects": [{"slot_id": SLOT, "value_json": json.dumps(value), "mode": "set"}],
        }
    )
    return with_contributor(fact, speaker)


def run(fact: Fact, slots: SlotStore) -> None:
    project(fact, slots, CATALOG, turn_id="evt_1", now=NOW)


def test_with_contributor_stamps_every_effect_and_contributor_of_reads_it() -> None:
    fact = mk(1, ["en"], "priya")
    assert contributor_of(fact) == "priya"
    assert all(e.contributor_id == "priya" for e in fact.effects)
    plain = mk(2, ["en"])
    assert contributor_of(plain) is None and plain.effects[0].contributor_id is None


def test_a_second_contributors_conflicting_statement_disputes_the_slot() -> None:
    slots = SlotStore(CATALOG, clock=FakeClock())
    run(mk(1, ["en"], "priya"), slots)
    run(mk(2, ["hi"], "rahul"), slots)
    rec = slots.get(SLOT)
    assert rec is not None
    assert rec.status == "disputed" and rec.tier != "Locked"
    assert rec.value == ["en"]  # the first value is kept; the other is a proposal, never merged


def test_the_same_contributor_correcting_themselves_replaces_the_value() -> None:
    slots = SlotStore(CATALOG, clock=FakeClock())
    run(mk(1, ["en"], "priya"), slots)
    run(mk(2, ["hi"], "priya"), slots)
    rec = slots.get(SLOT)
    assert rec is not None and rec.status != "disputed" and rec.value == ["hi"]


def test_without_contributors_a_new_value_replaces_the_old_one() -> None:
    slots = SlotStore(CATALOG, clock=FakeClock())
    run(mk(1, ["en"]), slots)
    run(mk(2, ["hi"]), slots)
    rec = slots.get(SLOT)
    assert rec is not None and rec.status != "disputed" and rec.value == ["hi"]
