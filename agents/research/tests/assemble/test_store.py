"""RC-1: nothing is deleted to fit a size; items above the store limit move to the appendix, protected ones stay."""

from __future__ import annotations

from typing import Any

from support.testing import load_fixture, run_engine

from mde_research_agent.assemble.store import (
    APPENDIX_REASON,
    assign_store,
    chunk_ids,
    compact_item,
    compact_view,
    is_protected,
    rank_key,
    tokens_of,
)

NOTES = {
    "rule": "Rule with a value: confirm it before relying on it.",
    "competitor": "Comparable app.",
}


def item(i: int, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"RI-{i:03d}",
        "job": "features",
        "kind": "competitor",
        "label": f"Label {i}",
        "claim": "A claim " * 12,
        "impact": "normal",
        "evidence": "single_source",
        "tier": "default",
        "confidence": "medium",
        "use_note": "",
        "flags": [],
        "applies_to": [{"kind": "NEW", "id": None, "relation": "suggests"}],
        "sources": [{"id": "S-001"}],
        "independent_domains": 1,
        "data": {"name": f"app {i}"},
        "prevalence": {"seen_in": 1, "of": 5, "level": "limited_evidence"},
    }
    base.update(kw)
    return base


def test_a_brief_linked_high_impact_limited_evidence_rule_stays_while_a_generic_core_item_goes() -> (
    None
):
    linked_rule = item(
        1,
        kind="rule",
        impact="high",
        applies_to=[{"kind": "FTR", "id": "FTR-001", "relation": "constrains"}],
        prevalence={"seen_in": 1, "of": 9, "level": "limited_evidence"},
        data={"statement": "x " * 40, "subject": "fee"},
    )
    generic = [item(i, prevalence={"seen_in": 9, "of": 9, "level": "core"}) for i in range(2, 40)]
    items = [linked_rule, *generic]
    plan = assign_store(
        items,
        overhead_tokens=0,
        store_tokens=tokens_of(compact_item(linked_rule, NOTES)) + 300,
        default_notes=NOTES,
    )
    d = plan.decisions
    assert d["RI-001"].store == "main" and d["RI-001"].rank == 1
    assert sum(1 for x in d.values() if x.store == "appendix") > 10
    assert all(x.appendix_reason == APPENDIX_REASON for x in d.values() if x.store == "appendix")
    assert len(d) == len(items)  # nothing is deleted: every item has a decision


def test_the_best_two_of_every_kind_and_flagged_items_always_stay() -> None:
    items = [item(i) for i in range(1, 30)] + [
        item(100, kind="workflow", data={"name": "n"}),
        item(101, kind="workflow", data={"name": "m"}),
        item(102, kind="workflow", data={"name": "o"}),
    ]
    items.append(item(200, flags=["conflict"], evidence="unverified"))
    plan = assign_store(items, overhead_tokens=0, store_tokens=50, default_notes=NOTES)
    main = {k for k, d in plan.decisions.items() if d.store == "main"}
    assert "RI-200" in main  # a conflict is protected
    assert len({k for k in main if k.startswith("RI-1")}) >= 2 or {"RI-100", "RI-101"} <= main
    assert (
        plan.protected_over_limit
    )  # the protected set alone is over the tiny limit: nothing moved, the caller warns


def test_when_everything_fits_nothing_goes_to_the_appendix_and_the_order_is_by_rank() -> None:
    items = [item(i) for i in range(1, 6)]
    plan = assign_store(items, overhead_tokens=100, store_tokens=40_000, default_notes=NOTES)
    assert all(d.store == "main" for d in plan.decisions.values())
    ranks = sorted((d.rank, k) for k, d in plan.decisions.items())
    assert [k for _, k in ranks] == sorted(plan.decisions)  # equal items rank by id


def test_rank_prefers_brief_linked_then_high_impact_then_evidence_with_level_only_as_a_tie_break() -> (
    None
):
    linked = item(
        1,
        applies_to=[{"kind": "FTR", "id": "FTR-1", "relation": "expects"}],
        evidence="single_source",
    )
    high = item(2, impact="high", evidence="single_source")
    official = item(
        3, evidence="official", prevalence={"seen_in": 1, "of": 9, "level": "limited_evidence"}
    )
    core = item(4, evidence="single_source", prevalence={"seen_in": 9, "of": 9, "level": "core"})
    ordered = sorted([core, official, high, linked], key=rank_key)
    assert [i["id"] for i in ordered] == ["RI-001", "RI-002", "RI-003", "RI-004"]


