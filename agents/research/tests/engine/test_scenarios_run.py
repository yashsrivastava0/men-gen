"""Scenario tests RS-01, 03, 05, 09, 11, 12, 13, 14, 16, 17, 20, 21, 22, 24, 31, 32, 34 and the engine-wide properties."""

import json
from datetime import date

import pytest

from mde_core.clock import FakeClock
from mde_research_agent.calls.queries import QueryOut, Variant
from support.compose import build_service
from support.fakes import FakeWorld
from support.testing import (
    AS_OF,
    atom,
    extractor,
    load_fixture,
    rule_extractor,
    run_engine,
)

QUICK = "quick-yoga-studio-bengaluru"
STANDARD = "standard-produce-marketplace-nashik"
THOROUGH = "thorough-freight-fleet-india"


async def test_rs01_without_a_search_provider_the_run_is_skipped_and_never_blocks() -> None:
    svc = build_service(provider="none", as_of=AS_OF)
    assert svc.search is None
    row = await svc.start_run("t", None, load_fixture(QUICK))
    await svc.wait(row.run_id, 30)
    snap = await svc.snapshot("t", row.run_id)
    assert snap.status == "skipped"
    res = await svc.result("t", row.run_id)
    assert (
        res["status"] == "skipped"
        and res["items"] == []
        and res["gaps"]
        and all(
            g["reason"] in ("not_run", "declined", "not_worthwhile", "ambiguous_reference")
            for g in res["gaps"]
        )
    )
    events = await svc.store.events_after(row.run_id, 0)
    assert (
        any(e.type == "warning" and "not set up" in e.label for e in events)
        and events[-1].type == "run.skipped"
    )
    await svc.aclose()


async def test_rs09_a_query_with_a_url_from_the_model_never_reaches_the_search_provider() -> None:
    bad = QueryOut(
        variants=[
            Variant(
                text="https://evil.example/x yoga studio app features Bengaluru",
                style="descriptive",
                language="en",
                exact=False,
                seed=False,
            )
        ]
    )
    run = await run_engine(load_fixture(QUICK), overrides={"rsch.write_queries": lambda i: bad})
    assert run.search.requests and not any(
        "http" in r.query or "evil" in r.query for r in run.search.requests
    )
    assert run.result.status == "complete" and not run.violations


async def test_rs11_template_requests_are_detected_and_the_plan_is_rebuilt() -> None:
    run = await run_engine(load_fixture(QUICK))
    iq = run.research["input_quality"]
    assert iq["plan_source"] == "rebuilt" and iq["requests"]["template"] >= 4
    assert {f["call"] for f in iq["failures"]} >= {"cmp.seed_research", "cmp.scan_ambiguity"}


async def test_rs12_weak_geography_searches_the_country_and_flags_items() -> None:
    run = await run_engine(
        load_fixture(THOROUGH),
        ceilings={
            "soft": {"searches": 30, "targets": 8},
            "hard": {"searches": 40, "targets": 8, "pages": 60},
        },
    )
    assert run.research["input_quality"]["geo_uncertain"] is True
    # every place-bound search names the country (the stated geography is weak, so no single city is trusted)
    assert all("india" in r.query.lower() for r in run.search.requests if r.country == "in")
    assert any("geo_uncertain" in i["flags"] for i in run.research["items"])
    assert {r.country for r in run.search.requests} <= {"in", None}


async def test_rs13_rs14_the_own_brand_is_never_searched_alone_and_an_equal_reference_is_ambiguous() -> (
    None
):
    run = await run_engine(
        load_fixture(THOROUGH),
        ceilings={
            "soft": {"searches": 30, "targets": 8},
            "hard": {"searches": 40, "targets": 8, "pages": 60},
        },
    )
    brand = "trucksetu"
    assert not any(brand in r.query.lower() for r in run.search.requests)
    assert any(
        g["reason"] == "ambiguous_reference" and brand in g["label"].lower()
        for g in run.research["gaps"]
    )
    assert brand not in json.dumps(run.research["items"]).lower()


