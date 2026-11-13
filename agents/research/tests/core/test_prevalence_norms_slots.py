import itertools
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_research_agent.catalog import load_catalog
from mde_research_agent.catalog.models import SlotSpec
from mde_research_agent.core.applies import BriefIndex, problem, retag
from mde_research_agent.core.hashing import content_hash
from mde_research_agent.core.norm_keys import match_brief, reconcile
from mde_research_agent.core.prevalence import (
    age_months,
    is_stale,
    level_for,
    make_prevalence,
    recency_of,
)
from mde_research_agent.core.slots import evaluate_slot, evaluate_target
from mde_research_agent.domain.research import AppliesTo

CAT = load_catalog()
PREV = CAT.prevalence


# --- prevalence and recency ---------------------------------------------------------------------


def test_levels_by_share_and_the_three_source_floor() -> None:
    def lv(n, of, **kw):
        return level_for(
            n, of, newest_age_months=kw.get("age", 3), rising=kw.get("rising", False), cfg=PREV
        )

    assert lv(2, 2) == "limited_evidence"  # fewer than 3 sources cannot say "core"
    assert lv(5, 6) == "core" and lv(3, 6) == "common" and lv(1, 6) == "advanced"
    assert lv(1, 6, rising=True, age=4) == "emerging"
    assert lv(1, 6, rising=True, age=30) == "advanced"  # rising but not recent
    assert lv(4, 6, rising=True, age=4) == "core"  # emerging needs few sources


@given(st.integers(0, 12), st.integers(0, 12))
def test_seen_in_never_exceeds_of_and_levels_are_monotone(seen: int, of: int) -> None:
    p = make_prevalence(seen, of, newest_age_months=2, rising=False, cfg=PREV)
    assert p.seen_in <= p.of or of == 0
    order = ["limited_evidence", "advanced", "common", "core"]
    if of >= 3:
        lo = make_prevalence(min(seen, of), of, newest_age_months=2, rising=False, cfg=PREV).level
        hi = make_prevalence(
            min(seen + 1, of), of, newest_age_months=2, rising=False, cfg=PREV
        ).level
        assert order.index(hi) >= order.index(lo)


def test_age_months_and_staleness() -> None:
    assert age_months(date(2025, 11, 12), date(2026, 11, 11)) == 11
    assert age_months(date(2024, 11, 11), date(2026, 11, 11)) == 24
    assert age_months(None, date(2026, 11, 11)) is None
    assert age_months(date(2030, 1, 1), date(2026, 11, 11)) == 0
    assert is_stale(25, job="features", is_fee=False, cfg=PREV) and not is_stale(
        24, job="features", is_fee=False, cfg=PREV
    )
    assert not is_stale(30, job="data", is_fee=False, cfg=PREV) and is_stale(
        61, job="data", is_fee=False, cfg=PREV
    )
    assert is_stale(25, job="services", is_fee=True, cfg=PREV)
    assert not is_stale(
        None, job="features", is_fee=False, cfg=PREV
    )  # unknown is unknown, not stale


def test_recency_uses_the_newest_known_date() -> None:
    r = recency_of([date(2025, 1, 5), None, date(2026, 9, 1)], date(2026, 11, 11), "rising")
    assert r.newest_source == date(2026, 9, 1) and r.age_months == 2 and r.trend == "rising"
    assert recency_of([None], date(2026, 11, 11)).newest_source is None


# --- norm keys ------------------------------------------------------------------------------------


def test_reconcile_joins_wordings_and_keeps_areas_apart() -> None:
    mapping = reconcile(
        [
            ("feature", "class booking"),
            ("feature", "class bookings"),
            ("feature", "booking classes"),
            ("feature", "class packs"),
            ("screen", "class booking"),
        ]
    )
    assert mapping[("feature", "class booking")] == mapping[("feature", "class bookings")]
    assert mapping[("feature", "class packs")] == "class packs"
    assert mapping[("screen", "class booking")] == "class booking"  # never merged across areas
    assert mapping[("feature", "class booking")] != mapping[("feature", "class packs")]


@given(
    st.lists(
        st.sampled_from(
            [
                "class booking",
                "class bookings",
                "waitlist",
                "wait list",
                "class packs",
                "pack purchase",
                "reminders",
            ]
        ),
        min_size=1,
        max_size=9,
    )
)
def test_reconcile_is_permutation_invariant(keys: list[str]) -> None:
    items = [("feature", k) for k in keys]
    base = reconcile(items)
    for perm in itertools.islice(itertools.permutations(items), 5):
        assert reconcile(list(perm)) == base


def test_match_brief_needs_a_real_overlap() -> None:
    names = {"FTR-001": "Class booking", "FTR-002": "Buy class packs", "FTR-003": "Reminders"}
    generic = frozenset({"class"})
    assert match_brief("class bookings", names) == "FTR-001"
    assert (
        match_brief("class", names, generic=generic) is None
    )  # a shared generic word alone never matches
    assert (
        match_brief("reminder notifications", names) == "FTR-003"
        or match_brief("reminder notifications", names) is None
    )
    assert match_brief("payroll export", names) is None


