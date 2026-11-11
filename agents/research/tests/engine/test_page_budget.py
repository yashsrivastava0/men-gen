"""The page budget: a page that cannot be read is refunded, a spare takes its place, and a run whose pages are used up
says so and stops paying for rounds nobody can read (Small run 2026-11-11: 40 of 40 pages at second 27, a starved
payments topic, seven round-two searches that read nothing, and a run still called complete)."""

from support.fakes import FakeWorld
from support.testing import load_fixture, run_engine

from mde_core.clock import SystemClock
from mde_research_agent.catalog.models import Ceiling, HardLimits
from mde_research_agent.engine.budget import PAGE_ATTEMPT_FACTOR, RunBudget

QUICK = "quick-yoga-studio-bengaluru"


def _budget(pages: int) -> RunBudget:
    soft = Ceiling(
        searches=20,
        pages=pages,
        seconds=180,
        rounds=2,
        lanes_search=6,
        lanes_extract=8,
        targets=10,
        followups_per_topic=2,
    )
    hard = HardLimits(searches=32, pages=pages, seconds=330, rounds=3, followups_per_topic=3)
    return RunBudget(soft, hard, SystemClock())


def test_a_refunded_page_does_not_use_up_the_ceiling() -> None:
    b = _budget(2)
    assert b.try_page() and b.try_page() and not b.try_page(), "two pages, then the ceiling"
    b.refund_page()  # one of them could not be read
    assert b.pages_read == 1 and b.pages_left() == 1 and b.try_page()
    assert b.pages_exhausted()


def test_refunds_cannot_make_the_loop_endless() -> None:
    b = _budget(4)
    admitted = 0
    while b.try_page():
        admitted += 1
        b.refund_page()  # every page fails
        assert admitted <= 4 * 2
    assert admitted == int(4 * PAGE_ATTEMPT_FACTOR) and b.pages_exhausted()


def test_used_up_pages_are_not_a_run_wide_stop() -> None:
    b = _budget(1)
    assert b.try_page() and not b.try_page()
    assert b.pages_exhausted() and b.exhausted() is None, "searches, time and money still have room"


async def test_unreadable_pages_are_refunded_and_never_fetched_twice() -> None:
    w = FakeWorld(provider_content_every=0)
    run = await run_engine(load_fixture(QUICK), world=w)
    skipped = [e for e in run.events if e.type == "page.skipped"]
    # whatever the fake web refused, the ceiling counts only pages that were read
    assert run.ctx.budget.pages_read == len(run.ctx.pages)
    assert len(run.fetch.fetched) == len(set(run.fetch.fetched)), "no URL is fetched twice"
    assert run.ctx.budget.pages_read <= run.ctx.budget.hard.pages
    assert all(e.detail["reason"] for e in skipped)


async def test_a_spare_takes_the_place_of_a_page_that_cannot_be_read() -> None:
    w = FakeWorld(generate=False)
    good = [
        w.add_page(
            f"https://docs.good{i}.example/apps",
            "Most apps of this kind let members book classes, join a waitlist and pay by card.",
        )
        for i in range(8)
    ]
    walled = [
        w.add_page(f"https://www.walled{i}.example/post", "x", reason="no_content")
        for i in range(3)
    ]
    # the three unreadable pages rank first for every query, the readable ones follow
    w.route(r".", [*walled, *good])
    run = await run_engine(load_fixture(QUICK), world=w)
    per_topic = {}
    for e in run.events:
        if e.type == "page.read" and e.round == 1:
            per_topic[e.target_id] = per_topic.get(e.target_id, 0) + 1
    assert any(e.type == "page.skipped" for e in run.events)
    cap = run.ctx.catalog.ceilings.pages_per_topic.max
    # topics still read up to the usual number of pages although the first picks were unreadable
    assert max(per_topic.values()) >= cap - 1, per_topic
    assert run.ctx.budget.pages_read == len(run.ctx.pages)


async def test_walled_social_hosts_are_not_fetched_without_provider_text() -> None:
    w = FakeWorld(generate=False)
    social = [
        w.add_page("https://www.facebook.com/a/posts/1", "yoga", reason="robots"),
        w.add_page("https://www.instagram.com/reel/2", "yoga", reason="robots"),
    ]
    good = [
        w.add_page(
            f"https://docs.good{i}.example/apps",
            "Most apps of this kind let members book classes, join a waitlist and pay by card.",
        )
        for i in range(5)
    ]
    w.route(r".", [*social, *good])
    run = await run_engine(load_fixture(QUICK), world=w)
    assert not any("facebook.com" in u or "instagram.com" in u for u in run.fetch.fetched)


async def test_a_run_that_runs_out_of_pages_is_partial_says_so_and_pays_for_no_blind_round() -> (
    None
):
    run = await run_engine(
        load_fixture(QUICK),
        ceilings={"soft": {"pages": 6}, "hard": {"pages": 6}},
    )
    b = run.ctx.budget
    assert b.pages_read == 6 and run.ctx.pages_refused > 0
    assert run.result.status == "partial" and run.result.reason == "budget_stop"
    warnings = [e for e in run.events if e.type == "warning"]
    assert any(e.detail.get("code") == "pages_exhausted" for e in warnings)
    refused = [
        ts
        for ts in run.ctx.targets.values()
        if ts.reason == "budget_stop" and ts.status != "covered"
    ]
    assert refused and all(ts.status == "partial" for ts in refused), (
        "not 'not_found': it never got to read"
    )
    assert not any(e.type == "gap.detected" for e in run.events), (
        "no round two without pages to read"
    )


async def test_follow_up_entities_are_the_topics_own_and_a_features_topics_competitors() -> None:
    from mde_research_agent.engine.director import followup_entities
    from mde_research_agent.engine.state import EntityRecord

    run = await run_engine(load_fixture(QUICK), use_model=False)
    ctx = run.ctx
    by_job = {ts.target.job: ts.target for ts in ctx.targets.values()}
    services, features = by_job["services"], by_job["features"]
    ctx.entities.clear()
    ctx.entities["a"] = EntityRecord("Alpha", "provider", target_ids={services.id})
    ctx.entities["b"] = EntityRecord("Beta", "provider", target_ids={"T-99"})
    ctx.entities["c"] = EntityRecord("Gamma", "competitor", target_ids={"T-98"})
    assert [e.name for e in followup_entities(ctx, services)] == ["Alpha"]
    assert [e.name for e in followup_entities(ctx, features)] == ["Gamma"]