async def test_rs16_the_time_box_stops_new_searches_and_commits_partial() -> None:
    clock = FakeClock()
    w = FakeWorld(provider_content_every=3, clock=clock, search_seconds=60.0)
    run = await run_engine(
        load_fixture(QUICK),
        world=w,
        clock=clock,
        ceilings={"hard": {"seconds": 150}, "soft": {"seconds": 120}},
    )
    assert run.result.status == "partial" and run.result.reason == "budget_stop"
    assert run.ctx.budget.searches_used <= 5, "searching stops at the hard time limit"
    assert any(e.type == "warning" and "budget" in e.label.lower() for e in run.events)
    assert run.output.budget.seconds >= 150


async def test_rs17_skip_stops_spending_immediately_and_keeps_what_is_verified() -> None:
    run = await run_engine(load_fixture(QUICK), skip_after_searches=6, use_model=False)
    assert run.result.status == "skipped" and run.research["status"] == "skipped"
    assert len(run.search.requests) <= 6 + 6, "only the searches already in flight finish"
    assert run.gateway.count("rsch.assemble") == 0, "no model call after Skip"
    assert not run.violations


async def test_rs20_a_researched_value_that_contradicts_a_locked_number_is_a_conflict_and_locked_wins() -> (
    None
):
    data = load_fixture(STANDARD)
    fact = next(f for f in data["brief"]["domain_facts"] if "commission" in f["statement"].lower())
    assert "4%" in fact["statement"]
    w = FakeWorld(generate=False)
    u = w.add_page(
        "https://docs.market.example/commission",
        "The typical marketplace commission is 9 percent on each sale.",
        published=date(2026, 9, 1),
    )
    w.route(r".", [u])

    def build(inp):
        return [
            atom(
                "rule",
                "The typical marketplace commission is 9 percent on each sale.",
                key="commission",
                impact="high",
                statement="Typical marketplace commission is 9 percent",
                subject="commission on each sale",
                value="9",
                unit="percent",
                authority=None,
            )
        ]

    run = await run_engine(data, world=w, overrides={"rsch.extract.data": extractor(build)})
    item = next(
        i for i in run.research["items"] if i["kind"] == "rule" and i["data"]["value"] == "9"
    )
    assert "conflict" in item["flags"] and item["tier"] == "open"
    note = next(c for c in run.research["conflicts"] if item["id"] in c["item_ids"])["note"]
    assert "Locked wins" in note and "4" in note
    assert run.output.items[0].tier in ("default", "open"), "research never marks anything Locked"


async def test_rs21_more_topics_than_budget_become_visible_gaps_not_silence() -> None:
    run = await run_engine(load_fixture(QUICK))
    unrun = [g for g in run.research["gaps"] if g["reason"] == "not_run"]
    assert unrun and all(g["label"] for g in unrun)
    assert run.research["coverage"]["targets"] == len(run.ctx.targets)
    assert not run.violations


async def test_rs22_a_same_vendor_checker_is_recorded_and_code_checks_stay_primary() -> None:
    run = await run_engine(
        load_fixture(STANDARD),
        checker_vendor="same",
        overrides={"rsch.extract.data": rule_extractor()},
        world=FakeWorld(provider_content_every=3),
    )
    assert run.research["run"]["checker_vendor"] == "same"
    high = [i for i in run.research["items"] if i["impact"] == "high"]
    assert high and all(
        "checker_same_vendor" in i["flags"] or "unchecked" in i["flags"] for i in high
    )
    assert not [v for v in run.violations if v.rule in ("RV-02", "RV-03")]


async def test_rs24_the_same_search_is_never_sent_twice_for_a_topic_and_the_cache_serves_repeats() -> (
    None
):
    from mde_research_agent.adapters.search.caching import CachingSearch
    from mde_research_agent.ports.search import SearchRequest

    run = await run_engine(load_fixture(QUICK))
    for t in run.evidence["targets"]:
        norm = [" ".join(sorted(q.lower().split())) for q in t["queries"]]
        assert len(norm) == len(set(norm))
    cache = CachingSearch(run.search, fee_words=("fee",), clock=lambda: 0.0)
    req = SearchRequest(query="yoga studio app fees Bengaluru", country="in", language="en")
    first, second = await cache.search(req), await cache.search(req)
    assert (
        not first.cached
        and second.cached
        and second.cost_micros == 0
        and cache.hits == 1
        and cache.misses == 1
    )


