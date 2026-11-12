"""Which topic carries a Brief research request: one request, one topic, chosen by the words in ``data/plan.yaml``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_research_agent.catalog import load_catalog
from mde_research_agent.core.text import stem_set
from mde_research_agent.domain.input import Request
from mde_research_agent.intake import read_export
from mde_research_agent.plan.build import build_plan
from mde_research_agent.plan.candidates import trusted_requests
from mde_research_agent.plan.routing import (
    job_of_request,
    read_request,
    score_words,
    specific_entity,
)

ROOT = Path(__file__).resolve().parents[4]
INPUTS = ROOT / "docs" / "compass" / "research-inputs"
CAT = load_catalog()
STEMS = sorted(p.stem for p in INPUTS.glob("*.json"))


def plan_of(stem: str):  # type: ignore[no-untyped-def]
    inp = read_export(json.loads((INPUTS / f"{stem}.json").read_text(encoding="utf-8")), CAT)
    return inp, build_plan(inp, CAT)


def carrying(plan, request_id: str):  # type: ignore[no-untyped-def]
    return [t for t in [*plan.targets, *plan.unrun] if request_id in t.links.requests]


def req(topic: str, question: str = "", entity: str = "", decision: str = "") -> Request:
    return Request(
        id="RR-1", topic=topic, question=question, entity=entity, decision_unlocked=decision
    )


# --- the three misroutings found on the synthetic inputs ---------------------------------------------------------------


def test_a_competitor_landscape_question_that_mentions_fees_is_not_a_rules_topic() -> None:
    """Dakar RR-001: 'which parent apps do schools use ... for timetables, grades, attendance and fees'."""
    _, plan = plan_of("synthetic-medium-school-parents-dakar")
    (topic,) = carrying(plan, "RR-001")
    assert (topic.job, topic.template_id) == ("features", "landscape")


def test_a_request_is_carried_by_exactly_one_topic_in_every_input() -> None:
    """Site-safety RR-001 used to sit on two topics (a landscape topic and a rules topic): never again."""
    for stem in STEMS:
        inp, plan = plan_of(stem)
        for r in trusted_requests(inp):
            topics = carrying(plan, r.id)
            assert len(topics) <= 1, (stem, r.id, [t.label for t in topics])
    _, big = plan_of("synthetic-big-site-safety-central-america")
    (topic,) = carrying(big, "RR-001")
    assert (topic.job, topic.template_id) == ("features", "landscape")


def test_most_of_the_big_requests_have_a_topic() -> None:
    """Six of the twelve Big requests were unmapped; the words and the catch-all topics now place all but one."""
    inp, plan = plan_of("synthetic-big-site-safety-central-america")
    assert len(trusted_requests(inp)) == 12
    unmapped = [r.id for r in trusted_requests(inp) if not carrying(plan, r.id)]
    assert len(unmapped) <= 2, unmapped


def test_across_all_nine_inputs_almost_every_usable_request_has_a_topic() -> None:
    total = missing = 0
    for stem in STEMS:
        inp, plan = plan_of(stem)
        for r in trusted_requests(inp):
            total += 1
            missing += not carrying(plan, r.id)
    assert total == 84 and missing <= 5, (total, missing)


def test_a_request_about_a_named_app_goes_to_that_apps_own_topic() -> None:
    inp, plan = plan_of("delhi-restaurant-delivery")
    names = {r.label for r in inp.references}
    assert names >= {"Zomato", "Swiggy"}
    for rid, app in (("RR-011", "Zomato"), ("RR-012", "Swiggy")):
        (topic,) = carrying(plan, rid)
        assert topic.template_id == "references" and topic.entity == app


def test_a_provider_request_is_carried_by_the_matching_provider_topic_only() -> None:
    _, plan = plan_of("thorough-freight-fleet-india")
    tracking = carrying(plan, "RR-012")  # GPS telematics integration
    messaging = carrying(plan, "RR-011")  # messaging for freight operations
    assert [t.template_id for t in tracking + messaging] == ["providers", "providers"]
    assert "tracking" in tracking[0].label.casefold() and "message" in messaging[0].label.casefold()
    assert tracking[0].id != messaging[0].id


# --- the router itself -------------------------------------------------------------------------------------------------


def test_a_word_in_the_topic_line_beats_a_word_in_the_question() -> None:
    r = req(
        "Parent apps used by schools", "Which apps do schools use for timetables, grades and fees?"
    )
    assert job_of_request(read_request(r), CAT) == "features"
    r = req("Fee rules", "Which comparable apps and competitors show a fee list?")
    assert job_of_request(read_request(r), CAT) == "data"


def test_the_words_of_the_apps_own_category_say_nothing_about_the_job() -> None:
    r = req("Payment options", "How does a payment work for a small shop?")
    assert job_of_request(read_request(r), CAT) == "services"
    ignore = stem_set(
        "payments app"
    )  # in a payments app, "payment" describes the product, not the request
    assert job_of_request(read_request(r, ignore), CAT) is None


def test_a_two_word_phrase_counts_for_both_words_and_a_repeat_counts_once() -> None:
    reading = read_request(req("Local providers in the city"))
    assert score_words(reading, ["local providers"])[0] == 6
    assert (
        score_words(reading, ["local providers", "local provider"])[0] == 6
    )  # the same phrase twice
    assert score_words(read_request(req("Permit", "permit")), ["permit", "permits"])[0] == 4


def test_no_word_means_no_job() -> None:
    assert (
        job_of_request(read_request(req("Teacher contact sharing", "What do teachers share?")), CAT)
        is None
    )
    assert job_of_request(read_request(req("")), CAT) is None


def test_a_tie_goes_to_the_word_that_comes_first_in_the_topic_line() -> None:
    payments_first = req("Payments and accounts")
    accounts_first = req("Accounts and payments")
    assert job_of_request(read_request(payments_first), CAT) == "services"
    assert job_of_request(read_request(accounts_first), CAT) == "structure"


def test_an_entity_that_only_repeats_the_category_is_no_entity() -> None:
    ignore = stem_set("hospital patient and staff app")
    assert specific_entity(req("t", entity="hospital patient and staff apps"), ignore) == ""
    assert specific_entity(req("t", entity="Zomato"), ignore) == "Zomato"


@pytest.mark.parametrize("stem", STEMS)
def test_routing_is_deterministic(stem: str) -> None:
    inp, plan = plan_of(stem)
    again = build_plan(inp, CAT)
    assert [t.links.requests for t in plan.targets] == [t.links.requests for t in again.targets]
