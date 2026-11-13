"""A list slot rewritten many times must stay small (found in a live Thorough run: 30 features reached 500 KB because every
rewrite nested the history of the one before, and every model call then carried 190K tokens of it)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.interview import Fact
from mde_compass.engine.brief_builder import apply_slots, slots_from_brief
from mde_compass.engine.fact_projector import project
from mde_compass.engine.slots import SlotStore, plain
from mde_core.clock import FakeClock

CAT = load_catalog()
NOW = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)


def test_plain_drops_the_evidence_of_every_item_at_any_depth() -> None:
    value = [
        {
            "name": "a",
            "_meta": {"history": [{"value": [{"name": "x", "_meta": {"tier": "Locked"}}]}]},
        },
        "text",
    ]
    assert plain(value) == [{"name": "a"}, "text"]
    assert plain({"city": "Pune", "_meta": 1}) == {"city": "Pune"}
    assert plain(None) is None and plain(3) == 3


def fact(n: int, name: str) -> Fact:
    return Fact.model_validate(
        {
            "id": f"FCT-{n:03d}",
            "type": "requirement",
            "statement": f"We need {name}",
            "quote": f"We need {name}",
            "origin": "stated",
            "created_at": NOW + timedelta(seconds=n),
            "effects": [
                {
                    "slot_id": "scope.features",
                    "mode": "add",
                    "value_json": json.dumps([{"name": name, "intent": f"intent {n}"}]),
                }
            ],
        }
    )


def test_a_feature_list_written_forty_times_through_the_brief_round_trip_stays_small() -> None:
    clock = FakeClock()
    store = SlotStore(CAT, clock=clock)
    brief = Brief()
    for n in range(1, 41):
        pref = chr(97 + n // 26) + chr(97 + n % 26)
        project(fact(n, f"{pref}{pref} service"), store, CAT, turn_id=f"evt_{n}", now=NOW)
        brief = apply_slots(brief, store, CAT)  # what the service does after each turn
        store.load(slots_from_brief(brief, CAT, clock))  # and after every patch or undo
    assert len(brief.scope) == 40
    size = len(json.dumps([f.model_dump(mode="json") for f in brief.scope]))
    assert size < 400_000, f"40 features took {size} bytes: the history is nesting again"
    one = max(len(json.dumps(f.model_dump(mode="json"))) for f in brief.scope)
    assert one < 20_000, f"one feature record took {one} bytes"
    slot = store.get("scope.features")
    assert slot is not None
    assert "_meta" not in json.dumps([h.value for h in slot.history]), (
        "a history snapshot holds no nested evidence"
    )
