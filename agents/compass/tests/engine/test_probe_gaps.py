"""A handoff-probe gap that was not fixed is recorded, never a silent "degraded" (ADR 0016)."""

from __future__ import annotations

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import AssumptionRecord, Brief, OpenQuestion
from mde_compass.engine.assumptions import review, sync_assumptions
from mde_compass.engine.ports import ProbeGap
from mde_compass.engine.probe_gaps import gap_to_assumption, record_gaps

CATALOG = load_catalog()


def test_a_gap_with_a_suggestion_becomes_an_assumption_and_the_rest_a_non_blocking_question() -> (
    None
):
    suggested = ProbeGap(
        question="Which look do you want?",
        slot_id="look.density_mode",
        fix="assume",
        suggested_value="compact",
    )
    row = gap_to_assumption(suggested, CATALOG)
    assert isinstance(row, AssumptionRecord)
    assert row.claim == "We could not tell How full the screens are; we suggest compact."
    assert (row.basis, row.slot_id, row.decision, row.impact) == ("gap", None, "proposed", "low")
    assert row.what_changes and row.reason and row.tier == "Default"
    asked = gap_to_assumption(
        ProbeGap(question="  Which country is it for?  ", slot_id="context.geo"), CATALOG
    )
    assert isinstance(asked, OpenQuestion)
    assert (asked.text, asked.blocking, asked.slot_id) == (
        "Which country is it for?",
        False,
        "context.geo",
    )
    # an assume gap with no value, or an unknown slot, is still recorded: as a question
    assert isinstance(
        gap_to_assumption(ProbeGap(question="x?", slot_id="idea.summary", fix="assume"), CATALOG),
        OpenQuestion,
    )
    odd = gap_to_assumption(
        ProbeGap(question="x?", slot_id="nope.nothing", fix="assume", suggested_value="y"), CATALOG
    )
    assert isinstance(odd, OpenQuestion) and odd.slot_id is None


def test_a_suggestion_for_an_important_slot_is_high_impact_so_it_blocks_gate_1_until_decided() -> (
    None
):
    gap = ProbeGap(
        question="Where?", slot_id="context.geo", fix="assume", suggested_value={"city": "Pune"}
    )
    row = gap_to_assumption(gap, CATALOG)
    assert isinstance(row, AssumptionRecord) and row.impact == "high"
    assert row.claim == "We could not tell Country and city; we suggest Pune."
    hi = gap_to_assumption(gap, CATALOG, language="hi")
    assert isinstance(hi, AssumptionRecord) and "सुझाव" in hi.claim


def test_record_gaps_adds_rows_once_with_stable_ids_and_survives_the_slot_mirror() -> None:
    brief = Brief()
    gaps = [
        ProbeGap(
            question="Which look?",
            slot_id="look.density_mode",
            fix="assume",
            suggested_value="compact",
        ),
        ProbeGap(question="Which country?", slot_id="context.geo"),
    ]
    once = record_gaps(brief, gaps, CATALOG)
    twice = record_gaps(once, gaps, CATALOG)
    assert [(a.id, a.basis) for a in twice.assumptions] == [("ASM-001", "gap")]
    assert [(q.id, q.text, q.blocking) for q in twice.open_questions] == [
        ("Q-001", "Which country?", False)
    ]
    kept = sync_assumptions(twice, {}, CATALOG)
    assert [a.id for a in kept.assumptions] == ["ASM-001"]
    # the person decides; a later probe that no longer reports the gap leaves the decision as the audit trail
    decided = twice.model_copy(deep=True)
    decided.assumptions = [
        a.model_copy(update={"decision": "accepted"}) for a in decided.assumptions
    ]
    assert [a.decision for a in record_gaps(decided, [], CATALOG).assumptions] == ["accepted"]
    assert review(record_gaps(brief, [gaps[0]], CATALOG)).unreviewed_high_ids == []
