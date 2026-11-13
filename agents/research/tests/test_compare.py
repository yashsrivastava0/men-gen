"""The quality gate (compare.py): what it passes, what it fails, and the floor test."""

from typing import Any

from mde_research_agent.compare import FLOOR_SHARE, compare, floor_test, render


def _run(
    items: list[tuple[str, str, str, str]],
    *,
    filled: int,
    required: int = 4,
    number_rejections: int = 0,
    extracted: int = 20,
    usd: int = 100_000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``items`` are ``(id, slot, evidence, norm_key)``."""
    research = {
        "status": "complete",
        "items": [
            {
                "id": i,
                "slot": s,
                "kind": "competitor",
                "label": k,
                "evidence": e,
                "independent_domains": 1,
            }
            for i, s, e, k in items
        ],
        "coverage": {"slots_filled": filled, "slots_required": required},
        "dropped": {"number_not_in_quote": number_rejections},
        "budget": {"searches_used": 10, "usd_micros": usd},
    }
    evidence = {
        "extraction": {"extracted": extracted},
        "atoms": [{"item_id": i, "norm_key": k, "verdict": "supported"} for i, _, _, k in items],
        "pages": [{}, {}],
    }
    return research, evidence


OLD = _run(
    [
        ("RI-1", "competitors", "official", "mindbody"),
        ("RI-2", "competitors", "single_source", "classpass"),
    ],
    filled=1,
)


def test_an_equal_run_passes_and_a_better_cheaper_run_passes() -> None:
    assert compare(OLD, OLD)["passed"]
    better = _run(
        [
            ("RI-1", "competitors", "official", "mindbody studio"),
            ("RI-2", "competitors", "official", "classpass"),
            ("RI-3", "competitors", "single_source", "momoyoga"),
        ],
        filled=2,
        usd=40_000,
    )
    res = compare(OLD, better)
    assert res["passed"], res["failed"]
    assert res["new"]["micros_per_slot_filled"] < res["old"]["micros_per_slot_filled"]


def test_each_gate_measure_fails_on_its_own() -> None:
    fewer_slots = _run(
        [
            ("RI-1", "competitors", "official", "mindbody"),
            ("RI-2", "competitors", "single_source", "x"),
        ],
        filled=0,
    )
    assert "required slots filled fell" in compare(OLD, fewer_slots)["failed"][0]
    less_official = _run(
        [
            ("RI-1", "competitors", "single_source", "mindbody"),
            ("RI-2", "competitors", "single_source", "x"),
        ],
        filled=1,
    )
    assert any("official share" in f for f in compare(OLD, less_official)["failed"])
    more_numbers = _run(
        [
            ("RI-1", "competitors", "official", "mindbody"),
            ("RI-2", "competitors", "single_source", "x"),
        ],
        filled=1,
        number_rejections=3,
    )
    assert any("number-rejection" in f for f in compare(OLD, more_numbers)["failed"])


def test_the_floor_test_wants_every_strong_pair_back() -> None:
    gone = _run(
        [
            ("RI-9", "competitors", "official", "momoyoga"),
            ("RI-2", "competitors", "single_source", "x"),
        ],
        filled=1,
    )
    f = floor_test(OLD, gone)
    assert f["old_strong"] == 1 and f["reappear"] == 0 and f["share"] < FLOOR_SHARE
    assert any("floor test" in x for x in compare(OLD, gone)["failed"])


def test_the_report_names_the_gate_and_the_sample_size() -> None:
    text = render(compare(OLD, OLD))
    assert "GATE: PASSED" in text and "1 vs 1" in text
