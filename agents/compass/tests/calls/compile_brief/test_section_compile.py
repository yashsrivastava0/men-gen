"""``cmp.compile_brief`` v2 (ADR 0016): one call per group of facts. Elements to add to list targets, typed and linked
placements, typical details, honest coverage (a fact the model forgot is UNPLACED, never silently completed), and the
flagged floor when a call fails."""

from __future__ import annotations

import json

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.compile_brief.adapter import (
    CompileAdapter,
    build_section_input,
    element_name,
    existing_index,
    to_section_result,
)
from mde_compass.calls.compile_brief.schema import (
    AdditionWire,
    CompileOutput,
    ItemWire,
    NotUsedWire,
    PlacementWire,
    TypicalWire,
)
from mde_compass.calls.testing import ScriptedGateway
from mde_compass.catalog.loader import load_catalog
from mde_compass.engine.compile_types import SectionRequest, SectionResult
from mde_compass.engine.ports import TurnContext
from mde_core.errors import ProviderError

CATALOG = load_catalog()
CTX = TurnContext(session_id="ses_1", tenant_id="t1")
KEY = "AKIAIOSFODNN7EXAMPLE"  # secret-scan: allow (a published fake key)

LEDGER = "\n".join(
    [
        "# bookings",
        "FCT-001 [requirement|bookings] Patients can move or cancel a visit (from Q-01) | conf 0.90 | stated",
        "FCT-002 [rule|cancellation] Free cancellation up to 2 hours before (from Q-02) | conf 0.90 | stated",
        "# doctors",
        "FCT-003 [role|doctors] Doctors see the day's patient list on a phone (from Q-03) | conf 0.90 | stated",
        "FCT-004 [context|chat] Okay thanks (from evt_4) | conf 0.50 | stated",
    ]
)
EXISTING = (
    "ROL-001 | roles | Patient\nFTR-001 | scope | Book a visit\nFTR-002 | scope | Day schedule"
)


def request(**changes: object) -> SectionRequest:
    base: dict[str, object] = {
        "language": "en",
        "group": "people_and_scope",
        "sections": ["roles", "scope"],
        "ledger": LEDGER,
        "project": "idea.summary: A booking app for a family clinic",
        "existing": EXISTING,
        "slot_state": {"roles.list": "volunteered", "scope.features": "assumed"},
    }
    base.update(changes)
    return SectionRequest.model_validate(base)


def wire_output(**changes: object) -> CompileOutput:
    base: dict[str, object] = {
        "items": [],
        "additions": [
            AdditionWire(
                path="roles.list",
                value_json='{"name": "Doctor", "goal": "See the day\'s patient list"}',
                fact_ids=["FCT-003"],
                confidence=0.85,
            ),
            AdditionWire(
                path="scope.features",
                value_json='{"name": "Move or cancel a visit", "intent": "Change a booked visit"}',
                fact_ids=["FCT-001"],
                confidence=0.8,
            ),
        ],
        "placements": [
            PlacementWire(
                fact_id="FCT-001",
                topic="bookings",
                consumers=["blueprint", "screens"],
                kind="flow",
                refs=["Move or cancel a visit", "FTR-001", "FTR-999", "Nothing like it"],
            ),
            PlacementWire(
                fact_id="FCT-002",
                topic="cancellation",
                consumers=["blueprint", "qa"],
                kind="Rule",
                refs=[],
            ),
            PlacementWire(
                fact_id="FCT-003",
                topic="doctors",
                consumers=["screens"],
                kind="bogus",
                refs=["Doctor"],
            ),
        ],
        "not_used": [NotUsedWire(fact_id="FCT-004", reason="thanks")],
        "summary": "",
        "pitch": "",
        "typical": [],
    }
    base.update(changes)
    return CompileOutput.model_validate(base)


# --------------------------------------------------------------------------------------------- the input
def test_the_section_input_carries_targets_with_their_state_the_existing_items_and_the_kinds() -> (
    None
):
    wire = build_section_input(CATALOG, request())
    assert wire.task == "fill" and wire.sections == ["roles", "scope"]
    state = {t.path: t.filled for t in wire.targets}
    assert state["roles.list"] == "user" and state["scope.features"] == "assumed"
    assert state["scope.must_not"] == ""
    assert wire.existing == [EXISTING] and wire.project == [
        "idea.summary: A booking app for a family clinic"
    ]
    assert {k.key for k in wire.kinds} >= {"rule", "screen", "flow", "acceptance", "other"}
    assert all(k.description for k in wire.kinds)


def test_the_typical_task_has_no_targets_and_the_none_section_has_no_targets_either() -> None:
    typical = build_section_input(CATALOG, request(task="typical", sections=[]))
    assert typical.task == "typical" and typical.targets == [] and typical.sections == ["typical"]
    placing = build_section_input(CATALOG, request(sections=["_none"]))
    assert placing.targets == [] and placing.task == "fill"