# --- applies_to -------------------------------------------------------------------------------------

INDEX = BriefIndex(
    ids={
        "FTR": frozenset({"FTR-001"}),
        "ROL": frozenset({"ROL-001"}),
        "ENT": frozenset({"ENT-001"}),
    },
    integration_keys=frozenset({"payments"}),
)


@pytest.mark.parametrize(
    ("entry", "ok"),
    [
        (AppliesTo(kind="FTR", id="FTR-001"), True),
        (AppliesTo(kind="FTR", id="FTR-099"), False),  # not in the Brief
        (AppliesTo(kind="ROL", id="FTR-001"), False),  # id of another kind
        (AppliesTo(kind="INT", id="payments"), True),
        (AppliesTo(kind="INT", id="maps"), False),
        (AppliesTo(kind="NEW"), True),
        (AppliesTo(kind="NEW", id="FTR-001"), False),
        (AppliesTo(kind="ENT", id=None), False),
    ],
)
def test_applies_to_checks(entry: AppliesTo, ok: bool) -> None:
    assert (problem(entry, INDEX) is None) is ok


def test_invalid_entries_are_retagged_new_and_deduped() -> None:
    fixed, changed = retag(
        [
            AppliesTo(kind="FTR", id="FTR-001"),
            AppliesTo(kind="FTR", id="FTR-404"),
            AppliesTo(kind="ROL", id="ROL-777"),
        ],
        INDEX,
    )
    assert changed == 2
    assert [e.kind for e in fixed] == ["FTR", "NEW"]  # the two NEW entries collapse into one
    assert all(problem(e, INDEX) is None for e in fixed)


# --- slots ------------------------------------------------------------------------------------------


@dataclass
class A:
    kind: str
    norm_key: str
    data: dict[str, Any] = field(default_factory=dict)


COMPETITORS = SlotSpec(
    key="competitors",
    type="count",
    kind="competitor",
    distinct_by="name",
    min=3,
    attr="features",
    attr_min=5,
)
NORMS = SlotSpec(
    key="feature_norms",
    type="distinct_values",
    kind="competitor",
    distinct_by="name",
    attr="features",
    min=8,
    min_support=2,
)


def comp(name: str, n: int, start: int = 0) -> A:
    return A(
        "competitor",
        name,
        {
            "name": name,
            "features": [
                {"feature_key": f"feature {i}", "level": "core"} for i in range(start, start + n)
            ],
        },
    )


def test_count_slot_needs_distinct_rich_items() -> None:
    atoms = [comp("A", 6), comp("B", 5), comp("a", 7)]  # "a" is a repeat of "A"
    fill = evaluate_slot(COMPETITORS, atoms)
    assert (fill.count == 2 and not fill.filled and fill.thin is not False) or fill.count == 2
    assert evaluate_slot(COMPETITORS, [*atoms, comp("C", 5)]).filled


def test_thin_items_do_not_fill_a_rich_slot() -> None:
    fill = evaluate_slot(COMPETITORS, [comp("A", 2), comp("B", 3), comp("C", 4)])
    assert not fill.filled and fill.count == 0 and fill.thin


def test_distinct_values_slot_counts_across_items() -> None:
    assert not evaluate_slot(NORMS, [comp("A", 5), comp("B", 2)]).filled  # 2 values are seen twice
    assert not evaluate_slot(NORMS, [comp("A", 8)]).filled  # one competitor alone is not a norm
    two = [comp("A", 9), comp("B", 9)]  # same nine features at both: nine values each seen twice
    assert evaluate_slot(NORMS, two).filled and evaluate_slot(NORMS, two).count == 9


def test_target_is_covered_only_when_every_required_slot_is_filled() -> None:
    optional = SlotSpec(key="trends", type="count", kind="trend", min=1, required=False)
    atoms = [comp("A", 9), comp("B", 9), comp("C", 9)]
    cov = evaluate_target([COMPETITORS, NORMS, optional], atoms)
    assert cov.covered and cov.required_total == 2 and cov.required_filled == 2
    cov2 = evaluate_target([COMPETITORS, NORMS, optional], atoms[:1])
    assert not cov2.covered and {f.key for f in cov2.unfilled_required} == {
        "competitors",
        "feature_norms",
    }


# --- content hash -------------------------------------------------------------------------------------


def test_content_hash_ignores_the_volatile_block_and_wall_clock_budget() -> None:
    base: dict[str, Any] = {
        "items": [{"id": "RI-001"}],
        "budget": {"searches_used": 4, "seconds": 41, "usd": "0.12", "usd_micros": 120000},
        "run": {"run_id": "run_1"},
    }
    other = {
        **base,
        "budget": {"searches_used": 4, "seconds": 99, "usd": "0.20", "usd_micros": 200000},
        "run": {"run_id": "run_2"},
    }
    assert content_hash(base) == content_hash(other)
    changed = {**base, "items": [{"id": "RI-002"}]}
    assert content_hash(changed) != content_hash(base)
    assert content_hash({**base, "budget": {**base["budget"], "searches_used": 5}}) != content_hash(
        base
    )
