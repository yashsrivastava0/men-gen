"""``engine.interview_state.InterviewMemory``: the working set of one turn. Ids are never reused, a turn writes
only what it changed, and an edited answer keeps both of its versions."""

from __future__ import annotations

import pytest

from mde_compass.domain.interview import BatchRecord, InterviewSummary
from mde_compass.engine.interview_state import InterviewMemory
from mde_compass.scenarios.doubles import NOW, make_card, make_fact, make_qa


def loaded() -> InterviewMemory:
    """A memory as the engine loads it at the start of a turn: ledgers filled, nothing yet written."""
    return InterviewMemory(
        facts=[make_fact(1), make_fact(2, status="superseded"), make_fact(5)],
        qa=[make_qa(make_card("Q-002")), make_qa(make_card("Q-003"), "open", asked=False)],
        batches=[BatchRecord(id="BAT-002", mode="initial", inputs_hash="h1")],
    )


# --------------------------------------------------------------------------------------- ids, never reused
def test_ids_continue_after_the_highest_even_with_gaps_and_superseded_facts() -> None:
    mem = loaded()
    assert mem.next_fact_id() == "FCT-006"  # FCT-003 and FCT-004 were never used, the highest is 5
    mem.add_fact(make_fact(6))
    assert mem.next_fact_id() == "FCT-007"
    assert mem.next_batch_id() == "BAT-003"


def test_ids_start_at_one_in_an_empty_memory_and_run_without_gaps() -> None:
    mem = InterviewMemory()
    seen = []
    for _ in range(4):
        fid = mem.next_fact_id()
        seen.append(fid)
        mem.add_fact(make_fact(int(fid.removeprefix("FCT-"))))
    assert seen == ["FCT-001", "FCT-002", "FCT-003", "FCT-004"] and mem.next_batch_id() == "BAT-001"


def test_a_superseded_or_not_used_fact_still_holds_its_id() -> None:
    mem = InterviewMemory(facts=[make_fact(1), make_fact(2)])
    mem.update_fact(make_fact(2, status="superseded"))
    mem.update_fact(make_fact(1, status="not_used"))
    assert mem.next_fact_id() == "FCT-003"
    assert [f.id for f in mem.active_facts()] == []


# ------------------------------------------------------------------------------------------ dirty tracking
def test_a_freshly_loaded_memory_is_not_dirty_so_a_read_only_turn_writes_nothing() -> None:
    mem = loaded()
    assert not mem.dirty and mem.new_fact_ids == [] and mem.changed_fact_ids == set()
    assert mem.qa_writes == {} and mem.batch_writes == [] and mem.summary_write is None


def test_adding_a_fact_marks_it_new_and_the_memory_dirty() -> None:
    mem = loaded()
    mem.add_fact(make_fact(6))
    assert mem.new_fact_ids == ["FCT-006"] and mem.dirty


def test_changing_a_loaded_fact_marks_it_changed_but_a_fact_new_this_turn_stays_new() -> None:
    mem = loaded()
    mem.update_fact(make_fact(1, status="superseded"))
    assert mem.changed_fact_ids == {"FCT-001"} and mem.dirty and mem.new_fact_ids == []
    mem.add_fact(make_fact(6))
    mem.update_fact(make_fact(6, status="disputed"))
    assert "FCT-006" not in mem.changed_fact_ids and mem.new_fact_ids == ["FCT-006"]
    assert mem.fact("FCT-006") is not None and mem.fact("FCT-006").status == "disputed"  # type: ignore[union-attr]


def test_updating_a_fact_that_does_not_exist_is_an_error_not_an_insert() -> None:
    with pytest.raises(KeyError):
        loaded().update_fact(make_fact(9))


def test_each_kind_of_write_makes_the_memory_dirty() -> None:
    cases = []
    m = loaded()
    m.put_qa(make_qa(make_card("Q-009")))
    cases.append(m)
    m = loaded()
    m.add_batch(BatchRecord(id="BAT-003", mode="replan", inputs_hash="h2"))
    cases.append(m)
    m = loaded()
    m.set_summary(InterviewSummary(version=1), NOW)
    cases.append(m)
    assert all(c.dirty for c in cases)


# ----------------------------------------------------------------------------------------------- Q&A ledger
def test_putting_a_known_question_replaces_it_in_place_and_records_the_write() -> None:
    mem = loaded()
    answered = make_qa(make_card("Q-003"), "answered")
    mem.put_qa(answered)
    assert [q.card_id for q in mem.qa] == ["Q-002", "Q-003"]  # order kept
    assert mem.qa_for("Q-003") is answered and mem.qa_writes == {("Q-003", 1): answered}


def test_an_edited_answer_writes_both_versions_the_old_one_superseded() -> None:
    mem = loaded()
    old = mem.qa_for("Q-002")
    assert old is not None
    new = old.model_copy(update={"version": 2, "status": "answered", "custom_text": "students"})
    mem.supersede_qa(old, new)
    assert mem.qa_for("Q-002") is new  # the newest version is the current one
    assert set(mem.qa_writes) == {("Q-002", 1), ("Q-002", 2)}
    assert (
        mem.qa_writes[("Q-002", 1)].status == "superseded"
        and mem.qa_writes[("Q-002", 1)].version == 1
    )
    assert mem.qa_writes[("Q-002", 2)].custom_text == "students"
    assert len([q for q in mem.qa if q.card_id == "Q-002"]) == 1  # one current row per question


def test_open_questions_and_the_questions_actually_put_to_the_person_are_told_apart() -> None:
    cards = [make_card(f"Q-00{n}", origin="followup" if n == 7 else "planner") for n in range(2, 8)]
    mem = InterviewMemory(
        qa=[
            make_qa(cards[0], "answered"),
            make_qa(cards[1], "skipped"),
            make_qa(cards[2], "you_decide"),
            make_qa(cards[3], "open", asked=False),
            make_qa(cards[4], "dropped"),
            make_qa(cards[5], "answered"),
        ]  # fmt: skip
    )
    assert [q.card_id for q in mem.open_qa()] == ["Q-005"]
    assert (
        mem.asked_count() == 4
    )  # answered, skipped, you_decide, answered: open and dropped are not asked
    assert mem.asked_count(origins=("followup",)) == 1
    assert mem.asked_count(origins=("planner", "fallback")) == 3


def test_a_superseded_version_is_not_counted_as_a_question_asked() -> None:
    mem = InterviewMemory(qa=[make_qa(make_card("Q-002"), "superseded")])
    assert mem.asked_count() == 0


# ---------------------------------------------------------------------------------------------- batches
def test_a_batch_is_found_again_by_mode_and_inputs_hash_so_a_retried_turn_does_not_pay_twice() -> (
    None
):
    mem = loaded()
    assert mem.find_batch("initial", "h1") is not None
    assert mem.find_batch("replan", "h1") is None and mem.find_batch("initial", "other") is None
    record = BatchRecord(id="BAT-003", mode="replan", inputs_hash="h2")
    mem.add_batch(record)
    assert mem.find_batch("replan", "h2") is record and mem.batch_writes == [record]


def test_the_summary_is_stamped_with_the_time_and_remembered_as_a_write() -> None:
    mem = loaded()
    mem.set_summary(InterviewSummary(version=2, through_fact_id="FCT-005"), NOW)
    assert mem.summary is not None and mem.summary.updated_at == NOW
    assert mem.summary_write is mem.summary
