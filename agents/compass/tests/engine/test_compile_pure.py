"""The pure parts of the Brief compile v2: routing, batching, digests, grounding, refs, merge, and the slot-less
assumptions (typical, correction, review) with their stable ids and decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import AssumptionRecord, Brief, FeatureRecord, RoleRecord
from mde_compass.domain.interview import Fact, FactEffect
from mde_compass.domain.slots import SlotRecord
from mde_compass.engine.assumptions import merge_derived, sync_assumptions
from mde_compass.engine.compile_apply import (
    derive_refs,
    grounded,
    merge_results,
    norm_name,
    resolve_refs,
)
from mde_compass.engine.compile_plan import (
    batches,
    digest_of,
    existing_lines,
    plan_calls,
    route,
    stale_keys,
    typical_digest,
)
from mde_compass.engine.compile_types import (
    DetailPlacement,
    ReviewFinding,
    SectionResult,
    TypicalDetail,
)
from mde_compass.engine.derived_assumptions import (
    corrections_from_facts,
    review_from_findings,
    snippet,
    typical_assumptions,
)
from mde_compass.engine.ports import NotUsedFact

CATALOG = load_catalog()
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def fact(n: int, fact_type: str, statement: str, *topics: str, **changes: Any) -> Fact:
    return Fact.model_validate(
        {
            "id": f"FCT-{n:03d}",
            "type": fact_type,
            "statement": statement,
            "topics": list(topics),
            "quote": statement,
            "created_at": NOW,
            "updated_at": NOW,
            **changes,
        }
    )


# ------------------------------------------------------------------------------------------ routing
def test_facts_are_routed_by_type_kind_and_topic_and_the_rest_go_to_general() -> None:
    facts = [
        fact(1, "requirement", "Customers browse the menu"),
        fact(2, "goal", "We want fewer phone orders"),
        fact(3, "preference", "Bright colours", "look"),
        fact(
            4, "rule", "Orders close at 9 pm", "audience"
        ),  # a rule, but the topic names audience too
        fact(5, "reference", "Like the Zomato app", "zzz"),
        fact(6, "requirement", "Gone", status="superseded"),
    ]
    groups = route(facts)
    assert [f.id for f in groups["people_and_scope"]] == ["FCT-001", "FCT-004"]
    assert [f.id for f in groups["idea"]] == ["FCT-002", "FCT-004", "FCT-005"]
    assert [f.id for f in groups["experience_and_systems"]] == ["FCT-003"]
    assert "general" not in groups and "FCT-006" not in {f.id for g in groups.values() for f in g}


def test_batches_respect_the_fact_count_and_the_character_cap() -> None:
    facts = [fact(i, "requirement", "word " * 20) for i in range(1, 8)]
    assert [len(b) for b in batches(facts, 3, 10_000)] == [3, 3, 1]
    line = (
        len(__import__("mde_compass.engine.context_pack", fromlist=["x"]).ledger_line(facts[0])) + 1
    )
    assert [len(b) for b in batches(facts, 100, line * 2 + 1)] == [2, 2, 2, 1]
    assert batches([], 3, 100) == []


def test_digests_change_with_the_facts_and_keys_are_stable() -> None:
    facts = [fact(1, "requirement", "A"), fact(2, "requirement", "B")]
    a = plan_calls(facts, max_facts=5, max_chars=5000)
    b = plan_calls(facts, max_facts=5, max_chars=5000)
    assert [(p.key, p.digest) for p in a] == [(p.key, p.digest) for p in b]
    changed = plan_calls(
        [facts[0], fact(2, "requirement", "B, changed")], max_facts=5, max_chars=5000
    )
    assert changed[0].key == a[0].key and changed[0].digest != a[0].digest
    assert a[0].key == "fill:people_and_scope:0"
    assert digest_of("k", ["s"], facts) != digest_of("k", ["t"], facts)
    assert typical_digest(facts) == typical_digest(list(reversed(facts)))  # order never matters
    assert typical_digest(facts) != typical_digest(facts[:1])
    assert stale_keys({"fill:idea:0": "x", "typical": "y", "text": "z"}, a) == ["fill:idea:0"]


def test_existing_lines_list_what_the_brief_holds_with_ids() -> None:
    brief = Brief()
    brief.scope = [FeatureRecord(id="FTR-001", name="Order  tracking")]
    brief.roles = [RoleRecord(id="ROL-001", name_in_user_words="Rider")]
    assert existing_lines(brief) == "ROL-001 | roles | Rider\nFTR-001 | scope | Order tracking"


# ----------------------------------------------------------------------------------------- grounding
def test_a_new_element_must_share_a_real_word_with_the_fact_it_cites() -> None:
    f = fact(1, "requirement", "Customers can track their order live on a map", "tracking")
    assert grounded({"name": "Live order tracking"}, [f])
    assert grounded("Track order", [f])
    assert not grounded({"name": "Drone parachute delivery"}, [f])
    assert not grounded({"name": "With this that"}, [f])  # stop words never count
    assert not grounded({"intent": "no name"}, [f])
    qr = fact(2, "requirement", "Scan a QR code at the table")
    assert grounded("QR", [qr]) and not grounded(
        "QR", [f]
    )  # a very short name must appear as written
    hi = fact(3, "requirement", "ग्राहक अपना ऑर्डर ट्रैक कर सकें")
    assert grounded({"name": "ऑर्डर ट्रैकिंग"}, [hi]) or grounded({"name": "ऑर्डर"}, [hi])


def test_names_compare_without_case_punctuation_or_plurals() -> None:
    assert (
        norm_name("Order-Tracking!") == norm_name("order tracking") == norm_name("orders tracking")
    )
    assert norm_name("Glass") == "glass"  # a double s is not a plural


def test_refs_resolve_ids_and_names_and_derive_only_long_names() -> None:
    brief = Brief()
    brief.scope = [
        FeatureRecord(id="FTR-001", name="Order tracking"),
        FeatureRecord(id="FTR-002", name="Map"),
    ]
    brief.roles = [RoleRecord(id="ROL-001", name_in_user_words="Rider")]
    assert resolve_refs(["FTR-001", "rider", "order-tracking", "Nothing", "FTR-999"], brief) == [
        "FTR-001",
        "ROL-001",
    ]
    f = fact(1, "rule", "Riders see the map and the order tracking screen")
    assert derive_refs(f, brief) == ["FTR-001", "ROL-001"]
    assert "FTR-002" not in derive_refs(
        f, brief
    )  # "Map" is shorter than four characters: too risky to link


# ---------------------------------------------------------------------------------------------- merge
def test_results_merge_in_order_and_a_placed_fact_is_never_not_used() -> None:
    a = SectionResult(
        placements=[
            DetailPlacement(
                fact_id="FCT-001", topic="menu", consumers=["blueprint"], kind="other", refs=["A"]
            )
        ],
        not_used=[
            NotUsedFact(fact_id="FCT-002", reason="x"),
            NotUsedFact(fact_id="FCT-003", reason="y"),
        ],
        summary="first",
    )
    b = SectionResult(
        placements=[
            DetailPlacement(
                fact_id="FCT-001", topic="later", consumers=["qa"], kind="rule", refs=["B"]
            ),
            DetailPlacement(fact_id="FCT-002", topic="t", consumers=["all"], kind="flow"),
        ],
        summary="second",
        used_fallback=True,
    )
    merged = merge_results([a, b])
    one = merged.placements["FCT-001"]
    assert (one.topic, one.consumers, one.kind, one.refs) == (
        "menu",
        ["blueprint", "qa"],
        "rule",
        ["A", "B"],
    )
    assert set(merged.not_used) == {"FCT-003"}  # FCT-002 was placed by the second call
    assert merged.summary == "first" and merged.failed == 1


# ------------------------------------------------------------------------- slot-less assumptions
def asm(
    claim: str, basis: str, decision: str = "proposed", id_: str = "ASM-001"
) -> AssumptionRecord:
    return AssumptionRecord(id=id_, claim=claim, basis=basis, decision=decision)  # type: ignore[arg-type]


def test_merge_derived_keeps_ids_and_decisions_tombstones_proposed_and_keeps_decided() -> None:
    brief = Brief()
    brief.ids.counters["ASM"] = 6
    brief.assumptions = [
        asm("Keep me", "typical", "accepted", "ASM-001"),
        asm("Refresh me", "typical", "proposed", "ASM-002"),
        asm("Gone and undecided", "typical", "proposed", "ASM-003"),
        asm("Gone but decided", "typical", "rejected", "ASM-004"),
        asm("A correction", "correction", "proposed", "ASM-005"),
        AssumptionRecord(id="ASM-006", claim="slot", slot_id="idea.summary"),
    ]
    fresh = [
        AssumptionRecord(id="ASM-000", claim="keep  ME", basis="typical", reason="new reason"),
        AssumptionRecord(
            id="ASM-000", claim="Refresh me", basis="typical", reason="new reason", impact="high"
        ),
        AssumptionRecord(id="ASM-000", claim="Brand new", basis="typical"),
    ]
    out = merge_derived(brief, fresh, ("typical",))
    by = {a.claim: a for a in out.assumptions}
    assert by["Keep me"].id == "ASM-001" and by["Keep me"].decision == "accepted"
    assert (
        by["Refresh me"].id == "ASM-002"
        and by["Refresh me"].impact == "high"
        and by["Refresh me"].reason == "new reason"
    )
    assert by["Brand new"].id == "ASM-007"
    assert "Gone and undecided" not in by and out.ids.is_deleted("ASM-003")
    assert by["Gone but decided"].decision == "rejected"  # the audit trail stays
    assert (
        by["A correction"].id == "ASM-005" and "slot" in by
    )  # other bases and slot assumptions are untouched
    assert [a.id for a in out.assumptions] == sorted(a.id for a in out.assumptions)
    assert brief.assumptions[2].decision == "proposed"  # the input is not mutated


def test_sync_assumptions_keeps_slot_less_assumptions() -> None:
    """``_rebuild`` runs ``sync_assumptions`` after every change: typical, correction and review assumptions must
    survive it (they stand for no slot, so the slot mirror must not touch them)."""
    brief = Brief()
    brief.assumptions = [
        asm("T", "typical", id_="ASM-001"),
        asm("C", "correction", "accepted", "ASM-002"),
        asm("R", "review", id_="ASM-003"),
    ]
    records: dict[str, SlotRecord] = {}
    out = sync_assumptions(brief, records, CATALOG)
    assert [(a.id, a.claim, a.decision) for a in out.assumptions] == [
        ("ASM-001", "T", "proposed"),
        ("ASM-002", "C", "accepted"),
        ("ASM-003", "R", "proposed"),
    ]


def test_typical_details_skip_what_is_already_said_and_flag_risky_ones_high() -> None:
    brief = Brief()
    brief.scope = [FeatureRecord(id="FTR-001", name="Order tracking")]
    brief.assumptions = [asm("Customers get a receipt", "pattern", id_="ASM-001")]
    typical = [
        TypicalDetail(claim="Customers get a receipt", kind="screen"),
        TypicalDetail(claim="The order tracking screen shows a map", kind="screen"),
        TypicalDetail(claim="Reminders go out the day before", kind="flow"),
        TypicalDetail(
            claim="Consent is asked first", kind="compliance"
        ),  # high by kind, not by the model's flag
        TypicalDetail(claim="Payments are handled by a provider", kind="nfr", risky=True),
    ]
    got = typical_assumptions(typical, brief, CATALOG, "en")
    assert [(a.claim, a.impact, a.theme) for a in got] == [
        ("Reminders go out the day before", "low", "Features"),
        ("Consent is asked first", "high", "Access"),
        ("Payments are handled by a provider", "high", "Business"),
    ]
    assert all(
        a.slot_id is None and a.basis == "typical" and a.status == "assumed" and a.tier == "Default"
        for a in got
    )


def test_corrections_follow_a_chain_and_ignore_a_restatement() -> None:
    f1 = fact(1, "number", "Delivery costs 20 rupees", "pricing", status="superseded")
    f2 = fact(
        2,
        "number",
        "Delivery costs 25 rupees",
        "pricing",
        status="superseded",
        supersedes=["FCT-001"],
    )
    f3 = fact(3, "number", "Delivery costs 30 rupees", "pricing", supersedes=["FCT-002"])
    same = fact(4, "rule", "Orders close at 9 pm", supersedes=["FCT-005"])
    old_same = fact(5, "rule", "orders close at 9 PM!", status="superseded")
    got = corrections_from_facts([f1, f2, f3, same, old_same], CATALOG, "en")
    assert [a.claim for a in got] == [
        "We used “Delivery costs 30 rupees”; you first said “Delivery costs 20 rupees”."
    ]
    assert got[0].impact == "low"  # touches no slot
    hi = corrections_from_facts([f1, f2, f3], CATALOG, "hi")
    assert hi[0].claim.startswith("हमने “Delivery costs 30 rupees” लिया")


def test_corrections_are_high_only_when_the_touched_slot_matters() -> None:
    def pair(slot: str) -> list[Fact]:
        old = fact(
            1,
            "context",
            "Pune",
            status="superseded",
            effects=[FactEffect(slot_id=slot, value_json='"x"')],
        )
        new = fact(
            2,
            "context",
            "Mumbai",
            supersedes=["FCT-001"],
            effects=[FactEffect(slot_id=slot, value_json='"y"')],
        )
        return [old, new]

    assert (
        corrections_from_facts(pair("context.geo"), CATALOG, "en")[0].impact == "high"
    )  # impact 5
    assert (
        corrections_from_facts(pair("look.density_mode"), CATALOG, "en")[0].impact == "low"
    )  # impact 2
    must_confirm = next(s for s in CATALOG.slots.values() if s.must_confirm)
    assert corrections_from_facts(pair(must_confirm.id), CATALOG, "en")[0].impact == "high"


def test_disputed_facts_are_grouped_by_slot_and_a_pair_says_both_ways() -> None:
    eff = [FactEffect(slot_id="context.geo", value_json='"x"')]
    a = fact(1, "context", "In Pune", status="disputed", effects=eff)
    b = fact(2, "context", "In Mumbai", status="disputed", effects=eff)
    lone = fact(3, "rule", "Free delivery", "pricing", status="disputed")
    got = corrections_from_facts([a, b, lone], CATALOG, "en")
    assert (
        got[0].claim
        == "You said both “In Pune” and “In Mumbai”; we kept the newer one. Please check."
    )
    assert got[0].impact == "high"
    assert (
        got[1].claim == "“Free delivery” does not fit something else you said. Please confirm it."
    )
    assert got[1].impact == "low"


def test_review_findings_that_do_not_add_up_become_assumptions_capped_at_five() -> None:
    findings = [
        ReviewFinding(slot_id="context.geo", text="Pune / Mumbai", kind="conflicting"),
        ReviewFinding(
            slot_id=None, text="fast", kind="vague"
        ),  # an open question, not an assumption
        ReviewFinding(slot_id=None, text="150% of users return daily", kind="unrealistic"),
        ReviewFinding(slot_id="look.density_mode", text="needs SMS", kind="dependency"),
        ReviewFinding(slot_id=None, text="Pune / Mumbai", kind="conflicting"),  # the same again
        *[ReviewFinding(slot_id=None, text=f"odd {i}", kind="plausibility") for i in range(10)],
    ]
    got = review_from_findings(findings, CATALOG, "en")
    assert len(got) == 5 and all(a.basis == "review" and a.slot_id is None for a in got)
    assert (
        got[0].claim == "These two do not fit together: Pune / Mumbai." and got[0].impact == "high"
    )
    assert (
        got[1].claim == "This may not be realistic: 150% of users return daily."
        and got[1].impact == "low"
    )
    assert (
        got[2].claim == "This needs something that is not in the plan: needs SMS."
        and got[2].impact == "low"
    )


def test_snippet_cuts_at_a_word_boundary() -> None:
    assert snippet("one two three four", 9) == "one two…"
    assert snippet("short", 50) == "short"
