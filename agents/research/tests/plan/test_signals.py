"""RC-8: capability signals, the trust job, light mode (doc 13 4.1 and 4.2). The word lists are data; no brand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_research_agent.catalog import load_catalog
from mde_research_agent.domain.input import Feature, ResearchInput
from mde_research_agent.intake import read_export
from mde_research_agent.plan.build import build_plan
from mde_research_agent.plan.signals import compute_signals, light_mode, trust_decision

CAT = load_catalog()
FIX = Path(__file__).resolve().parent.parent / "fixtures"


def inp_of(name: str) -> ResearchInput:
    for folder in ("exports", "synthetic"):
        p = FIX / folder / f"{name}.json"
        if p.exists():
            return read_export(json.loads(p.read_text(encoding="utf-8")), CAT)
    raise FileNotFoundError(name)


def tiny(*features: tuple[str, str]) -> ResearchInput:
    base = inp_of("unit-converter-tiny")
    return base.model_copy(
        update={"features": tuple(Feature(id=f"FTR-{i:03d}", name=n, intent=t) for i, (n, t) in enumerate(features, 1))}
    )


@pytest.mark.parametrize(
    ("name", "trust"),
    [
        ("quick-yoga-studio-bengaluru", False),  # payments alone never triggers trust
        ("coffee-loyalty-nairobi-sw-en", False),
        ("standard-produce-marketplace-nashik", True),  # payments together with two-sided money
        ("dental-clinic-dubai-ar-en", True),  # health data
        ("thorough-freight-fleet-india", True),  # deep always
        ("unit-converter-tiny", False),
    ],
)
def test_trust_follows_content_not_size(name: str, trust: bool) -> None:
    inp = inp_of(name)
    sig = compute_signals(inp, CAT.signals)
    on, why = trust_decision(inp, sig, CAT)
    assert on is trust and why
    plan = build_plan(inp, CAT)
    assert plan.signals == sig
    jobs = {t.job for t in [*plan.targets, *plan.unrun]}
    assert ("trust" in jobs) is (trust and not light_mode(inp, sig, CAT))


def test_payments_alone_is_a_signal_but_not_a_trust_trigger() -> None:
    inp = inp_of("coffee-loyalty-nairobi-sw-en")
    sig = compute_signals(inp, CAT.signals)
    assert "payments" in sig and "two_sided" not in sig and not trust_decision(inp, sig, CAT)[0]


def test_loose_words_no_longer_make_an_integration_need() -> None:
    sig = compute_signals(tiny(("Order id on the list", "Show the order id and a buy button"), ("Sign here", "Sign the note")), CAT.signals)
    assert "identity_kyc" not in sig and "payments" not in sig
    strong = compute_signals(tiny(("Pay online", "Take a card payment at checkout")), CAT.signals)
    assert "payments" in strong


def test_a_weak_word_needs_two_different_places() -> None:
    one = compute_signals(tiny(("Reminders", "Remind people")), CAT.signals)
    two = compute_signals(tiny(("Reminders", "Remind people"), ("Alerts", "Send an alert by email")), CAT.signals)
    assert "messaging" not in one and "messaging" in two


def test_the_tiny_converter_is_light_and_plans_two_topics_with_visible_gaps() -> None:
    inp = inp_of("unit-converter-tiny")
    sig = compute_signals(inp, CAT.signals)
    assert sig == () and light_mode(inp, sig, CAT)
    plan = build_plan(inp, CAT)
    assert plan.light and len(plan.targets) == 2
    assert {f"{t.job}:{t.template_id}" for t in plan.targets} == set(CAT.signals.light.templates)
    assert plan.dropped and all(d.reason == "trivia" for d in plan.dropped if d.reason != "declined")


def test_real_exports_keep_their_load_classes() -> None:
    got = {n: inp_of(n).load for n in ("quick-yoga-studio-bengaluru", "standard-produce-marketplace-nashik", "thorough-freight-fleet-india")}
    assert [(g.score, g.load_class) for g in got.values()] == [(17, "focused"), (27, "standard"), (83, "deep")]


def test_the_design_job_requires_checkable_facts_and_leaves_conventions_optional() -> None:
    design = CAT.jobs["design"]
    by_id = {t.id: t for t in design.targets}
    assert all(s.required for s in by_id["platform_guidelines"].slots) and by_id["platform_guidelines"].slots[0].min == 3
    assert by_id["accessibility"].slots[0].required and by_id["accessibility"].slots[0].min == 2
    assert not any(s.required for s in by_id["conventions"].slots)
    key = next(t for t in CAT.jobs["screens"].targets if t.id == "key_screens").slots[0]
    assert key.min == 3 and key.deep_min == 5


def test_trust_job_is_priority_45_all_optional_and_conditional() -> None:
    trust = CAT.jobs["trust"]
    assert set(trust.kinds) == {"compliance_note", "rule", "service_option"}
    assert all(t.priority <= 45 and "trust" in t.when for t in trust.targets)
    assert not any(s.required for t in trust.targets for s in t.slots)
