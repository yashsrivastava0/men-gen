from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.domain.interview import FACT_TYPES, Fact
from mde_compass.engine.facts import (
    GENERAL_TOPIC,
    digest,
    digest_line,
    digest_report,
    dispute,
    fact_coverage,
    fact_number,
    group_by_topic,
    mark_not_used,
    supersede,
)

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)


def mk(n: int, **kw: object) -> Fact:
    base: dict[str, object] = {
        "id": f"FCT-{n:03d}",
        "type": "rule",
        "statement": f"statement number {n}",
        "topics": [],
        "confidence": 0.5,
    }
    base.update(kw)
    return Fact.model_validate(base)


# ------------------------------------------------------------------------------------ status changes
def test_supersede_keeps_both_and_links_them() -> None:
    old, new = mk(1, statement="Delivery is free"), mk(2, statement="Delivery costs 20 rupees")
    old2, new2 = supersede(old, new, now=NOW)
    assert old2.status == "superseded" and old2.updated_at == NOW
    assert new2.supersedes == ["FCT-001"] and new2.status == "active" and new2.created_at == NOW
    # Inputs are untouched and nothing is deleted.
    assert old.status == "active" and new.supersedes == []
    assert old2.statement == old.statement and old2.id == old.id


def test_supersede_is_idempotent_on_the_link_and_keeps_earlier_links() -> None:
    old, new = mk(1), mk(3, supersedes=["FCT-002"])
    _, once = supersede(old, new, now=NOW)
    _, twice = supersede(old, once, now=NOW + timedelta(seconds=1))
    assert once.supersedes == ["FCT-002", "FCT-001"] == twice.supersedes


def test_supersede_keeps_an_existing_created_at() -> None:
    created = NOW - timedelta(hours=1)
    _, new = supersede(mk(1), mk(2, created_at=created), now=NOW)
    assert new.created_at == created


def test_supersede_self_is_refused() -> None:
    with pytest.raises(ValueError):
        supersede(mk(1), mk(1), now=NOW)


def test_dispute_marks_both() -> None:
    a, b = dispute(mk(1), mk(2), now=NOW)
    assert a.status == b.status == "disputed" and a.updated_at == NOW


def test_dispute_does_not_revive_superseded_or_not_used() -> None:
    a, b = dispute(
        mk(1, status="superseded"), mk(2, status="not_used", not_used_reason="x"), now=NOW
    )
    assert a.status == "superseded" and b.status == "not_used"


def test_dispute_then_supersede_resolves() -> None:
    a, b = dispute(mk(1), mk(2), now=NOW)
    a2, b2 = supersede(a, b, now=NOW)
    assert (
        a2.status == "superseded" and b2.status == "disputed"
    )  # the caller re-activates the winner


def test_mark_not_used_needs_a_reason() -> None:
    f = mark_not_used(mk(1), "  small talk ", NOW)
    assert f.status == "not_used" and f.not_used_reason == "small talk" and f.updated_at == NOW
    with pytest.raises(ValueError):
        mark_not_used(mk(1), "   ", NOW)
    with pytest.raises(ValueError):
        mark_not_used(mk(1, status="superseded"), "why", NOW)


# --------------------------------------------------------------------------------------------- lines
def test_digest_line_format() -> None:
    f = mk(
        14, type="rule", topics=["pricing"], statement="Tiffin costs 80 rupees", qa_card_id="Q-03"
    )
    assert digest_line(f) == "FCT-014 [rule|pricing] Tiffin costs 80 rupees (from Q-03)"


def test_digest_line_variants() -> None:
    assert digest_line(mk(1, topics=[])) == "FCT-001 [rule] statement number 1"
    two = mk(2, topics=["pricing", "delivery"], source_ref="SRC-001:p3")
    assert (
        digest_line(two) == "FCT-002 [rule|pricing,delivery] statement number 2 (from SRC-001:p3)"
    )
    assert digest_line(mk(3, statement="a\n  b\tc")).endswith("a b c")
    tap = mk(4, qa_card_id="Q-02", source_ref="evt_9")
    assert digest_line(tap).endswith("(from Q-02)")  # the question wins over the raw source ref


def test_grouping_is_alphabetical_with_general_last() -> None:
    facts = [
        mk(1, topics=["pricing"]),
        mk(2, topics=[]),
        mk(3, topics=["delivery"]),
        mk(4, topics=["pricing"]),
    ]
    groups = group_by_topic(facts)
    assert [g for g, _ in groups] == ["delivery", "pricing", GENERAL_TOPIC]
    assert [f.id for f in groups[1][1]] == ["FCT-001", "FCT-004"]


# -------------------------------------------------------------------------------------------- digest
def test_digest_lists_all_active_facts_grouped() -> None:
    facts = [
        mk(1, topics=["pricing"]),
        mk(2, status="superseded"),
        mk(3, topics=["delivery"]),
        mk(4, status="disputed"),
        mk(5, status="not_used", not_used_reason="small talk"),
    ]
    report = digest_report(facts, budget_tokens=500)
    assert report.kept == ("FCT-001", "FCT-003") and report.dropped == ()
    assert report.text.splitlines() == [
        "# delivery",
        "FCT-003 [rule|delivery] statement number 3",
        "# pricing",
        "FCT-001 [rule|pricing] statement number 1",
    ]
    assert digest(facts, topics=[], budget_tokens=500) == report.text


def test_digest_empty() -> None:
    assert digest([], budget_tokens=100) == ""


def _many(n: int) -> list[Fact]:
    topics = ["pricing", "delivery", "menu", "staff"]
    return [
        mk(i, topics=[topics[i % 4]], confidence=round(0.3 + (i % 7) / 10, 2), statement="x" * 60)
        for i in range(1, n + 1)
    ]


