"""Cross-check: merging, the two-independent-sources rule, deterministic ids."""

from __future__ import annotations

from datetime import date

from mde_contracts.research import SourceRef
from mde_research.crosscheck import (
    MergedFact,
    RawFact,
    build_facts,
    independent_domains,
    is_verified,
    max_fact_number,
    merge_raw_facts,
    similarity,
    source_ref,
    statement_tokens,
)

DAY = date(2026, 11, 8)


def src(url: str) -> SourceRef:
    return source_ref(url, None, DAY)


def raw(order: int, statement: str, url: str, **kw: object) -> RawFact:
    fields: dict[str, object] = {
        "job": "R1",
        "kind": "feature",
        "impact": "low",
        "confidence": "medium",
    }
    fields.update(kw)
    return RawFact(order=order, statement=statement, source=src(url), **fields)  # type: ignore[arg-type]


def test_source_ref_carries_url_host_and_retrieval_date() -> None:
    ref = source_ref("https://www.Example.com/a", "T", DAY)
    assert ref.url == "https://www.Example.com/a"
    assert ref.domain == "example.com"
    assert ref.retrieved_at == DAY
    assert ref.title == "T"


def test_independent_domains_counts_registrable_domains() -> None:
    same = [src("https://a.example.com/x"), src("https://b.example.com/y")]
    assert independent_domains(same) == 1
    other = [src("https://example.com/x"), src("https://example.org/y")]
    assert independent_domains(other) == 2
    uk = [
        src("https://a.example.co.uk/x"),
        src("https://b.example.co.uk/y"),
        src("https://example.com/z"),
    ]
    assert independent_domains(uk) == 2


def test_low_impact_needs_one_source_high_impact_needs_two_independent_domains() -> None:
    one = [src("https://example.com/x")]
    assert is_verified("low", one)
    assert not is_verified("high", one)
    assert not is_verified("high", [src("https://a.example.com/x"), src("https://b.example.com/y")])
    assert is_verified("high", [src("https://example.com/x"), src("https://example.org/y")])
    assert not is_verified("low", [])


def test_near_identical_statements_merge_and_union_sources() -> None:
    a = raw(0, "Reviews build trust in online shops", "https://example.com/a")
    b = raw(1, "Reviews build trust in online shops.", "https://example.org/b", confidence="high")
    c = raw(2, "A completely different statement about carts", "https://example.net/c")
    merged = merge_raw_facts([a, b, c])
    assert len(merged) == 2
    assert [s.url for s in merged[0].sources] == ["https://example.com/a", "https://example.org/b"]
    assert merged[0].confidence == "high"  # the better confidence wins


def test_similar_statements_merge_by_word_overlap() -> None:
    a = raw(0, "Most shops let buyers filter results by price and rating", "https://example.com/a")
    b = raw(1, "Most shops let buyers filter results by rating and price", "https://example.org/b")
    assert len(merge_raw_facts([a, b])) == 1


def test_different_jobs_are_never_merged() -> None:
    a = raw(0, "Reviews build trust in shops", "https://example.com/a", job="R1")
    b = raw(1, "Reviews build trust in shops", "https://example.org/b", job="R2")
    assert len(merge_raw_facts([a, b])) == 2


def test_merging_keeps_the_cautious_impact_and_dedupes_urls() -> None:
    a = raw(0, "Card data must stay with the provider", "https://example.com/a", impact="low")
    b = raw(1, "Card data must stay with the provider", "https://example.com/a#x", impact="high")
    (merged,) = merge_raw_facts([a, b])
    assert merged.impact == "high"
    assert len(merged.sources) == 1


def test_merge_is_order_independent_for_the_result_set() -> None:
    a = raw(0, "Reviews build trust in online shops", "https://example.com/a")
    b = raw(1, "Reviews build trust in online shops", "https://example.org/b")
    forward = merge_raw_facts([a, b])
    backward = merge_raw_facts([b, a])
    assert len(forward) == len(backward) == 1


