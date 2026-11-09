"""``engine.interview_support``: the pure helpers of the interview controller (what the model is shown, how a
request is hashed, how facts are built and derived)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from mde_compass.catalog import load_catalog
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain.brief import Brief
from mde_compass.domain.common import BriefItem
from mde_compass.domain.interview import FactEffect, InterviewBlock
from mde_compass.engine import interview_support as sup
from mde_compass.engine.ports import ExtractedFact, SlotUpdate, TurnUnderstanding
from mde_compass.engine.slots import SlotStore
from mde_compass.scenarios.doubles import (
    NOW,
    make_card,
    make_fact,
    make_qa,
    make_status,
    plan_request,
)
from mde_core.clock import FakeClock

CATALOG = load_catalog()
MIN_CHARS = DEFAULT_CONFIG.interview.min_quote_chars


# ------------------------------------------------------------------------------------------------ text_of
@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, ""),
        ("", ""),
        ([], ""),
        ({}, ""),
        ("Pune", "Pune"),
        (["a", "", "b"], "a, b"),
        ({"city": "Pune", "country": "India"}, "Pune"),
        ({"name": "Rider", "goal": "deliver"}, "Rider"),
        ({"x": "1", "y": ""}, "x: 1"),
        (12, "12"),
        (True, "True"),
    ],
)
def test_text_of_flattens_a_slot_value_to_a_short_line(value: Any, text: str) -> None:
    assert sup.text_of(value) == text


@pytest.mark.parametrize(
    ("slot", "kind"),
    [
        ("roles.list", "role"),
        ("goals.primary", "goal"),
        ("constraints.offline", "constraint"),
        ("glossary.terms", "glossary"),
        ("scope.must_not", "exclusion"),
        ("scope.references", "reference"),
        ("integrations.wanted", "requirement"),
        ("scope.features", "requirement"),
        ("context.geo", "context"),
        ("audience.segments", "context"),
        ("locales", "context"),
        ("look.tone_words", "preference"),
        ("brand.name", "preference"),
        ("idea.business_model", "requirement"),  # no prefix matches: the default
    ],
)
def test_the_fact_type_follows_the_slot_prefix(slot: str, kind: str) -> None:
    assert sup.fact_type_for(slot) == kind


# ------------------------------------------------------------------------------------------- need_briefs
def statuses(**kw: tuple[str, int]) -> dict[str, Any]:
    return {k: make_status(k, state, impact=impact) for k, (state, impact) in kw.items()}


def test_need_briefs_list_open_needs_first_by_state_then_impact() -> None:
    table = {
        "audience.segments": make_status("audience.segments", "satisfied", impact=5),
        "roles.list": make_status("roles.list", "partial", impact=5),
        "idea.business_model": make_status("idea.business_model", "missing", impact=4),
        "context.geo": make_status("context.geo", "missing", impact=5),
        "scope.features": make_status("scope.features", "assumed", impact=5),
    }
    out = sup.need_briefs(table, CATALOG, "en")
    assert [b.need_id for b in out] == [
        "context.geo", "idea.business_model", "roles.list", "scope.features", "audience.segments",
    ]  # fmt: skip
    assert out[0].sufficient and out[0].aspects and out[0].label


def test_a_need_asked_twice_and_still_open_is_offered_no_more_it_counts_as_assumed() -> None:
    once = make_status("audience.segments", "missing").model_copy(update={"asked": 1})
    twice = make_status("audience.segments", "partial").model_copy(update={"asked": 2})
    done = make_status("roles.list", "satisfied").model_copy(update={"asked": 3})
    out = {
        b.need_id: b
        for b in sup.need_briefs({"audience.segments": once, "roles.list": done}, CATALOG, "en")
    }
    assert out["audience.segments"].state == "missing" and out["roles.list"].state == "satisfied"
    capped = sup.need_briefs({"audience.segments": twice}, CATALOG, "en")
    assert capped[0].state == "assumed"  # the planner no longer sees it as a need to ask about
    third = once.model_copy(update={"asked": 5})
    assert sup.need_briefs({"audience.segments": third}, CATALOG, "en")[0].state == "assumed"


def test_generated_needs_and_unknown_ids_are_never_shown_to_the_model() -> None:
    table = {
        "unknowns.research_wanted": make_status(
            "unknowns.research_wanted", "missing", level="generated"
        ),
        "domain:rider_pay": make_status("domain:rider_pay", "missing"),
        "audience.segments": make_status("audience.segments", "missing"),
    }
    assert [b.need_id for b in sup.need_briefs(table, CATALOG, "en")] == ["audience.segments"]


def test_need_labels_follow_the_language() -> None:
    table = {"audience.segments": make_status("audience.segments", "missing")}
    en = sup.need_briefs(table, CATALOG, "en")[0].label
    hi = sup.need_briefs(table, CATALOG, "hi")[0].label
    assert en != hi and any("ऀ" <= ch <= "ॿ" for ch in hi)


def test_an_assumed_value_travels_with_its_need() -> None:
    st = make_status("audience.segments", "assumed").model_copy(
        update={"assumed_value": ["everyone"]}
    )
    assert sup.need_briefs({"audience.segments": st}, CATALOG, "en")[0].assumed_value == [
        "everyone"
    ]


def test_style_notes_come_from_the_frames_of_the_needs_involved_and_are_capped() -> None:
    notes = sup.style_notes(CATALOG, ["audience.segments", "roles.list", "scope.features"])
    assert notes and all(": " in n and "Examples:" in n for n in notes)
    assert len(sup.style_notes(CATALOG, CATALOG.slots.keys(), limit=2)) <= 2
    assert sup.style_notes(CATALOG, []) == []


# ----------------------------------------------------------------------------------------- project_card
def test_the_project_card_names_the_idea_place_audience_apps_mode_and_language() -> None:
    brief = Brief()
    brief.idea["summary"] = BriefItem(value="A tiffin app", status="volunteered", tier="Locked")
    brief.context["geo"] = BriefItem(value={"city": "Pune"}, status="volunteered")
    brief.audience["segments"] = BriefItem(value=["Office workers"], status="volunteered")
    card = sup.project_card(brief, "hi", "guided")
    assert card["idea"] == "A tiffin app" and card["geography"] == "Pune"
    assert (
        card["audience"] == "Office workers"
        and card["language"] == "hi"
        and card["mode"] == "guided"
    )
    assert card["category"] == "" and card["situation"] == {}


# ------------------------------------------------------------------------------------------ inputs_hash
def test_the_inputs_hash_is_stable_and_changes_with_any_input() -> None:
    a, b = plan_request("initial"), plan_request("initial")
    assert sup.inputs_hash(a) == sup.inputs_hash(b) and len(sup.inputs_hash(a)) == 64
    assert sup.inputs_hash(a) != sup.inputs_hash(plan_request("replan"))
    assert sup.inputs_hash(a) != sup.inputs_hash(plan_request("initial", want=(1, 2)))
    more = a.model_copy(update={"project_terms": [*a.project_terms, "extra"]})
    assert sup.inputs_hash(a) != sup.inputs_hash(more)
    shuffled = json.loads(a.model_dump_json())
    assert sup.inputs_hash(
        type(a).model_validate(dict(reversed(list(shuffled.items()))))
    ) == sup.inputs_hash(a)


# --------------------------------------------------------------------------- numbers and counts of cards
def test_the_next_card_number_is_past_every_id_seen_in_cards_or_the_ledger() -> None:
    assert sup.next_card_number([], []) == 1
    cards = [make_card("Q-004"), make_card("Q-002")]
    qa = [make_qa(make_card("Q-009")), make_qa(make_card("weird"))]
    assert sup.next_card_number(cards, qa) == 10


def test_counts_by_need_count_shown_questions_only_and_followups_on_request() -> None:
    base = make_card("Q-002", needs=("audience.segments",))
    follow = make_card(
        "Q-003", needs=("audience.segments",), origin="followup", parent="Q-002", depth=1
    )
    other = make_card("Q-004", needs=("roles.list", "domain:x"))
    ledger = [
        make_qa(base), make_qa(follow), make_qa(other, "skipped"),
        make_qa(make_card("Q-005", needs=("roles.list",)), "open"),
        make_qa(make_card("Q-006", needs=("roles.list",)), "dropped"),
        make_qa(make_card("Q-007", needs=("roles.list",)), "superseded"),
    ]  # fmt: skip
    assert sup.counts_by_need(ledger) == {"audience.segments": 2, "roles.list": 1, "domain:x": 1}
    assert sup.counts_by_need(ledger, followups_only=True) == {"audience.segments": 1}


def test_the_audit_block_is_recounted_from_the_ledgers_and_keeps_what_is_not_a_count() -> None:
    cards = [make_card(f"Q-00{n}") for n in range(2, 8)]
    qa = [
        make_qa(cards[0], "answered"), make_qa(cards[1], "skipped"), make_qa(cards[2], "you_decide"),
        make_qa(cards[3], "dropped"), make_qa(cards[4], "open"),
        make_qa(make_card("Q-009", origin="followup"), "answered"),
    ]  # fmt: skip
    facts = [make_fact(1), make_fact(2, status="superseded"), make_fact(3, status="not_used")]
    block = InterviewBlock(fallback_count=2, stop_reason="budget", budget="quick")
    out = sup.refresh_block(block, qa, facts, batches=3)
    assert (out.questions_asked, out.followups, out.answered, out.skipped, out.assumed) == (
        4,
        1,
        2,
        1,
        1,
    )
    assert (out.batches, out.facts_active, out.facts_not_used) == (3, 1, 1)
    assert out.question_ids == [
        "Q-002",
        "Q-003",
        "Q-004",
        "Q-006",
        "Q-009",
    ]  # not dropped or superseded
    assert out.fallback_count == 2 and out.stop_reason == "budget" and out.budget == "quick"


# ------------------------------------------------------------------------------------------- build_fact
def build(ef: ExtractedFact, sources: list[str], **kw: Any) -> Any:
    return sup.build_fact(
        ef, fact_id=kw.pop("fact_id", "FCT-001"), sources=sources, source_ref="evt_1", card_id=kw.pop("card_id", None),
        origin=kw.pop("origin", "stated"), now=NOW, min_quote_chars=kw.pop("min_chars", MIN_CHARS),
    )  # fmt: skip


def extracted(quote: str, statement: str = "Customers are office workers") -> ExtractedFact:
    return ExtractedFact(
        type="context", statement=statement, quote=quote, topics=["audience"], confidence=0.7
    )


def test_a_quote_found_in_the_users_words_is_verified() -> None:
    fact = build(extracted("office workers"), ["We serve office workers in Pune"])
    assert not fact.unverified_quote and fact.status == "active" and fact.origin == "stated"
    assert fact.id == "FCT-001" and fact.source_ref == "evt_1" and fact.created_at == NOW


@pytest.mark.parametrize(
    "quote",
    [
        "OFFICE   WORKERS",
        "  office\nworkers  ",
        "Office Workers",
        "“office workers”",
        "office workers.",
    ],
)
def test_quote_verification_ignores_case_whitespace_and_edge_punctuation(quote: str) -> None:
    assert not build(extracted(quote), ["We serve office workers in Pune"]).unverified_quote


@pytest.mark.parametrize(
    "quote",
    ["office staff", "we serve students", "office ... Pune", "officeworkers", "x"],
)
def test_a_quote_the_user_never_said_is_kept_but_flagged_and_can_never_support_a_locked_item(
    quote: str,
) -> None:
    fact = build(extracted(quote), ["We serve office workers in Pune"])
    assert fact.unverified_quote and fact.quote == quote and not fact.supports_locked()


def test_a_tap_is_verified_against_the_labels_of_the_chosen_options_not_the_question() -> None:
    labels = ["Independent riders paid per delivery"]
    assert not build(
        extracted("Independent riders paid per delivery"), labels, origin="tap"
    ).unverified_quote
    spoofed = build(
        extracted("Who carries the tiffins"), labels, origin="tap"
    )  # words of the question
    assert spoofed.unverified_quote


def test_a_fact_without_a_quote_is_not_flagged_but_never_supports_a_locked_item() -> None:
    fact = build(extracted(""), ["anything"], origin="assumption")
    assert fact.quote == "" and not fact.unverified_quote and not fact.supports_locked()


def test_the_minimum_quote_length_is_a_policy_setting() -> None:
    assert build(extracted("an"), ["an app"], min_chars=3).unverified_quote
    assert not build(extracted("an"), ["an app"], min_chars=2).unverified_quote


def test_a_long_statement_and_quote_are_cut_and_an_empty_statement_gets_a_placeholder() -> None:
    long = build(extracted("office workers", statement="x" * 3000), ["office workers"])
    assert len(long.statement) == 1000
    assert build(extracted("a" * 900), ["a" * 900]).quote == "a" * 500
    assert (
        build(extracted("office workers", statement="   "), ["office workers"]).statement
        == "(empty)"
    )


def test_the_extracted_fields_are_carried_to_the_fact() -> None:
    effect = FactEffect(slot_id="audience.segments", value_json='["Office workers"]')
    ef = ExtractedFact(
        type="rule", statement="s", quote="office", topics=["a"], entities=["Pune"], consumers=["blueprint"],
        confidence=0.9, aspects=["audience.segments:who"], effects=[effect],
    )  # fmt: skip
    fact = build(ef, ["office"], card_id="Q-002")
    assert (fact.type, fact.topics, fact.entities, fact.consumers) == (
        "rule",
        ["a"],
        ["Pune"],
        ["blueprint"],
    )
    assert (
        fact.aspects == ["audience.segments:who"]
        and fact.effects == [effect]
        and fact.qa_card_id == "Q-002"
    )


# ----------------------------------------------------------------------------------------- derived_facts
def upd(slot: str, value: Any, *, remove: bool = False, quote: str = "q") -> SlotUpdate:
    return SlotUpdate(slot_id=slot, value=value, quote=quote, confidence=0.6, remove=remove)


def test_slot_updates_no_fact_accounts_for_become_facts_so_the_ledger_is_complete() -> None:
    understood = TurnUnderstanding(
        slot_updates=[
            upd("context.geo", {"city": "Pune"}),
            upd("audience.segments", ["Office workers"], quote="office"),
        ]
    )
    out = sup.derived_facts(understood, {"audience.segments"}, CATALOG, "en", complete=False)
    assert [e.effects[0].slot_id for e in out] == ["context.geo"]  # the covered slot is skipped
    fact = out[0]
    assert (
        fact.statement == "Country and city: Pune" and fact.quote == "q" and fact.confidence == 0.6
    )
    assert fact.topics == ["context"] and fact.type == "context"
    assert (
        json.loads(fact.effects[0].value_json) == {"city": "Pune"} and fact.effects[0].mode == "set"
    )
    assert fact.aspects == []  # not a tap: it does not complete its need


def test_a_tap_completes_its_need_and_a_removal_is_a_remove_effect() -> None:
    tap = sup.derived_facts(
        TurnUnderstanding(slot_updates=[upd("auth.methods", ["phone_otp"])]),
        set(),
        CATALOG,
        "en",
        complete=True,
    )
    assert tap[0].aspects == ["auth.methods:*"]
    gone = sup.derived_facts(
        TurnUnderstanding(slot_updates=[upd("scope.features", None, remove=True)]),
        set(),
        CATALOG,
        "en",
        complete=False,
    )
    assert gone[0].effects[0].mode == "remove" and gone[0].statement.endswith("not wanted")


def test_derived_facts_name_the_consumers_the_catalog_lists_and_skip_unknown_slots() -> None:
    out = sup.derived_facts(
        TurnUnderstanding(slot_updates=[upd("nope.slot", 1), upd("roles.list", [{"name": "Rider"}])]),
        set(), CATALOG, "en", complete=False,
    )  # fmt: skip
    assert len(out) == 1 and out[0].type == "role"
    assert out[0].consumers == [c for c in CATALOG.slots["roles.list"].consumers if c != "all"]


def test_derived_facts_are_written_in_the_users_language() -> None:
    out = sup.derived_facts(
        TurnUnderstanding(slot_updates=[upd("context.geo", {"city": "Pune"})]),
        set(),
        CATALOG,
        "hi",
        complete=False,
    )
    assert out[0].statement.endswith(": Pune") and not out[0].statement.startswith("Country")


# --------------------------------------------------------------------------------------------- small ones
def test_slots_of_collects_the_slots_facts_and_extracted_facts_touch() -> None:
    effect = FactEffect(slot_id="context.geo", value_json="1")
    fact = make_fact(1, effects=[effect])
    ext = ExtractedFact(type="context", statement="s", effects=[FactEffect(slot_id="roles.list")])
    assert sup.slots_of([fact, ext]) == {"context.geo", "roles.list"}
    assert sup.slots_of([]) == set()


def test_values_snapshot_reads_only_the_slots_that_exist() -> None:
    from mde_compass.domain.common import Provenance

    store = SlotStore(CATALOG, clock=FakeClock())
    store.write(
        "context.geo", {"city": "Pune"}, state="volunteered",
        provenance=Provenance(type="user_text", ref="evt_1", quote="Pune"), confidence=0.8, turn_id="t1",
    )  # fmt: skip
    assert sup.values_snapshot(store, ["context.geo", "locales"]) == {
        "context.geo": {"city": "Pune"}
    }
