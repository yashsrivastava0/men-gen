"""The Brief compile v2 through the real engine code (ADR 0016): per-group parallel calls, list slots EXTENDED with
cited and grounded elements, honest coverage, one failing section, no churn on a re-run, typical details and
corrections as assumptions. The model is scripted; everything else (service, slots, store rules) is real."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from mde_compass.calls.compile_brief.ledger import parse_ledger
from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain.common import Provenance
from mde_compass.domain.events import UserMessage
from mde_compass.domain.hashing import brief_hash
from mde_compass.domain.interview import Fact, FactEffect
from mde_compass.engine.compile_types import (
    DetailPlacement,
    SectionRequest,
    SectionResult,
    TypicalDetail,
    floor_result,
)
from mde_compass.engine.interview_compile import compile_ledger
from mde_compass.engine.ports import CompiledItem, NotUsedFact
from mde_compass.engine.service import Service, _Turn
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def planner_service(**context: int) -> Service:
    interview = dataclasses.replace(DEFAULT_CONFIG.interview, engine="planner")
    ctx = (
        dataclasses.replace(DEFAULT_CONFIG.context, **context)
        if context
        else DEFAULT_CONFIG.context
    )
    config = dataclasses.replace(DEFAULT_CONFIG, interview=interview, context=ctx)
    return build_service(memory=True, clock=FakeClock(), compass_config=config)


def fact(
    n: int,
    fact_type: str,
    statement: str,
    *topics: str,
    origin: str = "stated",
    quote: str | None = None,
    **changes: Any,
) -> Fact:
    return Fact.model_validate(
        {
            "id": f"FCT-{n:03d}",
            "type": fact_type,
            "statement": statement,
            "topics": list(topics),
            "origin": origin,
            "quote": statement if quote is None else quote,
            "confidence": 0.9,
            "created_at": NOW,
            "updated_at": NOW,
            **changes,
        }
    )


class Script:
    """A scripted ``SectionCompiler``: the default answer places every fact it was given (kind from the kinds file)."""

    def __init__(
        self,
        fill: Callable[[SectionRequest, list[str]], SectionResult] | None = None,
        typical: list[TypicalDetail] | None = None,
        fail: tuple[str, ...] = (),
    ) -> None:
        self.fill = fill
        self.typical = typical or []
        self.fail = fail
        self.calls: list[SectionRequest] = []

    async def compile_section(self, ctx: Any, request: SectionRequest) -> SectionResult:
        self.calls.append(request)
        if request.task == "typical":
            return SectionResult(typical=list(self.typical))
        if request.group in self.fail:
            raise RuntimeError("the section call failed")
        ids = [f.fact_id for f in parse_ledger(request.ledger)]
        if self.fill is not None:
            return self.fill(request, ids)
        return placed(ids)

    def fills(self) -> list[SectionRequest]:
        return [c for c in self.calls if c.task == "fill"]


def placed(ids: list[str], **changes: Any) -> SectionResult:
    return SectionResult(
        placements=[DetailPlacement(fact_id=i, topic="t", consumers=["blueprint"]) for i in ids],
        **changes,
    )


def item(path: str, value: Any, *fact_ids: str, confidence: float = 0.9) -> CompiledItem:
    return CompiledItem(path=path, value=value, fact_ids=list(fact_ids), confidence=confidence)


async def make_turn(svc: Service, facts: list[Fact]) -> _Turn:
    conv = await Conversation.start(svc)
    turn = await svc._load("t1", conv.session_id, UserMessage(event_id="evt_test", text="x"))
    for f in facts:
        turn.memory.add_fact(f)
    return turn


def install(svc: Service, script: Script) -> Script:
    assert svc.ports is not None
    object.__setattr__(svc.ports, "brief_compiler", script)
    return script


def user_slot(turn: _Turn, path: str, value: Any, fact_id: str, state: str = "confirmed") -> None:
    turn.slots.write(
        path,
        value,
        state=state,  # type: ignore[arg-type]
        provenance=Provenance(type="user_tap", ref=fact_id, quote="said so", date=NOW),
        turn_id="evt_test",
    )


FEATURE_FACTS = [
    fact(1, "requirement", "Customers can browse the menu", "menu"),
    fact(2, "requirement", "Customers can track their order live on a map", "tracking"),
    fact(3, "requirement", "The owner gets a weekly sales report", "reports"),
    fact(4, "rule", "Orders close at 9 pm", "order_cutoff"),
]


def features_of(turn: _Turn) -> list[str]:
    return [f.name for f in turn.brief.scope]


# -------------------------------------------------------------------------------------------- extension
async def test_a_user_list_is_extended_with_cited_grounded_elements_and_nothing_is_overwritten() -> (
    None
):
    svc = planner_service()
    turn = await make_turn(svc, FEATURE_FACTS)
    user_slot(turn, "scope.features", [{"name": "Browse the menu"}], "FCT-001")
    user_slot(turn, "idea.summary", "A lunch ordering app", "FCT-001")

    def fill(request: SectionRequest, ids: list[str]) -> SectionResult:
        if "scope" not in request.sections:
            return placed(ids)
        return placed(
            ids,
            items=[
                item("idea.summary", "Something else entirely", "FCT-002")
            ],  # a user value: refused
            additions=[
                item(
                    "scope.features",
                    {"name": "Live order tracking", "intent": "See the rider on a map"},
                    "FCT-002",
                ),
                item(
                    "scope.features", {"name": "browse the MENU"}, "FCT-001"
                ),  # already there: not twice
                item(
                    "scope.features",
                    {"name": "Weekly sales report", "intent": "A report for the owner"},
                    "FCT-003",
                ),
            ],
        )

    script = install(svc, Script(fill))
    await compile_ledger(svc.interview, turn)
    assert features_of(turn) == ["Browse the menu", "Live order tracking", "Weekly sales report"]
    new = {f.name: f for f in turn.brief.scope}["Live order tracking"]
    assert new.intent == "See the rider on a map" and new.id.startswith("FTR-")
    assert (
        new.tier == "Locked" and new.status == "volunteered"
    )  # verified facts: the evidence rule holds
    assert turn.brief.idea["summary"].value == "A lunch ordering app"  # never overwritten
    assert any(c.task == "typical" for c in script.calls)


async def test_an_invented_element_is_dropped_but_its_fact_is_kept_as_a_detail() -> None:
    svc = planner_service()
    turn = await make_turn(svc, FEATURE_FACTS)
    user_slot(turn, "scope.features", [{"name": "Browse the menu"}], "FCT-001")

    def fill(request: SectionRequest, ids: list[str]) -> SectionResult:
        extra = (
            [
                item("scope.features", {"name": "Drone parachute delivery"}, "FCT-002")
            ]  # shares no word with FCT-002
            if "scope" in request.sections
            else []
        )
        return SectionResult(
            additions=extra,
            placements=[
                DetailPlacement(fact_id=i, topic="t", kind="flow")
                for i in ids
                if request.group == "place"
            ],
        )

    script = install(svc, Script(fill))
    await compile_ledger(svc.interview, turn)
    assert features_of(turn) == ["Browse the menu"]
    assert "FCT-002" in {d.id for d in turn.brief.domain_facts}  # nothing the person said is lost
    assert any(c.group == "place" for c in script.calls)  # it was asked for once more


async def test_elements_resting_on_unverified_facts_never_extend_a_list_the_person_filled() -> None:
    guess = fact(
        5,
        "requirement",
        "Customers pay with cash on delivery",
        "payments",
        origin="assumption",
        quote="",
    )
    svc = planner_service()
    turn = await make_turn(svc, [FEATURE_FACTS[0], guess])
    user_slot(turn, "scope.features", [{"name": "Browse the menu"}], "FCT-001")

    def fill(request: SectionRequest, ids: list[str]) -> SectionResult:
        return placed(
            ids, additions=[item("scope.features", {"name": "Cash on delivery"}, "FCT-005")]
        )

    install(svc, Script(fill))
    await compile_ledger(svc.interview, turn)
    assert features_of(turn) == ["Browse the menu"]
    assert "FCT-005" in {d.id for d in turn.brief.domain_facts}


async def test_an_empty_list_gets_unverified_elements_only_as_a_guess_never_locked() -> None:
    guess = fact(
        5,
        "requirement",
        "Customers pay with cash on delivery",
        "payments",
        origin="assumption",
        quote="",
    )
    svc = planner_service()
    turn = await make_turn(svc, [guess])

    def fill(request: SectionRequest, ids: list[str]) -> SectionResult:
        return placed(
            ids, additions=[item("scope.features", {"name": "Cash on delivery"}, "FCT-005")]
        )

    install(svc, Script(fill))
    await compile_ledger(svc.interview, turn)
    rec = turn.slots.get("scope.features")
    assert rec is not None and rec.status == "assumed" and rec.tier != "Locked"
    assert [f.tier for f in turn.brief.scope] == ["Default"]


async def test_a_guess_is_replaced_by_verified_evidence_but_a_user_value_never_is() -> None:
    svc = planner_service()
    turn = await make_turn(svc, [fact(1, "preference", "Playful, bright colours", "look")])
    turn.slots.write(
        "look.tone_words",
        ["professional"],
        state="assumed",
        provenance=Provenance(type="pattern", ref="", date=NOW),
        turn_id="evt_test",
    )
    user_slot(turn, "audience.segments", ["students"], "FCT-001")

    def fill(request: SectionRequest, ids: list[str]) -> SectionResult:
        return placed(
            ids,
            items=[
                item("look.tone_words", ["friendly_playful"], "FCT-001"),
                item("audience.segments", ["office workers"], "FCT-001"),
            ],
        )

    install(svc, Script(fill))
    await compile_ledger(svc.interview, turn)
    assert turn.slots.get("look.tone_words").value == ["friendly_playful"]  # type: ignore[union-attr]
    assert turn.slots.get("look.tone_words").status == "volunteered"  # type: ignore[union-attr]
    assert turn.slots.get("audience.segments").value == ["students"]  # type: ignore[union-attr]


# --------------------------------------------------------------------------------- coverage, honestly
async def test_a_fact_the_model_forgets_is_asked_again_then_placed_and_counted_not_hidden() -> None:
    svc = planner_service()
    turn = await make_turn(svc, FEATURE_FACTS)
    script = install(
        svc, Script(lambda request, ids: SectionResult())
    )  # the model mentions nothing
    await compile_ledger(svc.interview, turn)
    block = turn.brief.meta.interview
    assert block is not None and block.facts_unreferenced == 4 and block.coverage == 0.0
    assert {d.id for d in turn.brief.domain_facts} == {"FCT-001", "FCT-002", "FCT-003", "FCT-004"}
    assert all(d.kind for d in turn.brief.domain_facts)  # placed by code, typed by the kinds file
    assert sum(1 for c in script.calls if c.group == "place") >= 1  # one more ask before giving up
    assert turn.brief.meta.compile_digests.keys() <= {
        "typical"
    }  # nothing is cached: it retries next time


async def test_the_second_ask_can_still_place_what_was_forgotten() -> None:
    svc = planner_service()
    turn = await make_turn(svc, FEATURE_FACTS)

    def fill(request: SectionRequest, ids: list[str]) -> SectionResult:
        return placed(ids) if request.group == "place" else SectionResult()

    install(svc, Script(fill))
    await compile_ledger(svc.interview, turn)
    block = turn.brief.meta.interview
    assert block is not None and block.facts_unreferenced == 0 and block.coverage == 1.0


async def test_one_failing_section_is_the_flagged_floor_and_the_others_are_unaffected() -> None:
    facts = [*FEATURE_FACTS, fact(5, "constraint", "Works on cheap phones", "devices")]
    svc = planner_service()
    turn = await make_turn(svc, facts)

    def fill(request: SectionRequest, ids: list[str]) -> SectionResult:
        return placed(
            ids,
            items=[item("constraints.must_use", ["Android"], *ids[:1])]
            if "constraints" in request.sections
            else [],
        )

    script = install(svc, Script(fill, fail=("people_and_scope",)))
    await compile_ledger(svc.interview, turn)
    block = turn.brief.meta.interview
    assert block is not None and block.fallback_count >= 1  # flagged and counted
    rows = {d.id: d for d in turn.brief.domain_facts}
    assert {"FCT-001", "FCT-002", "FCT-003", "FCT-004"} <= set(
        rows
    )  # the failed section's facts: the floor
    assert all(rows[i].consumers == ["all"] for i in ("FCT-001", "FCT-004"))
    assert turn.brief.constraints["must_use"].value == ["Android"]  # the other section was applied
    failed_key = next(
        k for k in ("fill:people_and_scope:0",) if k not in turn.brief.meta.compile_digests
    )
    assert failed_key and any(
        k.startswith("fill:experience") for k in turn.brief.meta.compile_digests
    )
    # next time only the failed section is asked again
    before = len(script.calls)
    script.fail = ()
    await compile_ledger(svc.interview, turn)
    again = [c.group for c in script.calls[before:] if c.task == "fill"]
    assert again == ["people_and_scope"]


# ------------------------------------------------------------------------------------------ no churn
async def test_compiling_twice_with_the_same_ledger_gives_the_same_brief_hash_and_no_second_call() -> (
    None
):
    svc = planner_service()
    facts = [
        *FEATURE_FACTS,
        fact(10, "preference", "Bright, cheerful colours", "look"),
        fact(11, "goal", "We want fewer phone orders", "goals"),
    ]
    turn = await make_turn(svc, facts)
    user_slot(turn, "scope.features", [{"name": "Browse the menu"}], "FCT-001")
    typical = [
        TypicalDetail(claim="Customers get a receipt", reason="r", kind="screen", what_changes="w")
    ]

    def fill(request: SectionRequest, ids: list[str]) -> SectionResult:
        return placed(
            ids,
            additions=[item("scope.features", {"name": "Live order tracking"}, "FCT-002")]
            if "scope" in request.sections
            else [],
        )

    script = install(svc, Script(fill, typical=typical))
    await compile_ledger(svc.interview, turn)
    first_hash = brief_hash(turn.brief)
    first_calls = len(script.calls)
    assert {c.group for c in script.fills()} == {
        "idea",
        "people_and_scope",
        "experience_and_systems",
    }
    assert first_calls == 4  # three sections and the typical call
    await compile_ledger(svc.interview, turn)
    assert len(script.calls) == first_calls  # every section and the typical call were cached
    assert brief_hash(turn.brief) == first_hash
    # a new fact changes only its own section (and the typical call, which reads the whole ledger)
    turn.memory.add_fact(fact(9, "rule", "Refunds within 7 days", "refunds"))
    await compile_ledger(svc.interview, turn)
    assert [c.group for c in script.calls[first_calls:]] == ["people_and_scope", "typical"]


async def test_a_superseded_fact_makes_its_section_run_again_and_only_that_one() -> None:
    svc = planner_service()
    turn = await make_turn(svc, FEATURE_FACTS)
    script = install(svc, Script())
    await compile_ledger(svc.interview, turn)
    keys = dict(turn.brief.meta.compile_digests)
    old = turn.memory.fact("FCT-004")
    assert old is not None
    turn.memory.update_fact(old.model_copy(update={"status": "superseded"}))
    before = len(script.calls)
    await compile_ledger(svc.interview, turn)
    assert any(c.task == "fill" for c in script.calls[before:])
    assert turn.brief.meta.compile_digests != keys


# ------------------------------------------------------------------------------- typical and corrections
async def test_typical_details_become_assumptions_with_no_slot_never_locked_and_survive_a_rebuild() -> (
    None
):
    svc = planner_service()
    turn = await make_turn(svc, FEATURE_FACTS)
    typical = [
        TypicalDetail(
            claim="Customers get a receipt after paying", reason="", kind="screen", what_changes=""
        ),
        TypicalDetail(
            claim="Card details are never stored by the app",
            reason="r",
            kind="compliance",
            risky=True,
            what_changes="w",
        ),
        TypicalDetail(
            claim="Customers can browse the menu", kind="screen"
        ),  # already a fact and a feature name
    ]
    install(svc, Script(typical=typical))
    user_slot(turn, "scope.features", [{"name": "Browse the menu"}], "FCT-001")
    await compile_ledger(svc.interview, turn)
    mine = [a for a in turn.brief.assumptions if a.basis == "typical"]
    assert [a.claim for a in mine] == [
        "Customers get a receipt after paying",
        "Card details are never stored by the app",
    ]
    assert all(a.slot_id is None and a.tier != "Locked" and a.decision == "proposed" for a in mine)
    assert [a.impact for a in mine] == ["low", "high"]
    assert (
        mine[0].what_changes and mine[0].reason
    )  # filled from the strings when the model gave none
    svc._rebuild(turn)  # sync_assumptions keeps the slot-less ones
    assert [a.id for a in turn.brief.assumptions if a.basis == "typical"] == [a.id for a in mine]


async def test_a_decided_typical_detail_is_kept_and_a_rejected_one_is_not_proposed_again() -> None:
    svc = planner_service()
    turn = await make_turn(svc, FEATURE_FACTS)
    typical = [TypicalDetail(claim="Customers get a receipt after paying", kind="screen")]
    script = install(svc, Script(typical=typical))
    await compile_ledger(svc.interview, turn)
    asm = next(a for a in turn.brief.assumptions if a.basis == "typical")
    turn.brief.assumptions = [
        a.model_copy(update={"decision": "rejected"}) if a.id == asm.id else a
        for a in turn.brief.assumptions
    ]
    turn.memory.add_fact(
        fact(9, "rule", "Refunds within 7 days", "refunds")
    )  # the ledger changed: typical runs again
    await compile_ledger(svc.interview, turn)
    again = [a for a in turn.brief.assumptions if a.basis == "typical"]
    assert [(a.id, a.decision) for a in again] == [
        (asm.id, "rejected")
    ]  # not proposed a second time
    assert sum(1 for c in script.calls if c.task == "typical") == 2


async def test_a_replaced_fact_is_shown_as_a_correction_the_person_decides() -> None:
    svc = planner_service()
    first = fact(1, "number", "Delivery costs 20 rupees", "pricing", status="superseded")
    second = fact(
        2,
        "number",
        "Delivery costs 30 rupees",
        "pricing",
        supersedes=["FCT-001"],
        effects=[FactEffect(slot_id="idea.business_model", value_json='"fee"')],
    )
    turn = await make_turn(svc, [first, second])
    install(svc, Script())
    await compile_ledger(svc.interview, turn)
    fixes = [a for a in turn.brief.assumptions if a.basis == "correction"]
    assert len(fixes) == 1
    assert (
        fixes[0].claim
        == "We used “Delivery costs 30 rupees”; you first said “Delivery costs 20 rupees”."
    )
    assert fixes[0].slot_id is None and fixes[0].what_changes
    assert (
        fixes[0].impact == "high"
    )  # idea.business_model has impact 4: the person must decide at Gate 1
    from mde_compass.engine.assumptions import review

    assert fixes[0].id in review(turn.brief).unreviewed_high_ids


async def test_the_basic_port_still_works_for_a_compiler_without_sections() -> None:
    svc = planner_service()
    turn = await make_turn(svc, FEATURE_FACTS)
    assert svc.ports is not None
    real = svc.ports.brief_compiler.compile_brief  # type: ignore[attr-defined]
    seen: list[int] = []

    async def basic(ctx: Any, request: Any) -> Any:
        seen.append(request.ledger.count("FCT-"))
        return await real(ctx, request)

    object.__setattr__(
        svc.ports, "brief_compiler", type("Basic", (), {"compile_brief": staticmethod(basic)})()
    )
    await compile_ledger(svc.interview, turn)
    assert seen and {d.id for d in turn.brief.domain_facts} >= {"FCT-001", "FCT-004"}


async def test_the_floor_helper_is_flagged_and_types_every_fact() -> None:
    result = floor_result(FEATURE_FACTS)
    assert result.used_fallback and [p.fact_id for p in result.placements] == [
        "FCT-001",
        "FCT-002",
        "FCT-003",
        "FCT-004",
    ]
    assert {p.fact_id: p.kind for p in result.placements}["FCT-004"] == "rule"
    assert NotUsedFact(
        fact_id="FCT-001", reason="x"
    )  # keeps the import used for the not_used test below


async def test_not_used_facts_are_marked_with_their_reason() -> None:
    svc = planner_service()
    turn = await make_turn(svc, [*FEATURE_FACTS, fact(6, "context", "Okay thanks", "chat")])

    def fill(request: SectionRequest, ids: list[str]) -> SectionResult:
        keep = [i for i in ids if i != "FCT-006"]
        return SectionResult(
            placements=[DetailPlacement(fact_id=i, topic="t") for i in keep],
            not_used=[NotUsedFact(fact_id="FCT-006", reason="thanks")] if "FCT-006" in ids else [],
        )

    install(svc, Script(fill))
    await compile_ledger(svc.interview, turn)
    gone = turn.memory.fact("FCT-006")
    assert gone is not None and gone.status == "not_used" and gone.not_used_reason == "thanks"
    assert "FCT-006" not in {d.id for d in turn.brief.domain_facts}


@pytest.mark.parametrize("size", [2, 3])
async def test_big_groups_are_cut_into_batches_that_run_in_parallel(size: int) -> None:
    svc = planner_service(compile_chunk_facts=size)
    facts = [
        fact(i, "requirement", f"Feature number {i} for customers", "menu") for i in range(1, 8)
    ]
    turn = await make_turn(svc, facts)
    script = install(svc, Script())
    await compile_ledger(svc.interview, turn)
    per_call = [len(parse_ledger(c.ledger)) for c in script.fills()]
    assert max(per_call) <= size and sum(per_call) >= 7
    assert {c.group for c in script.fills()} >= {"people_and_scope"}


# ------------------------------------------------------------------------------ Locked after a pivot
async def test_a_locked_value_citing_a_superseded_fact_is_downgraded_by_the_compile() -> None:
    svc = planner_service()
    old = fact(1, "context", "A tiffin service in Pune", "idea", status="superseded")
    new = fact(2, "context", "A clinic booking app in Indore", "idea")
    turn = await make_turn(svc, [old, new])
    user_slot(turn, "idea.summary", "A tiffin service in Pune", "FCT-001", state="volunteered")
    assert turn.slots.get("idea.summary").tier == "Locked"  # type: ignore[union-attr]
    install(svc, Script())  # the compile writes nothing: the downgrade alone must show
    await compile_ledger(svc.interview, turn)
    rec = turn.slots.get("idea.summary")
    assert rec is not None and (rec.tier, rec.status) == ("Default", "assumed")
    item = turn.brief.idea["summary"]
    assert item.tier == "Default" and item.status == "assumed"
    assert any(
        a.slot_id == "idea.summary" for a in turn.brief.assumptions
    )  # the person sees it as an assumption


async def test_the_downgraded_guess_is_replaced_by_verified_evidence_in_the_same_compile() -> None:
    svc = planner_service()
    old = fact(1, "context", "A tiffin service in Pune", "idea", status="superseded")
    new = fact(2, "context", "A clinic booking app in Indore", "idea")
    turn = await make_turn(svc, [old, new])
    user_slot(turn, "idea.summary", "A tiffin service in Pune", "FCT-001", state="volunteered")
    install(
        svc,
        Script(
            lambda request, ids: placed(
                ids, items=[item("idea.summary", "A clinic booking app in Indore", "FCT-002")]
            )
        ),
    )
    await compile_ledger(svc.interview, turn)
    assert turn.brief.idea["summary"].value == "A clinic booking app in Indore"
    assert turn.brief.idea["summary"].tier == "Locked"