def test_secrets_never_reach_the_section_wire() -> None:
    wire = build_section_input(
        CATALOG,
        request(
            project=f"key {KEY}",
            existing=f"FTR-001 | scope | {KEY}",
            ledger=f"{LEDGER}\nFCT-009 [rule] {KEY}",
        ),
    )
    assert KEY not in wire.model_dump_json()


def test_existing_lines_give_ids_and_names() -> None:
    ids, names = existing_index([EXISTING, "junk line", "ROL-002 | roles | Receptionist"])
    assert ids == {"ROL-001", "FTR-001", "FTR-002", "ROL-002"}
    assert names["patient"] == "ROL-001" and names["book a visit"] == "FTR-001"


# -------------------------------------------------------------------------------------- the conversion
def test_additions_are_valid_elements_of_list_targets_citing_real_facts() -> None:
    wire = build_section_input(CATALOG, request())
    bad = wire_output(
        additions=[
            AdditionWire(
                path="roles.list",
                value_json='{"name": "Doctor"}',
                fact_ids=["FCT-003"],
                confidence=0.9,
            ),
            AdditionWire(
                path="roles.list",
                value_json='{"name": "doctor"}',
                fact_ids=["FCT-003"],
                confidence=0.9,
            ),  # repeat
            AdditionWire(
                path="roles.list",
                value_json='{"name": "Nurse"}',
                fact_ids=["FCT-404"],
                confidence=0.9,
            ),  # no real fact
            AdditionWire(
                path="roles.list", value_json='{"nope": "x"}', fact_ids=["FCT-003"], confidence=0.9
            ),  # unknown key
            AdditionWire(
                path="roles.list", value_json='{"goal": "x"}', fact_ids=["FCT-003"], confidence=0.9
            ),  # no name
            AdditionWire(
                path="roles.list", value_json="{", fact_ids=["FCT-003"], confidence=0.9
            ),  # not JSON
            AdditionWire(
                path="idea.summary", value_json='"x"', fact_ids=["FCT-003"], confidence=0.9
            ),  # not a target
            AdditionWire(
                path="scope.must_not", value_json='"No cash"', fact_ids=["FCT-002"], confidence=2.0
            ),  # a text list
            AdditionWire(
                path="scope.features", value_json='"Reports"', fact_ids=["FCT-001"], confidence=0.9
            ),  # a string for objects
        ]
    )
    got = to_section_result(CATALOG, wire, bad)
    assert [(a.path, element_name(a.value)) for a in got.additions] == [
        ("roles.list", "Doctor"),
        ("scope.must_not", "No cash"),
    ]
    assert got.additions[1].confidence == 1.0  # clamped


def test_placements_get_a_valid_kind_and_only_real_refs() -> None:
    wire = build_section_input(CATALOG, request())
    got = to_section_result(CATALOG, wire, wire_output())
    by_id = {p.fact_id: p for p in got.placements}
    assert by_id["FCT-001"].kind == "flow"
    assert by_id["FCT-002"].kind == "rule"  # "Rule" is normalized
    assert by_id["FCT-003"].kind == "rule"  # "bogus": derived from the type (a role fact)
    # a ref is an existing id, or the name of an element this answer adds; anything else is dropped
    assert by_id["FCT-001"].refs == ["Move or cancel a visit", "FTR-001"]
    assert by_id["FCT-003"].refs == ["Doctor"]
    assert by_id["FCT-001"].consumers == ["blueprint", "screens"]


def test_a_fact_the_model_forgot_is_unplaced_not_silently_placed() -> None:
    wire = build_section_input(CATALOG, request())
    got = to_section_result(CATALOG, wire, wire_output(placements=[], not_used=[]))
    # FCT-001 and FCT-003 are cited by additions; FCT-002 and FCT-004 are mentioned nowhere
    assert got.unplaced == ["FCT-002", "FCT-004"] and got.placements == []


def test_a_fact_that_is_placed_and_called_not_used_is_only_placed() -> None:
    wire = build_section_input(CATALOG, request())
    got = to_section_result(
        CATALOG,
        wire,
        wire_output(
            not_used=[
                NotUsedWire(fact_id="FCT-002", reason="x"),
                NotUsedWire(fact_id="FCT-004", reason="  "),
            ]
        ),
    )
    assert got.not_used == [] and got.unplaced == ["FCT-004"]  # a blank reason is no reason


