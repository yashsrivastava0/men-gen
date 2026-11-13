"""The ``capability`` foreach: one platform topic per capability the Brief implies, from data/capabilities.yaml (D-156).

The Brief's own words and the signals of plan/signals.py decide WHETHER a capability is researched; they are never a source.
"""

from __future__ import annotations

import pytest
from support.testing import load_fixture

from mde_research_agent.catalog import load_catalog
from mde_research_agent.domain.input import Feature, ResearchInput
from mde_research_agent.intake.reader import read_export
from mde_research_agent.plan.build import build_plan
from mde_research_agent.plan.candidates import candidates_for, capability_candidates

CAT = load_catalog()


def tiny(*features: tuple[str, str], load_class: str = "standard") -> ResearchInput:
    """The tiny converter Brief (no capability implied) with these features."""
    base = read_export(load_fixture("unit-converter-tiny"), CAT)
    feats = tuple(
        Feature(id=f"FTR-{n:03d}", name=name, intent=intent)
        for n, (name, intent) in enumerate(features, start=1)
    )
    load = base.load.model_copy(update={"load_class": load_class}) if base.load else None
    return base.model_copy(update={"features": feats, "load": load})


def ids(inp: ResearchInput) -> list[str]:
    return [c.extra["capability"] for c in capability_candidates(inp, CAT)]


def test_a_brief_that_implies_nothing_gets_no_platform_topic() -> None:
    assert ids(tiny(("Convert units", "Convert a length or a weight"))) == []
    assert build_plan(tiny(("Convert units", "Convert a length")), CAT).targets == () or all(
        t.job != "platform"
        for t in build_plan(tiny(("Convert units", "Convert a length")), CAT).targets
    )


def test_a_strong_word_implies_a_capability_and_links_the_feature() -> None:
    inp = tiny(("Reminders", "Send a push notification before each appointment"))
    (cand,) = capability_candidates(inp, CAT)
    assert cand.extra["capability"] == "push_notifications" and cand.name == "push notifications"
    assert cand.links.features == ("FTR-001",), "the topic carries the Brief id that implied it"
    assert cand.terms and cand.bonus >= 2


def test_weak_words_count_only_in_enough_different_pieces_of_the_brief() -> None:
    one = tiny(("Invite", "Invite a friend and share a result"))
    assert "deep_links" not in ids(one), "one feature carrying weak words is not enough"
    two = tiny(("Invite", "Invite a friend"), ("Share", "Share a score with a link"))
    assert "deep_links" in ids(two)


def test_a_signal_implies_its_capability_without_any_capability_word() -> None:
    inp = tiny(("Checkout", "Customers pay online at checkout"))
    assert "store_payments" in ids(inp)


def test_each_capability_is_one_candidate_and_the_best_evidenced_comes_first() -> None:
    inp = tiny(
        ("Live tracking", "Track the vehicle with live tracking and background location"),
        ("Alerts", "Send a push notification"),
        ("Alerts again", "Another push notification when a trip ends"),
        ("Photos", "Take photo of the delivery with the camera"),
    )
    got = ids(inp)
    assert len(got) == len(set(got)), "at most one topic per capability"
    assert got.index("background_location") < got.index("push_notifications") or got.index(
        "push_notifications"
    ) < got.index("camera_photo_upload")
    names = [c.name for c in capability_candidates(inp, CAT)]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("klass", ["focused", "standard", "deep"])
def test_the_size_class_caps_how_many_capabilities_are_planned(klass: str) -> None:
    every = tuple(
        (
            c.label,
            f"{c.strong[0] if c.strong else c.weak[0]} and {c.strong[-1] if c.strong else c.weak[-1]}",
        )
        for c in CAT.capabilities.capabilities
    )
    got = ids(tiny(*every, load_class=klass))
    assert len(got) == min(
        CAT.depth.classes[klass].platform_capabilities, len(CAT.capabilities.capabilities)
    )  # type: ignore[index]


def test_the_planner_turns_candidates_into_scope_free_platform_topics() -> None:
    inp = tiny(
        ("Reminders", "Send a push notification"),
        ("Pay", "Pay online at checkout"),
        load_class="deep",
    )
    plan = build_plan(inp, CAT)
    topics = [t for t in (*plan.targets, *plan.unrun) if t.job == "platform"]
    assert {t.label for t in topics} == {
        "Platform rules: push notifications",
        "Platform rules: in-app payments and store purchase rules",
    }
    for t in topics:
        assert t.lane == "integrations" and "verify" in t.also_lanes
        assert t.place is None or t.place.text == "", "no city or country: platform docs are global"
        assert t.decision_unlocked and t.slots and t.slots[0].kind == "platform_constraint"
        assert t.links.features, "linked to the Brief features that implied it"


def test_the_foreach_is_data_driven_and_unknown_foreach_still_returns_nothing() -> None:
    inp = tiny(("Reminders", "Send a push notification"))
    assert candidates_for("capability", inp, CAT) == capability_candidates(inp, CAT)
    assert candidates_for("no_such_foreach", inp, CAT) == []
