"""Document claims to facts (ADR 0016): typed and topic-tagged facts, claims that need confirming can never be Locked,
duplicates merge, conflicts are kept and decided by the person, names are cut at a word, one confirmation card, and a
dense requirements table loses nothing."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from mde_compass.calls.merge_claims.adapter import to_input
from mde_compass.calls.merge_claims.assemble import assemble
from mde_compass.calls.merge_claims.fallback import fallback as merge_fallback
from mde_compass.calls.read_chunk import fallback as read_fallback
from mde_compass.calls.read_chunk.rules import load_rules
from mde_compass.calls.read_chunk.schema import ReadChunkInput
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.cards import Card
from mde_compass.domain.claims import Claim, Statement
from mde_compass.domain.interview import DOCUMENT_LOCK_CONFIDENCE, Fact
from mde_compass.engine.assumptions import merge_derived
from mde_compass.engine.document_facts import (
    apply_confirmation,
    claim_name,
    confirm_card,
    conflict_assumptions,
    cut_at_word,
    facts_from_claims,
    needing_confirmation,
    pointer_topics,
    quote_score,
)
from mde_compass.engine.document_flow import plan_from_claims
from mde_compass.intake import Section, chunk_sections

CATALOG = load_catalog()
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SLOTS = frozenset(CATALOG.slots)


def statement(
    text: str,
    kind: str = "requirement",
    *,
    quote: str | None = None,
    conf: float = 0.9,
    pointer: str = "page 2",
) -> Statement:
    return Statement(
        type=kind,
        text=text,
        quote=text if quote is None else quote,
        pointer=pointer,
        confidence=conf,
    )  # type: ignore[arg-type]


def claim(n: int, st: Statement, status: str = "used", **changes: Any) -> Claim:
    return Claim.model_validate(
        {"id": f"CLM-{n:03d}", "source_id": "SRC-001", "statement": st, "status": status, **changes}
    )


def build(*claims: Claim, start: int = 1) -> Any:
    return facts_from_claims(
        claims, source_id="SRC-001", start_number=start, now=NOW, slot_ids=SLOTS
    )


# ------------------------------------------------------------------------------------ types and topics
def test_a_claim_becomes_a_typed_topic_tagged_document_fact_with_its_quote_and_pointer() -> None:
    got = build(
        claim(1, statement("Riders must accept orders", pointer="heading: Rider app")),
        claim(
            2,
            statement(
                "Refunds are processed within 7 days", "rule_or_number", pointer="sheet Rules!A1:C9"
            ),
        ),
        claim(3, statement("Orders close at nine in the evening", "rule_or_number")),
        claim(4, statement("We run a lunch box service in Pune", "business_context")),
        claim(5, statement("Riders", "user_or_role"), slot_hint="roles.list"),
        start=7,
    )
    by = {f.id: f for f in got.facts}
    assert list(by) == ["FCT-007", "FCT-008", "FCT-009", "FCT-010", "FCT-011"]
    first = by["FCT-007"]
    assert (first.type, first.topics, first.origin, first.status) == (
        "requirement",
        ["requirements", "rider_app"],
        "document",
        "active",
    )
    assert first.source_ref == "SRC-001:heading: Rider app" and first.claim_id == "CLM-001"
    assert (
        first.quote == "Riders must accept orders"
        and not first.unverified_quote
        and first.consumers == ["all"]
    )
    assert (by["FCT-008"].type, by["FCT-008"].topics) == (
        "number",
        ["numbers", "rules"],
    )  # a digit makes it a number
    assert by["FCT-009"].type == "rule"  # no digit: a rule
    assert (by["FCT-010"].type, by["FCT-010"].topics) == ("context", ["business"])
    assert by["FCT-011"].type == "role" and by["FCT-011"].aspects == [
        "roles.list:*"
    ]  # a used claim answers its need
    assert all(f.created_at == NOW for f in got.facts)


def test_pointer_topics_read_headings_and_sheets_only() -> None:
    assert pointer_topics("heading: Payments & Refunds") == ["payments_refunds"]
    assert pointer_topics("sheet Roles!A1:D9; page 3") == ["roles"]
    assert pointer_topics("pages 3-9") == [] and pointer_topics("") == []
    assert pointer_topics("heading: " + "x" * 90) == ["x" * 40]


# --------------------------------------------------------------------------------------- confirmation
def test_a_claim_that_needs_confirming_is_a_low_confidence_fact_that_can_never_be_locked() -> None:
    got = build(
        claim(1, statement("Riders must accept orders", conf=0.9), "used"),
        claim(
            2, statement("Maybe offer a loyalty scheme", "preference", conf=0.9), "needs_confirming"
        ),
        claim(3, statement("Delivery fee is TBD", "open_issue", conf=0.3), "needs_confirming"),
        claim(
            4, statement("Orders can be cancelled", conf=0.6), "used"
        ),  # medium confidence, not yet confirmed
    )
    sure, maybe, tbd, medium = got.facts
    assert sure.confidence == 0.9 and sure.supports_locked()
    assert maybe.confidence < DOCUMENT_LOCK_CONFIDENCE and not maybe.supports_locked()
    assert tbd.confidence == 0.3 and not tbd.supports_locked()
    assert medium.confidence == 0.6 and not medium.supports_locked()
    assert got.needs_confirming == 3
    # the rule is only for documents: what the person said (or tapped) keeps its old behavior
    said = Fact(
        id="FCT-020", type="rule", statement="x", quote="x", confidence=0.2, origin="stated"
    )
    assert said.supports_locked()
    assert not said.model_copy(update={"origin": "document", "confidence": 0.69}).supports_locked()
    assert said.model_copy(update={"origin": "document", "confidence": 0.7}).supports_locked()


def test_one_card_asks_what_was_read_and_ticking_confirms() -> None:
    got = build(
        claim(1, statement("Maybe offer a loyalty scheme", "preference"), "needs_confirming"),
        claim(2, statement("Riders must accept orders"), "used"),
        claim(3, statement("Pay riders weekly", conf=0.5), "needs_confirming"),
    )
    card = confirm_card(got.facts, card_id="Q-031")
    assert isinstance(Card.model_validate(card.model_dump()), Card)
    assert (card.id, card.type, card.origin, card.need_ids, card.slots) == (
        "Q-031",
        "pick_many",
        "planner",
        [],
        [],
    )
    assert card.question == "We read these in your document: tick what is right."
    assert [o.id for o in card.options] == ["FCT-001", "FCT-003"]  # only what needs confirming
    assert all(
        not o.ticked and not o.recommended and o.basis == "your_document" for o in card.options
    )
    assert card.you_decide.enabled is False and card.custom_input.enabled
    hi = confirm_card(got.facts, language="hi")
    assert "दस्तावेज़" in hi.question
    # ticked = confirmed (0.9, now Lockable); unticked stays as it was
    changed = apply_confirmation(got.facts, ["FCT-001", "FCT-002", "FCT-404"], now=NOW)
    assert [(f.id, f.confidence) for f in changed] == [("FCT-001", 0.9)]
    assert (
        changed[0].supports_locked() and got.facts[0].confidence < DOCUMENT_LOCK_CONFIDENCE
    )  # a copy
    assert [f.id for f in needing_confirmation([*changed, got.facts[2]])] == ["FCT-003"]
    with pytest.raises(ValueError):
        confirm_card([got.facts[1]])


def test_the_card_holds_at_most_fifteen_options() -> None:
    many = [
        claim(i, statement(f"Possible idea number {i}", conf=0.4), "needs_confirming")
        for i in range(1, 30)
    ]
    assert len(confirm_card(build(*many).facts).options) == 15


# ------------------------------------------------------------------------------------------ duplicates
def test_duplicate_claims_merge_into_one_fact_keeping_the_better_quote() -> None:
    weak = statement("Riders must accept orders", quote="Riders ...", pointer="page 2")
    strong = statement(
        "Riders must accept orders",
        quote="Riders must accept orders within 2 minutes.",
        pointer="page 9",
    )
    got = build(
        claim(1, weak, "used"),
        claim(2, statement("Riders  MUST accept orders!", quote="x", conf=0.95), "duplicate"),
        claim(3, strong, "duplicate"),
    )
    (fact,) = got.facts
    assert got.merged == 2
    assert (
        fact.quote == "Riders must accept orders within 2 minutes." and fact.claim_id == "CLM-003"
    )
    assert (
        fact.source_ref == "SRC-001:page 9" and fact.confidence == 0.95
    )  # corroborated: the highest confidence
    assert quote_score(strong) > quote_score(weak)


def test_a_statement_repeated_only_as_claims_to_confirm_stays_below_the_bar() -> None:
    got = build(
        claim(1, statement("Maybe a loyalty scheme", "preference", conf=0.9), "needs_confirming"),
        claim(2, statement("maybe a loyalty scheme", "preference", conf=0.9), "duplicate"),
    )
    (fact,) = got.facts
    assert fact.confidence == 0.65 and not fact.supports_locked() and got.merged == 1


# ------------------------------------------------------------------------------------------- conflicts
def conflicting_pair() -> list[Claim]:
    statements = [
        statement("Refunds are allowed after delivery", "rule_or_number"),
        statement("Refunds are not allowed after delivery", "rule_or_number"),
        statement("Riders must accept orders"),
    ]
    merged = assemble(
        statements,
        merge_fallback(to_input(statements, "SRC-001", "en")),
        source_id="SRC-001",
        slot_ids=SLOTS,
    )
    return merged.claims


def test_conflicting_claims_are_kept_as_disputed_facts_and_decided_through_an_assumption() -> None:
    claims = conflicting_pair()
    assert [c.status for c in claims] == ["conflicting", "conflicting", "used"]
    got = build(*claims)
    assert [(f.status, f.confidence >= 0) for f in got.facts] == [
        ("disputed", True),
        ("disputed", True),
        ("active", True),
    ]
    assert got.disputed == 2 and got.conflicts == [("CLM-001", "CLM-002")]
    assert all(f.quote for f in got.facts)  # nothing the document said is lost
    fresh = conflict_assumptions(claims, CATALOG, "en")
    assert len(fresh) == 1
    one = fresh[0]
    assert one.claim == (
        "Your document says both “Refunds are allowed after delivery” and "
        "“Refunds are not allowed after delivery”; we used neither until you decide."
    )
    assert (one.basis, one.slot_id, one.decision, one.impact) == (
        "document",
        None,
        "proposed",
        "low",
    )
    assert "दस्तावेज़" in conflict_assumptions(claims, CATALOG, "hi")[0].claim
    # it joins the Brief like the other slot-less assumptions, and survives a re-merge of the same basis
    brief = merge_derived(Brief(), fresh, ("document",))
    assert [a.id for a in brief.assumptions] == ["ASM-001"]


def test_a_conflict_on_an_important_slot_is_high_impact() -> None:
    a = claim(
        1,
        statement("Delivery only in Pune"),
        "conflicting",
        slot_hint="context.geo",
        conflicts_with=["CLM-002"],
    )
    b = claim(
        2,
        statement("Delivery only in Mumbai"),
        "conflicting",
        slot_hint="context.geo",
        conflicts_with=["CLM-001"],
    )
    assert [x.impact for x in conflict_assumptions([a, b], CATALOG, "en")] == ["high"]


# ---------------------------------------------------------------------------------------------- names
def test_names_are_the_models_short_name_else_a_word_boundary_cut_at_eighty_characters() -> None:
    long = "The rider application must allow every verified rider to accept, reject and reassign delivery orders in real time"
    assert len(claim_name(statement(long))) <= 80
    assert (
        claim_name(statement(long))
        == "The rider application must allow every verified rider to accept, reject and"
    )
    assert not claim_name(statement(long)).endswith(" ") and long.startswith(
        claim_name(statement(long))
    )
    named = SimpleNamespace(text=long, name="  Order   handling ")
    assert claim_name(named) == "Order handling"  # type: ignore[arg-type]
    assert claim_name(SimpleNamespace(text="Short one.", name="")) == "Short one"  # type: ignore[arg-type]
    assert cut_at_word("a" * 100, 10) == "a" * 10  # one long word is cut hard, never emptied
    assert cut_at_word("one two three", 7) == "one two"


def test_features_and_roles_are_no_longer_cut_to_eight_or_four_words() -> None:
    text = "Customers can reorder any of their last ten orders with one tap from home"
    plan = plan_from_claims(
        [
            claim(1, statement(text), "used"),
            claim(2, statement("Delivery riders working night shifts", "user_or_role"), "used"),
        ]
    )
    by_slot = {w.slot_id: w.value for w in plan.writes}
    assert by_slot["scope.features"][0]["name"] == text  # type: ignore[index]
    assert by_slot["roles.list"] == [{"name": "Delivery riders working night shifts"}]


# -------------------------------------------------------------------------------------- dense tables
def test_a_dense_requirements_table_loses_no_row() -> None:
    rows = [
        f"REQ-{i:03d} | The app must support capability number {i} for dispatchers | High"
        for i in range(1, 151)
    ]
    section = Section(
        pointer="sheet Reqs!A1:C151",
        text="\n".join(rows),
        table=True,
        header="ID | Requirement | Priority",
        sheet="Reqs",
        last_col="C",
        row_numbers=tuple(range(2, 152)),
        header_row=1,
    )
    chunks = chunk_sections(
        [section], source_id="SRC-001", target_tokens=(1500, 2500), max_table_rows=40
    )
    assert len(chunks) == 4 and all(
        c.text.startswith("ID | Requirement | Priority") for c in chunks
    )
    cap = load_rules().max_statements
    statements: list[Statement] = []
    for c in chunks:
        out = read_fallback.fallback(
            ReadChunkInput(chunk_text=c.text, pointer=c.pointer, language="en", idea_hint="")
        )
        assert len(out.statements) <= cap
        statements += [
            statement(s.text, s.type, quote=s.quote, pointer=c.pointer) for s in out.statements
        ]
    assert len(statements) == 150  # one statement per row: none lost, none merged
    facts = build(*[claim(i, st, "used") for i, st in enumerate(statements, start=1)])
    assert len(facts.facts) == 150 and {f.type for f in facts.facts} == {"requirement"}
    assert all("reqs" in f.topics for f in facts.facts)  # the sheet name types the detail