def test_similarity_edge_cases() -> None:
    assert similarity(frozenset(), frozenset({"a"})) == 0.0
    assert similarity(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0
    assert statement_tokens("The shop and the cart") == frozenset({"shop", "cart"})


def test_ids_are_sequential_in_job_then_discovery_order() -> None:
    merged = merge_raw_facts(
        [
            raw(0, "Alpha statement about carts", "https://example.com/a", job="R4"),
            raw(1, "Beta statement about search", "https://example.com/b", job="R1"),
            raw(2, "Gamma statement about maps", "https://example.com/c", job="R1"),
        ]
    )
    facts = build_facts(merged)
    assert [(f.id, f.job) for f in facts] == [("F-001", "R1"), ("F-002", "R1"), ("F-003", "R4")]
    assert [f.statement.split()[0] for f in facts] == ["Beta", "Gamma", "Alpha"]


def test_ids_are_deterministic() -> None:
    items = [
        raw(0, "Alpha statement about carts", "https://example.com/a"),
        raw(1, "Beta statement about search", "https://example.com/b"),
    ]
    first = build_facts(merge_raw_facts(items))
    second = build_facts(merge_raw_facts(list(items)))
    assert first == second


def test_numbering_can_continue_after_kept_facts() -> None:
    facts = build_facts(
        merge_raw_facts([raw(0, "Alpha statement about carts", "https://example.com/a")]),
        first_number=7,
    )
    assert facts[0].id == "F-007"
    assert max_fact_number(facts) == 7
    assert max_fact_number([]) == 0


def test_unverified_high_impact_facts_get_low_confidence() -> None:
    merged = merge_raw_facts(
        [
            raw(
                0,
                "Card data must stay with the provider",
                "https://example.com/a",
                impact="high",
                confidence="high",
            ),
            raw(
                1,
                "Search is common in shops",
                "https://example.com/b",
                impact="low",
                confidence="high",
            ),
        ]
    )
    by_text = {f.statement.split()[0]: f for f in build_facts(merged)}
    assert by_text["Card"].verified is False
    assert by_text["Card"].confidence == "low"
    assert by_text["Search"].verified is True
    assert by_text["Search"].confidence == "high"


def test_corroborated_high_impact_fact_is_verified() -> None:
    merged = merge_raw_facts(
        [
            raw(0, "Card data must stay with the provider", "https://example.com/a", impact="high"),
            raw(1, "Card data must stay with the provider", "https://example.org/b", impact="high"),
        ]
    )
    (fact,) = build_facts(merged)
    assert fact.verified is True
    assert len(fact.sources) == 2
    assert {s.retrieved_at for s in fact.sources} == {DAY}


def test_every_fact_has_sources_with_a_retrieval_date() -> None:
    facts = build_facts(
        merge_raw_facts([raw(0, "Alpha statement about carts", "https://example.com/a")])
    )
    assert all(f.sources and all(s.url and s.retrieved_at for s in f.sources) for f in facts)
    assert (
        build_facts([MergedFact(order=0, job="R1", kind="other", statement="No sources here")])
        == []
    )


def test_fact_cap_drops_unverified_facts_first() -> None:
    items = [
        raw(0, "Unverified claim number one about law", "https://example.com/a", impact="high"),
        raw(1, "Verified claim two about carts", "https://example.com/b"),
        raw(2, "Verified claim three about maps", "https://example.com/c"),
    ]
    facts = build_facts(merge_raw_facts(items), max_facts=2)
    assert [f.statement.split()[0] for f in facts] == ["Verified", "Verified"]


def test_sources_are_sorted_for_stable_hashes() -> None:
    merged = merge_raw_facts(
        [
            raw(0, "Reviews build trust in online shops", "https://example.org/b"),
            raw(1, "Reviews build trust in online shops", "https://example.com/a"),
        ]
    )
    (fact,) = build_facts(merged)
    assert [s.url for s in fact.sources] == ["https://example.com/a", "https://example.org/b"]