def test_typical_details_are_capped_deduplicated_and_never_invent_numbers() -> None:
    wire = build_section_input(
        CATALOG,
        request(
            task="typical",
            sections=[],
            assumptions=["Patients get a reminder"],
            ledger="FCT-001 [rule|x] Visits last 15 minutes (from Q-1)",
        ),
    )
    many = [
        TypicalWire(
            claim=f"A detail number {'x' * i} here",
            reason="r",
            kind="screen",
            risky=False,
            what_changes="w",
        )
        for i in range(20)
    ]
    out = wire_output(
        typical=[
            TypicalWire(
                claim="Patients get a reminder",
                reason="",
                kind="flow",
                risky=False,
                what_changes="",
            ),  # already assumed
            TypicalWire(
                claim="A limit of 50 visits a day",
                reason="r",
                kind="rule",
                risky=False,
                what_changes="w",
            ),  # invented number
            TypicalWire(
                claim="Visits last 15 minutes",
                reason="r",
                kind="rule",
                risky=False,
                what_changes="w",
            ),  # number is from the facts
            TypicalWire(claim="  ", reason="r", kind="rule", risky=False, what_changes="w"),
            TypicalWire(
                claim="visits last 15 minutes",
                reason="r",
                kind="rule",
                risky=False,
                what_changes="w",
            ),  # repeat
            TypicalWire(
                claim="Consent is asked first",
                reason="r",
                kind="nonsense",
                risky=True,
                what_changes="w",
            ),
            *many,
        ]
    )
    got = to_section_result(CATALOG, wire, out)
    claims = [t.claim for t in got.typical]
    assert claims[:2] == ["Visits last 15 minutes", "Consent is asked first"]
    assert len(got.typical) == 12
    assert (
        got.typical[1].kind == "context" and got.typical[1].risky
    )  # an unknown kind falls to the default
    assert got.items == got.additions == got.placements == [] and got.unplaced == []


# ------------------------------------------------------------------------------------------ the adapter
async def test_a_successful_section_call_returns_the_v2_result() -> None:
    gateway = ScriptedGateway(wire_output())
    got = await CompileAdapter(CallRunner(gateway), CATALOG).compile_section(CTX, request())
    assert not got.used_fallback and len(got.additions) == 2 and got.unplaced == []
    assert [p.fact_id for p in got.placements] == ["FCT-001", "FCT-002", "FCT-003"]
    sent = gateway.calls[0][1]
    assert json.loads(sent.model_dump_json())["existing"] == [EXISTING]  # type: ignore[attr-defined]


async def test_no_facts_means_no_call() -> None:
    gateway = ScriptedGateway()
    got = await CompileAdapter(CallRunner(gateway), CATALOG).compile_section(
        CTX, request(ledger="", task="fill")
    )
    assert got == SectionResult() and gateway.calls == []


async def test_a_failed_call_is_the_flagged_floor_for_that_section_only() -> None:
    adapter = CompileAdapter(CallRunner(ScriptedGateway(ProviderError("boom"))), CATALOG)
    got = await adapter.compile_section(CTX, request())
    assert got.used_fallback and got.unplaced == [] and got.items == got.additions == []
    assert [p.fact_id for p in got.placements] == ["FCT-001", "FCT-002", "FCT-003", "FCT-004"]
    assert all(p.consumers == ["all"] and p.kind for p in got.placements)
    assert {p.fact_id: p.kind for p in got.placements}["FCT-002"] == "rule"


async def test_a_structurally_bad_section_answer_falls_back_to_the_floor() -> None:
    bad = wire_output(
        additions=[
            AdditionWire(
                path="idea.summary", value_json='"x"', fact_ids=["FCT-001"], confidence=0.5
            )
        ]
    )
    got = await CompileAdapter(CallRunner(ScriptedGateway(bad)), CATALOG).compile_section(
        CTX, request()
    )
    assert got.used_fallback and got.additions == [] and len(got.placements) == 4


async def test_the_floor_of_the_typical_task_proposes_nothing() -> None:
    adapter = CompileAdapter(CallRunner(ScriptedGateway(ProviderError("boom"))), CATALOG)
    got = await adapter.compile_section(CTX, request(task="typical", sections=[]))
    assert got.used_fallback and got.typical == [] and got.placements == []


async def test_the_basic_port_still_completes_coverage_for_old_callers() -> None:
    from mde_compass.engine.ports import CompileRequest

    adapter = CompileAdapter(
        CallRunner(ScriptedGateway(wire_output(placements=[], not_used=[]))), CATALOG
    )
    got = await adapter.compile_brief(CTX, CompileRequest(language="en", ledger=LEDGER))
    assert {p.fact_id for p in got.placements} >= {"FCT-002", "FCT-004"}


def test_items_for_a_list_target_keep_their_whole_value() -> None:
    wire = build_section_input(CATALOG, request())
    out = wire_output(
        additions=[],
        items=[
            ItemWire(
                path="roles.list",
                value_json='[{"name": "Doctor"}, {"name": "Nurse"}]',
                fact_ids=["FCT-003"],
                confidence=0.8,
            )
        ],
    )
    got = to_section_result(CATALOG, wire, out)
    assert got.items[0].value == [{"name": "Doctor"}, {"name": "Nurse"}]


@pytest.mark.parametrize("value", [{"name": " A "}, "  A ", {"term": "A"}, {"label": "A"}])
def test_element_name_reads_the_name_like_key(value: object) -> None:
    assert element_name(value) == "A"
