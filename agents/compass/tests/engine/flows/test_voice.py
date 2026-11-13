"""Voice run-ons: atomic statements, echoes, the numbered read-back and removal."""

from __future__ import annotations

import time

from mde_compass.engine.flows import voice

DICTATION = (
    "so i want an app for my clinic in pune where patients can book appointments and doctors can see "
    "their schedule and also pay online and get reminders on whatsapp and hindi and english both"
)


def test_a_run_on_becomes_atomic_statements() -> None:
    assert voice.segment(DICTATION) == [
        "I want an app for my clinic in pune where patients can book appointments",
        "Doctors can see their schedule",
        "Pay online",
        "Get reminders on whatsapp",
        "Hindi and english both",  # a list of languages is not torn apart
    ]


def test_a_short_dictation_is_one_statement() -> None:
    assert voice.segment("book a table and pay") == ["book a table and pay"]
    assert voice.segment("") == []
    assert voice.segment("   ") == []


def test_hindi_dictation_is_split_on_hindi_joiners() -> None:
    text = (
        "मुझे क्लिनिक का ऐप चाहिए जिसमें मरीज़ अपॉइंटमेंट बुक करें और डॉक्टर शेड्यूल देखें और भुगतान करें और रिमाइंडर मिलें"
    )
    assert len(voice.segment(text)) == 4


def test_sentences_split_first_and_fragments_join_the_line_before() -> None:
    out = voice.segment(
        "I want a booking app for my gym members. They pay monthly. Reminders by sms yes."
    )
    assert out == [
        "I want a booking app for my gym members",
        "They pay monthly",
        "Reminders by sms yes",
    ]


def test_the_number_of_statements_is_capped() -> None:
    text = " and also ".join(f"people can do thing number {i}" for i in range(40))
    assert len(voice.segment(text)) == 12


def test_three_or_more_statements_are_read_back_as_a_numbered_list() -> None:
    plan = voice.plan(DICTATION)
    assert plan.readback and len(plan.statements) == 5
    assert voice.numbered(plan.statements).startswith("1. I want an app")


def test_two_statements_are_not_read_back_unless_ambiguous() -> None:
    text = "patients can book appointments with the doctor and doctors can see their schedule today"
    assert not voice.plan(text).readback
    assert voice.plan(text, ambiguous=True).readback


def test_names_numbers_and_places_are_echoed_once_each() -> None:
    plan = voice.plan(
        "we have 250 members and 12 trainers in Pune",
        geo={"city": "Pune"},
        brand="FitHub",
        references=[{"label": "Cult"}, {"label": "fithub"}],
    )
    assert [(e.kind, e.text) for e in plan.echoes] == [
        ("place", "Pune"), ("name", "FitHub"), ("name", "Cult"), ("number", "250"), ("number", "12"),
    ]  # fmt: skip


def test_the_numbered_list_is_read_back_from_the_last_assistant_message() -> None:
    lines = [
        "hello",
        "I heard 3 things. Please check them:\n1. First one\n2. Second one\n3. Third one\nSay remove 2",
        "ok",
    ]
    assert voice.last_list(lines) == ["First one", "Second one", "Third one"]
    assert voice.last_list(["no list here", "2. starts at two"]) == []
    assert voice.last_list(["1. old list", "second", "third"]) == []  # no longer on screen


def test_removal_requests() -> None:
    assert voice.removal_request("remove 2", 5) == [2]
    assert voice.removal_request("please drop number 3 and 4", 5) == [3, 4]
    assert voice.removal_request("2 hatao", 5) == [2]
    assert voice.removal_request("please remove number 2 and 4", 5) == [2, 4]
    assert voice.removal_request("remove 2 features", 5) == []  # about something else, not the list
    assert voice.removal_request("remove 9", 5) == []  # not on the list
    assert voice.removal_request("I want a delivery app and remove the login", 5) == []  # no number
    assert voice.removal_request("book 2 tables", 5) == []  # no removal word
    long = "remove " + "word " * 20 + "3"
    assert voice.removal_request(long, 5) == []  # a long message is a normal message


def test_segmentation_is_linear_on_hostile_text() -> None:
    started = time.perf_counter()
    voice.segment("and " * 20_000)
    voice.segment("hindi and " * 10_000)
    voice.segment("a" * 50_000)
    voice.plan("1 " * 25_000)
    assert time.perf_counter() - started < 3.0