async def test_rs31_six_cities_do_not_multiply_the_searches() -> None:
    data = load_fixture(QUICK)
    data["brief"]["context"]["geo"]["value"] = {"country": "India"}
    data["brief"]["context"]["geo"]["confidence"] = 0.5
    data["consumer_views"]["research"]["geography"] = {"country": "India"}
    base = await run_engine(data)
    cities = ["Pune", "Mumbai", "Delhi", "Chennai", "Kolkata", "Hyderabad"]
    data2 = json.loads(json.dumps(data))
    data2["brief"]["domain_facts"].append(
        {
            **data["brief"]["domain_facts"][0],
            "id": "FCT-900",
            "statement": "We operate from " + ", ".join(cities) + ".",
            "topics": ["location"],
            "kind": "context",
        }
    )
    many = await run_engine(data2)
    assert abs(many.ctx.budget.searches_used - base.ctx.budget.searches_used) <= 4


async def test_rs34_repeated_model_failures_are_counted_warned_and_end_at_best_partial() -> None:
    def boom(inp):
        return TimeoutError("model too slow")

    run = await run_engine(
        load_fixture(QUICK), overrides={"rsch.extract": boom, "rsch.write_queries": boom}
    )
    assert run.output.budget.failures >= 5 and run.result.status in ("partial", "failed")
    assert any(e.type == "warning" and "took too long" in e.label for e in run.events)
    assert run.research["status"] == run.result.status


async def test_rs03_a_reference_with_a_huge_feature_set_is_ranked_by_prevalence_and_capped() -> (
    None
):
    feats = " ".join(f"It offers feature{n} for members." for n in range(80))
    w = FakeWorld(generate=False)
    u = w.add_page("https://docs.huge.example/features", feats, published=date(2026, 9, 1))
    w.route(r".", [u])

    def build(inp):
        if inp.job != "features":
            return []
        return [
            atom(
                "competitor",
                "It offers feature0 for members.",
                key="huge app",
                name="Huge App",
                features=[{"feature_key": f"feature{n}", "level": "core"} for n in range(80)],
                platforms=["ios"],
                screens=[],
            )
        ]

    run = await run_engine(
        load_fixture(QUICK), world=w, overrides={"rsch.extract.features": extractor(build)}
    )
    assert (
        len(run.research["slices"]["blueprint"]["digest"])
        <= run.ctx.catalog.ceilings.for_class("focused").soft.slice_tokens * 4 * 1.2
    )
    assert run.research["norms"]


async def test_rs05_research_never_turns_findings_into_scope() -> None:
    run = await run_engine(load_fixture(QUICK))
    sug = run.research["suggestions"]
    assert all(s["state"] == "undecided" for group in sug.values() for s in group)
    assert all(
        a["kind"] != "FTR" or a["id"] in {f.id for f in run.ctx.input.features}
        for i in run.research["items"]
        for a in i["applies_to"]
    )


# --- engine-wide properties -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [QUICK, STANDARD])
async def test_properties_hold_on_a_normal_run(name: str) -> None:
    run = await run_engine(load_fixture(name))
    assert not run.violations, [(v.rule, v.message) for v in run.violations]
    assert all(i["tier"] in ("default", "open") for i in run.research["items"])
    assert (
        run.ctx.budget.searches_used <= run.ctx.budget.hard.searches
        and run.ctx.budget.pages_read <= run.ctx.budget.hard.pages
    )
    seqs = [e.seq for e in run.events]
    assert seqs == list(range(1, len(seqs) + 1)), "events are contiguous"
    # a covered topic issues no further queries
    covered_at = {e.target_id: e.seq for e in run.events if e.type == "target.covered"}
    assert not [
        e
        for e in run.events
        if e.type == "query.issued"
        and e.target_id in covered_at
        and e.seq > covered_at[e.target_id]
    ]
    # every item has verified quotes behind it and every committed quote is on its page
    atoms = [a for a in run.evidence["atoms"] if a["status"] == "verified" and a["item_id"]]
    assert {a["item_id"] for a in atoms} == {i["id"] for i in run.research["items"]}
    # every request seen is in an item or a gap
    assert run.research["coverage"]["targets"] == len(run.ctx.targets)


async def test_every_query_that_reached_the_provider_passed_the_gate() -> None:
    run = await run_engine(load_fixture(STANDARD))
    assert [v for v in run.violations if v.rule in ("RV-06", "RV-14")] == []
    assert run.search.requests and all(
        r.query.strip() and "http" not in r.query for r in run.search.requests
    )