def test_every_verified_item_ranks_before_any_unverified_one() -> None:
    """P6: unverified is last of all, so the appendix takes it first, even when it is Brief-linked and high impact."""
    linked_unverified = item(
        1,
        impact="high",
        applies_to=[{"kind": "FTR", "id": "FTR-1", "relation": "expects"}],
        evidence="unverified",
    )
    plain_single = item(2, evidence="single_source")
    plain_unverified = item(3, evidence="unverified")
    ordered = sorted([plain_unverified, linked_unverified, plain_single], key=rank_key)
    assert [i["id"] for i in ordered] == ["RI-002", "RI-001", "RI-003"]


def test_an_unverified_item_is_not_protected_unless_it_is_a_conflict_or_needs_confirming() -> None:
    link = [{"kind": "FTR", "id": "FTR-1", "relation": "constrains"}]
    assert is_protected(item(1, kind="rule", impact="high", applies_to=link, evidence="official"))
    assert not is_protected(
        item(2, kind="rule", impact="high", applies_to=link, evidence="unverified")
    )
    assert is_protected(
        item(3, kind="rule", applies_to=link, evidence="unverified", flags=["conflict"])
    )
    assert is_protected(item(4, evidence="unverified", flags=["needs-confirm"]))


def test_under_a_tight_limit_the_appendix_takes_unverified_items_before_verified_ones() -> None:
    link = [{"kind": "FTR", "id": "FTR-1", "relation": "constrains"}]
    verified = [
        item(i, kind="rule", impact="high", applies_to=link, evidence="single_source")
        for i in range(1, 6)
    ]
    weak = [
        item(i, kind="rule", impact="high", applies_to=link, evidence="unverified")
        for i in range(6, 11)
    ]
    budget = sum(tokens_of(compact_item(v, NOTES)) for v in verified) + 5
    plan = assign_store(
        [*weak, *verified], overhead_tokens=0, store_tokens=budget, default_notes=NOTES
    )
    main = {k for k, d in plan.decisions.items() if d.store == "main"}
    assert {v["id"] for v in verified} <= main, "the verified items stay"
    assert not {w["id"] for w in weak} & main, "the unverified ones went to the appendix first"
    assert not plan.protected_over_limit


def test_the_best_two_of_a_kind_are_verified_ones() -> None:
    items = [
        item(1, evidence="unverified"),
        item(2, evidence="unverified"),
        item(3, evidence="official"),
    ]
    plan = assign_store(items, overhead_tokens=0, store_tokens=1, default_notes=NOTES)
    main = {k for k, d in plan.decisions.items() if d.store == "main"}
    assert main == {"RI-003"}


def test_a_protected_rule_needs_a_brief_link() -> None:
    assert not is_protected(item(1, kind="rule"))
    assert is_protected(
        item(1, kind="rule", applies_to=[{"kind": "FTR", "id": "FTR-1", "relation": "constrains"}])
    )
    assert is_protected(item(1, flags=["needs-confirm"]))


def test_the_compact_view_keeps_ids_and_is_smaller_than_the_full_items() -> None:
    full = item(
        1,
        use_note="Comparable app.",
        sources=[
            {
                "id": "S-001",
                "domain": "a.example",
                "source_class": "official",
                "url": "https://a.example/x",
                "retrieved_on": "2026-11-11",
                "published_on": None,
            }
        ],
    )
    c = compact_item(full, NOTES)
    assert c["id"] == "RI-001" and "use_note" not in c  # the kind's default note is dropped
    assert c["sources"] == ["S-001"] and c["prevalence"] == "1/5 limited_evidence"
    assert tokens_of(c) < tokens_of(full)
    research = {
        "schema": "research.v5",
        "status": "complete",
        "items": [full, item(2, store="appendix", appendix_reason=APPENDIX_REASON)],
        "profile": {},
    }
    view = compact_view(research, NOTES)
    assert [i["id"] for i in view["items"]] == ["RI-001"] and view["appendix_items"] == 1
    assert set(view["sources"]) == {"S-001"} and view["sources"]["S-001"]["domain"] == "a.example"