def test_over_budget_keeps_relevant_topics_first() -> None:
    facts = _many(40)
    budget_tokens = 150  # about 600 characters: room for roughly eight lines
    report = digest_report(facts, topics=["Pricing"], budget_tokens=budget_tokens)
    assert len(report.text) <= budget_tokens * 4
    assert report.dropped and report.dropped_count == len(report.dropped)
    kept = [f for f in facts if f.id in report.kept]
    assert any(f.topics == ["pricing"] for f in kept)
    # Every relevant fact that is dropped ranks below every relevant fact that is kept.
    relevant = [f for f in facts if f.topics == ["pricing"]]
    kept_rel = [f for f in relevant if f.id in report.kept]
    dropped_rel = [f for f in relevant if f.id in report.dropped]
    assert kept_rel
    if dropped_rel:
        assert min(f.confidence for f in kept_rel) >= max(f.confidence for f in dropped_rel)
        # and no irrelevant fact is kept while a relevant one was dropped
        assert all(f.topics == ["pricing"] for f in kept)
    assert f"+{report.dropped_count} more facts omitted" in report.text


def test_over_budget_keeps_everything_for_relevant_topics_when_they_fit() -> None:
    facts = _many(40)
    relevant_ids = {f.id for f in facts if f.topics == ["staff"]}
    report = digest_report(facts, topics=["staff"], budget_tokens=400)
    assert relevant_ids <= set(report.kept)
    assert report.dropped  # the rest did not all fit


def test_dropped_facts_prefer_high_confidence_then_recent() -> None:
    facts = [
        mk(1, confidence=0.9, statement="a" * 50),
        mk(2, confidence=0.9, statement="b" * 50),
        mk(3, confidence=0.2, statement="c" * 50),
    ]
    one_line = (
        len(digest_line(facts[0])) + len("# general\n") + 60
    )  # room for one line plus the note
    report = digest_report(facts, budget_tokens=one_line // 4 + 1)
    assert report.kept == ("FCT-002",)  # same confidence: the more recent wins
    assert set(report.dropped) == {"FCT-001", "FCT-003"}


def test_budget_zero_and_tiny_budget_never_overflow() -> None:
    facts = _many(5)
    assert digest(facts, budget_tokens=0) == ""
    assert len(digest(facts, budget_tokens=3)) <= 12


@settings(max_examples=60)
@given(
    n=st.integers(0, 60),
    budget=st.integers(0, 400),
    cpt=st.integers(1, 6),
    relevant=st.lists(
        st.sampled_from(["pricing", "delivery", "menu", "staff", "none"]), max_size=3
    ),
)
def test_digest_never_exceeds_budget_and_is_deterministic(
    n: int, budget: int, cpt: int, relevant: list[str]
) -> None:
    facts = _many(n)
    first = digest_report(facts, topics=relevant, budget_tokens=budget, chars_per_token=cpt)
    second = digest_report(
        list(reversed(facts)), topics=relevant, budget_tokens=budget, chars_per_token=cpt
    )
    assert len(first.text) <= budget * cpt
    assert first == second  # input order does not change the output
    assert set(first.kept) | set(first.dropped) == {f.id for f in facts}
    assert not set(first.kept) & set(first.dropped)


# ------------------------------------------------------------------------------------------ coverage
def test_coverage_rules() -> None:
    facts = [
        mk(1),  # used
        mk(2),  # unreferenced: a defect
        mk(3, status="not_used", not_used_reason="small talk"),  # fine
        mk(4, status="not_used", not_used_reason=""),  # not_used without a reason: a defect
        mk(5, status="superseded"),  # needs no coverage
        mk(6, status="disputed"),  # needs no coverage
        mk(7, status="superseded"),  # referenced anyway: stale
    ]
    report = fact_coverage(facts, {"FCT-001", "FCT-007", "FCT-099", "SRC-001:p1"})
    assert report.used == ("FCT-001",)
    assert report.not_used == ("FCT-003",)
    assert report.unreferenced == ("FCT-002", "FCT-004")
    assert report.stale == ("FCT-007",)
    assert report.unknown == ("FCT-099",)  # a non-FCT ref is not our business
    assert not report.ok
    assert report.ratio == pytest.approx(2 / 4)


def test_coverage_ok_and_empty() -> None:
    ok = fact_coverage([mk(1), mk(2, status="not_used", not_used_reason="small talk")], ["FCT-001"])
    assert ok.ok and ok.ratio == 1.0
    empty = fact_coverage([], [])
    assert empty.ok and empty.ratio == 1.0


def test_fact_number() -> None:
    assert fact_number("FCT-014") == 14 and fact_number("garbage") == 0


@settings(max_examples=40)
@given(
    statuses=st.lists(
        st.sampled_from(["active", "superseded", "disputed", "not_used"]), max_size=15
    ),
    pick=st.lists(st.integers(0, 14), max_size=15),
    types=st.lists(st.sampled_from(FACT_TYPES), min_size=15, max_size=15),
)
def test_coverage_partition_property(
    statuses: list[str], pick: list[int], types: list[str]
) -> None:
    facts = [
        mk(i + 1, status=s, type=types[i], not_used_reason="r" if s == "not_used" else "")
        for i, s in enumerate(statuses)
    ]
    refs = {f"FCT-{i + 1:03d}" for i in pick}
    report = fact_coverage(facts, refs)
    must = {f.id for f in facts if f.status in ("active", "not_used")}
    assert set(report.used) | set(report.not_used) | set(report.unreferenced) == must
    assert report.ok == (not report.unreferenced and not report.stale and not report.unknown)
    assert not set(report.used) & set(report.unreferenced)
