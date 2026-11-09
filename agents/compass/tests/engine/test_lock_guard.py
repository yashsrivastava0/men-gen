"""No Locked value rests on a fact that is gone (ADR 0016): the post-pass, the slot finder for ``reproject``, and the
compile running it before it writes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.common import Provenance
from mde_compass.domain.interview import Fact
from mde_compass.engine import fact_projector
from mde_compass.engine.lock_guard import downgrade_unsupported_locks, slots_citing
from mde_compass.engine.slots import SlotStore
from mde_core.clock import FakeClock

CATALOG = load_catalog()
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def fact(n: int, **changes: Any) -> Fact:
    base: dict[str, Any] = {
        "id": f"FCT-{n:03d}",
        "type": "context",
        "statement": f"statement {n}",
        "quote": f"quote {n}",
        "origin": "stated",
        "confidence": 0.9,
        "created_at": NOW,
    }
    base.update(changes)
    return Fact.model_validate(base)


def store() -> SlotStore:
    return SlotStore(CATALOG, clock=FakeClock())


def write(slots: SlotStore, slot_id: str, value: Any, ref: str, type_: str = "user_text") -> None:
    res = slots.write(
        slot_id,
        value,
        state="volunteered",
        provenance=Provenance(type=type_, ref=ref, quote="said so", date=NOW),  # type: ignore[arg-type]
        turn_id="evt_1",
    )
    assert res.applied


def test_a_locked_slot_citing_a_superseded_fact_becomes_default_and_is_reported() -> None:
    slots = store()
    write(slots, "idea.summary", "A lunch app", "FCT-001")
    write(slots, "idea.category", "food", "FCT-002")
    write(
        slots, "context.geo", {"city": "Pune"}, "evt_9"
    )  # the person typed it: it cites a turn, not a fact
    assert slots.get("idea.summary").tier == "Locked"  # type: ignore[union-attr]
    facts = [fact(1, status="superseded"), fact(2)]
    changes = downgrade_unsupported_locks(slots, facts)
    assert [(c.slot_id, c.unsupported, c.records) for c in changes] == [
        ("idea.summary", ("FCT-001",), 0)
    ]
    gone = slots.get("idea.summary")
    assert gone is not None and (gone.tier, gone.status) == ("Default", "assumed")
    assert gone.value == "A lunch app" and gone.history[-1].reason == "evidence no longer active"
    assert slots.get("idea.category").tier == "Locked"  # still supported  # type: ignore[union-attr]
    assert slots.get("context.geo").tier == "Locked"  # not a fact citation: untouched  # type: ignore[union-attr]


def test_unverified_disputed_and_unknown_facts_do_not_support_a_lock_either() -> None:
    slots = store()
    for n, slot in enumerate(
        ("idea.summary", "idea.category", "idea.subcategory", "idea.business_model"), start=1
    ):
        write(slots, slot, f"v{n}", f"FCT-{n:03d}")
    facts = [
        fact(1, status="disputed"),
        fact(2, unverified_quote=True),
        fact(3, status="not_used", not_used_reason="x"),
    ]
    changes = downgrade_unsupported_locks(slots, facts)  # FCT-004 is not in the ledger at all
    assert {c.slot_id for c in changes} == {
        "idea.summary",
        "idea.category",
        "idea.subcategory",
        "idea.business_model",
    }
    assert all(slots.get(c.slot_id).tier == "Default" for c in changes)  # type: ignore[union-attr]


def test_a_document_claim_below_the_bar_does_not_support_a_lock() -> None:
    slots = store()
    write(slots, "idea.summary", "x", "FCT-001", "document")
    changes = downgrade_unsupported_locks(slots, [fact(1, origin="document", confidence=0.6)])
    assert [c.slot_id for c in changes] == ["idea.summary"]
    ok = store()
    write(ok, "idea.summary", "x", "FCT-001", "document")
    assert downgrade_unsupported_locks(ok, [fact(1, origin="document", confidence=0.9)]) == []


def test_a_list_slot_downgrades_only_the_records_that_lost_their_evidence() -> None:
    slots = store()
    keep = {
        "name": "Browse the menu",
        "_meta": {
            "tier": "Locked",
            "status": "volunteered",
            "confidence": 0.9,
            "history": [],
            "provenance": [{"type": "user_text", "ref": "FCT-001", "quote": "q"}],
        },
    }
    lose = {
        "name": "Old idea",
        "_meta": {
            "tier": "Locked",
            "status": "volunteered",
            "confidence": 0.9,
            "history": [],
            "provenance": [{"type": "user_text", "ref": "FCT-002", "quote": "q"}],
        },
    }
    write(slots, "scope.features", [keep, lose], "FCT-001")
    changes = downgrade_unsupported_locks(slots, [fact(1), fact(2, status="superseded")])
    assert [(c.slot_id, c.unsupported, c.records) for c in changes] == [
        ("scope.features", ("FCT-002",), 1)
    ]
    value = slots.get("scope.features").value  # type: ignore[union-attr]
    assert value[0]["_meta"]["tier"] == "Locked" and value[1]["_meta"]["tier"] == "Default"  # type: ignore[index]
    assert value[1]["_meta"]["status"] == "assumed"  # type: ignore[index]
    assert (
        slots.get("scope.features").tier == "Locked"
    )  # the slot's own evidence (FCT-001) still holds  # type: ignore[union-attr]


def test_slots_citing_finds_the_slots_reproject_never_sees() -> None:
    slots = store()
    write(slots, "idea.summary", "x", "FCT-001")  # written by the compile: no effect on any fact
    write(slots, "idea.category", "y", "FCT-002")
    meta = {
        "tier": "Locked",
        "status": "volunteered",
        "confidence": 0.9,
        "history": [],
        "provenance": [{"type": "user_text", "ref": "FCT-003"}],
    }
    write(slots, "scope.features", [{"name": "A", "_meta": meta}], "FCT-004")
    assert slots_citing(slots, {"FCT-001"}) == ["idea.summary"]
    assert slots_citing(slots, {"FCT-003"}) == ["scope.features"]  # a record's own evidence counts
    assert slots_citing(slots, {"FCT-001", "FCT-002", "FCT-404"}) == [
        "idea.summary",
        "idea.category",
    ]
    assert slots_citing(slots, set()) == []
    # reproject on those slots clears a value whose only support is gone, as it does for projected values
    result = fact_projector.reproject(
        slots_citing(slots, {"FCT-001"}),
        [fact(1, status="superseded")],
        slots,
        CATALOG,
        turn_id=None,
        now=NOW,
    )
    assert result.cleared == ["idea.summary"] and not slots.filled("idea.summary")