async def test_a_small_store_limit_moves_items_to_the_appendix_and_conserves_every_count() -> None:
    run = await run_engine(load_fixture("standard-produce-marketplace-nashik"), store_tokens=1500)
    res = run.research
    items = res["items"]
    verified = [a for a in run.evidence["atoms"] if a["status"] == "verified" and a.get("item_id")]
    assert len(items) <= len(verified) and items
    appendix = [i for i in items if i["store"] == "appendix"]
    assert appendix and all(i["appendix_reason"] for i in appendix)
    assert res["profile"]["appendix_items"] == len(appendix)
    assert "size_cap" not in res["dropped"]
    main_ids = {i["id"] for i in items if i["store"] == "main"}
    for sl in res["slices"].values():
        assert set(sl["item_ids"]) <= main_ids  # a lane lists main-store items only
        assert sl["item_ids"] == [i for c in sl["chunks"] for i in c]
    assert [i["rank"] for i in sorted(items, key=lambda i: i["rank"])] == list(
        range(1, len(items) + 1)
    )
    assert not [v for v in run.violations if v.rule in ("RV-09", "RV-22")]


async def test_without_a_limit_problem_the_whole_output_is_main_store_and_slices_split_by_chunk_items() -> (
    None
):
    run = await run_engine(load_fixture("quick-yoga-studio-bengaluru"))
    assert all(i["store"] == "main" and i["tokens_est"] > 0 for i in run.research["items"])
    assert (
        run.research["profile"]["store_tokens"] > 0
        and run.research["profile"]["appendix_items"] == 0
    )


# --- chunking by lane: never truncate ------------------------------------------------------------------------------------------


def test_chunks_split_a_big_lane_at_chunk_items_and_join_back_to_the_whole_list() -> None:
    ids = [f"RI-{i:04d}" for i in range(1, 481)]  # a Big-sized lane: 480 items
    chunks = chunk_ids(ids, 120)
    assert [len(c) for c in chunks] == [120, 120, 120, 120]
    assert [i for c in chunks for i in c] == ids, "nothing is cut, nothing is reordered"
    odd = chunk_ids(ids[:301], 120)
    assert [len(c) for c in odd] == [120, 120, 61] and [i for c in odd for i in c] == ids[:301]
    assert chunk_ids([], 120) == [] and chunk_ids(ids[:1], 120) == [["RI-0001"]]


def test_a_chunk_size_below_one_is_refused() -> None:
    import pytest

    with pytest.raises(ValueError):
        chunk_ids(["a"], 0)


async def test_every_lane_of_a_deep_run_is_chunked_whole() -> None:
    run = await run_engine(load_fixture("thorough-freight-fleet-india"))
    r = run.research
    assert r["profile"]["load_class"] == "deep"
    size = run.ctx.catalog.ceilings.for_class("deep").chunk_items
    assert size == 120
    for lane, sl in r["slices"].items():
        assert sl["chunks"] == chunk_ids(sl["item_ids"], size), lane
        assert all(len(c) <= size for c in sl["chunks"]), lane
        assert [i for c in sl["chunks"] for i in c] == sl["item_ids"], (
            f"{lane}: chunks join back to the lane"
        )
    assert run.violations == []


async def test_a_big_run_with_a_small_chunk_size_splits_every_lane_and_cuts_nothing() -> None:
    """The real assembly path with several chunks per lane (the fake web gives ~110 items, so the size is lowered)."""
    run = await run_engine(load_fixture("thorough-freight-fleet-india"), chunk_items=20)
    r = run.research
    split = 0
    for lane, sl in r["slices"].items():
        assert all(len(c) <= 20 for c in sl["chunks"]), lane
        assert [i for c in sl["chunks"] for i in c] == sl["item_ids"], (
            f"{lane}: chunks join back to the lane"
        )
        split += len(sl["chunks"]) > 1
    assert split >= 3, "several lanes needed more than one chunk"
    main = {i["id"] for i in r["items"] if i["store"] == "main"}
    assert {i for sl in r["slices"].values() for i in sl["item_ids"]} <= main
    assert run.violations == [], "RV-09 (chunks add up to the lane list, none over the size) holds"
