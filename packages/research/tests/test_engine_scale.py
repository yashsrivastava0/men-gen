"""The Standard and Deep ceilings are reachable (D-078): per-job cap, planner, gap-fill reserve.

Before D-078 a plan could not exceed ten searches (two queries for each of five web jobs). Now the
per-job number is the depth cap spread over the web jobs that apply (one rule,
``plan.queries_per_job``), the planner has enough distinct query templates to fill it, and a gap
search keeps room for the re-synthesis.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, cast

import pytest
from pydantic import BaseModel
from research_helpers import (
    DirectMockGateway,
    RecordedCall,
    ScriptedGateway,
    full_slots,
    make_context,
    make_engine,
    make_extras_request,
    make_plan,
    make_policy,
    make_request,
    make_result,
    make_slots,
    make_status,
)

from mde_contracts.calls import C6Input, C8Input, C8Output, S1Input
from mde_contracts.gateway import BudgetStatus, CallContext, CallResult, CallSpec, FreeTextResult
from mde_core.errors import BudgetExceeded
from mde_prompts.calls.c6_plan_research.plan import queries_per_job, web_jobs
from mde_research import ResearchConfig
from mde_research.crosscheck import JOB_ORDER
from mde_research.safety import clean_query

ARCHETYPES = ["commerce", "food_delivery", "booking_services", "internal_workforce", "custom"]
CAPS = {"light": 6, "standard": 12, "deep": 20}
GAPS = [f"open question number {i} about shops" for i in range(8)]


def all_jobs_request(depth: str, archetype: str = "commerce", **policy: object) -> Any:
    """Every web job applies: two compliance flags, a service, a reference app and a website."""
    return make_extras_request(
        archetype=archetype,
        depth=depth,
        slots=full_slots(archetype),
        references=["Rival App"],
        brand_url="https://brand.example/",
        policy=make_policy(**policy),  # type: ignore[arg-type]
    )


def no_names_request(depth: str, archetype: str = "commerce", slots: Any = None) -> Any:
    """The same Brief under a policy that bars company names: R2 and R3 drop out."""
    return make_extras_request(
        archetype=archetype,
        depth=depth,
        slots=slots or full_slots(archetype),
        references=["Rival App"],
        brand_url="https://brand.example/",
        policy=make_policy(allow_company_name=False),
    )


def searches(gw: DirectMockGateway | ScriptedGateway) -> list[S1Input]:
    return [cast("S1Input", c.input) for c in gw.calls if c.call_id == "S1.web_search"]


def c6_input(gw: DirectMockGateway | ScriptedGateway) -> C6Input:
    (found,) = [c.input for c in gw.calls if c.call_id == "C6.plan_research"]
    assert isinstance(found, C6Input)
    return found


def planned_per_job(
    gw: DirectMockGateway | ScriptedGateway, *, skip_gap: bool = True
) -> Counter[str]:
    purposes = {id(c.input): c.ctx.purpose for c in gw.calls if c.call_id == "S1.web_search"}
    return Counter(
        s.job for s in searches(gw) if not skip_gap or purposes[id(s)] != "research:gap_search"
    )


# --------------------------------------------------------------------------- (a) five web jobs


@pytest.mark.parametrize(("depth", "per_job"), [("standard", 3), ("deep", 4)])
async def test_with_all_five_web_jobs_the_run_reaches_exactly_the_ceiling(
    depth: str, per_job: int
) -> None:
    gw = DirectMockGateway()
    note = await make_engine(gw).run(all_jobs_request(depth), make_context()[0])
    plan = c6_input(gw)
    assert plan.jobs_available == ["R1", "R2", "R3", "R4", "R5"]
    assert plan.max_queries_per_job == per_job
    planned = planned_per_job(gw)
    assert sum(planned.values()) == CAPS[depth]  # exactly the ceiling, not 10
    assert set(planned) == {"R1", "R2", "R3", "R4", "R5"}
    assert max(planned.values()) <= per_job
    assert note.status == "ok"
    expected = CAPS[depth] + (1 if depth == "deep" else 0)  # the C8 mock reports one gap at Deep
    assert note.queries_run == gw.counts["S1.web_search"] == expected


async def test_standard_spreads_twelve_searches_over_five_jobs_by_priority() -> None:
    gw = DirectMockGateway()
    await make_engine(gw).run(all_jobs_request("standard"), make_context()[0])
    # five firsts, five seconds, then the two best-ranked jobs (R1, R4) get a third
    assert dict(planned_per_job(gw)) == {"R1": 3, "R2": 2, "R3": 2, "R4": 3, "R5": 2}


async def test_deep_gives_every_job_four_queries() -> None:
    gw = DirectMockGateway()
    await make_engine(gw).run(all_jobs_request("deep"), make_context()[0])
    assert dict(planned_per_job(gw)) == {"R1": 4, "R2": 4, "R3": 4, "R4": 4, "R5": 4}


@pytest.mark.parametrize("archetype", ARCHETYPES)
async def test_every_archetype_reaches_the_ceilings(archetype: str) -> None:
    for depth in ("standard", "deep"):
        gw = DirectMockGateway()
        await make_engine(gw).run(all_jobs_request(depth, archetype), make_context()[0])
        assert sum(planned_per_job(gw).values()) == CAPS[depth], (archetype, depth)


# --------------------------------------------------------------------------- (b) no company names


@pytest.mark.parametrize(
    ("depth", "per_job", "expected"),
    [
        ("standard", 4, {"R1": 4, "R4": 4, "R5": 4}),
        ("deep", 7, {"R1": 7, "R4": 7, "R5": 6}),  # 21 candidates, the cap of 20 cuts R5's last
    ],
)
async def test_without_company_names_r1_r4_r5_alone_reach_the_ceiling(
    depth: str, per_job: int, expected: dict[str, int]
) -> None:
    gw = DirectMockGateway()
    note = await make_engine(gw).run(no_names_request(depth), make_context()[0])
    plan = c6_input(gw)
    assert plan.jobs_available == ["R1", "R4", "R5"]
    assert plan.policy.allow_company_name_in_queries is False
    assert plan.max_queries_per_job == per_job
    assert dict(planned_per_job(gw)) == expected
    assert sum(expected.values()) == CAPS[depth]
    assert note.status == "ok"
    for s1 in searches(gw):
        assert "rival" not in s1.query.casefold() and "brand.example" not in s1.query.casefold()
        assert s1.job not in {"R2", "R3"}


@pytest.mark.parametrize("depth", ["standard", "deep"])
async def test_one_flag_and_one_service_are_enough_for_the_ceiling_without_names(
    depth: str,
) -> None:
    slots = make_slots(
        "commerce", features=["catalog_browse", "payments"], compliance__flags=["financial_data"]
    )
    gw = DirectMockGateway()
    await make_engine(gw).run(no_names_request(depth, slots=slots), make_context()[0])
    assert sum(planned_per_job(gw).values()) == CAPS[depth]


async def test_the_enterprise_policy_with_connectors_still_counts_only_web_jobs() -> None:
    from mde_content.policies import resolve_policy

    policy = resolve_policy("enterprise-it-services-firm")  # Deep, no names, three connectors
    gw = DirectMockGateway()
    req = make_request(
        archetype="internal_workforce",
        depth="deep",
        slots=full_slots("internal_workforce"),
        policy=policy,
    )
    note = await make_engine(gw).run(req, make_context()[0])
    plan = c6_input(gw)
    assert plan.jobs_available == ["R1", "R4", "R5", "R6"]
    assert plan.max_queries_per_job == 7  # R6 reads documents: three web jobs, not four
    assert sum(planned_per_job(gw).values()) == 20
    assert note.status == "ok"


# --------------------------------------------------------------------------- (c) Light


@pytest.mark.parametrize("archetype", ARCHETYPES)
@pytest.mark.parametrize("names", [True, False])
@pytest.mark.parametrize("rich", [True, False])
async def test_light_never_searches_more_than_six_times(
    archetype: str, names: bool, rich: bool
) -> None:
    slots = full_slots(archetype) if rich else make_slots(archetype)
    req = make_extras_request(
        archetype=archetype,
        depth="light",
        slots=slots,
        references=["Rival App"] if rich else [],
        brand_url="https://brand.example/" if rich else None,
        policy=make_policy(allow_company_name=names),
    )
    gw = DirectMockGateway()
    note = await make_engine(gw).run(req, make_context()[0])
    assert 0 < gw.counts["S1.web_search"] <= 6
    assert note.queries_run == gw.counts["S1.web_search"]
    assert c6_input(gw).max_queries_per_job == 2
    assert max(planned_per_job(gw).values()) <= 2  # Light keeps two per job


async def test_a_light_run_with_few_jobs_keeps_two_queries_per_job() -> None:
    gw = DirectMockGateway()
    await make_engine(gw).run(make_request(depth="light", slots=make_slots()), make_context()[0])
    plan = c6_input(gw)
    assert plan.jobs_available == ["R1", "R5"]
    assert plan.max_queries_per_job == 2  # not ceil(6 / 2): a small Guided brief searches as before
    assert gw.counts["S1.web_search"] == 4


# --------------------------------------------------------------------------- (d) ordering


@pytest.mark.parametrize("cap", range(5, 21))
async def test_the_first_query_of_every_job_still_comes_before_any_second(cap: int) -> None:
    gw = DirectMockGateway()
    config = {"depth_caps": {"light": cap, "standard": cap, "deep": cap}}
    await make_engine(gw, **config).run(all_jobs_request("standard"), make_context()[0])
    counts = planned_per_job(gw)
    assert sum(counts.values()) == cap
    assert set(counts) == {"R1", "R2", "R3", "R4", "R5"}
    assert max(counts.values()) - min(counts.values()) <= 1
    # the jobs that got the extra queries are the best-ranked ones: R1, R4, R5, R2, R3
    ranked = [counts[j] for j in ("R1", "R4", "R5", "R2", "R3")]
    assert ranked == sorted(ranked, reverse=True)


async def test_queries_run_in_job_order_each_job_in_plan_order() -> None:
    gw = DirectMockGateway()
    await make_engine(gw).run(all_jobs_request("deep"), make_context()[0])
    jobs = [s.job for s in searches(gw)[:20]]
    assert [j for j in JOB_ORDER if j in jobs] == list(dict.fromkeys(jobs))  # grouped, R1 first
    assert len({s.query for s in searches(gw)}) == len(searches(gw))


# --------------------------------------------------------------------------- the shared rule


@pytest.mark.parametrize("depth", ["light", "standard", "deep"])
@pytest.mark.parametrize("names", [True, False])
@pytest.mark.parametrize("archetype", ARCHETYPES)
async def test_the_engine_hands_c6_the_number_the_shared_rule_gives(
    depth: str, names: bool, archetype: str
) -> None:
    gw = DirectMockGateway()
    req = make_extras_request(
        archetype=archetype,
        depth=depth,
        slots=full_slots(archetype),
        references=["Rival App"],
        brand_url="https://brand.example/",
        policy=make_policy(allow_company_name=names),
    )
    await make_engine(gw).run(req, make_context()[0])
    plan = c6_input(gw)
    assert plan.max_queries_per_job == queries_per_job(
        CAPS[depth], len(web_jobs(plan.jobs_available))
    )
    assert planned_per_job(gw).most_common(1)[0][1] <= plan.max_queries_per_job


async def test_the_engine_uses_the_configured_depth_cap_for_the_rule() -> None:
    gw = DirectMockGateway()
    config = {"depth_caps": {"light": 6, "standard": 25, "deep": 40}}
    await make_engine(gw, **config).run(all_jobs_request("standard"), make_context()[0])
    assert c6_input(gw).max_queries_per_job == 5  # ceil(25 / 5)


async def test_a_model_plan_is_cut_to_the_per_job_number() -> None:
    long_plan = {
        "R1": [f"r1 distinct question number {i} about shops" for i in range(10)],
        "R4": [f"r4 distinct question number {i} about rules" for i in range(10)],
    }
    gw = ScriptedGateway(fallback=DirectMockGateway())
    gw.script("C6.plan_research", make_plan(long_plan))
    await make_engine(gw).run(all_jobs_request("standard"), make_context()[0])
    assert c6_input(gw).max_queries_per_job == 3
    assert dict(planned_per_job(gw)) == {"R1": 3, "R4": 3}  # three each, not ten


async def test_a_rerun_of_one_job_uses_the_number_its_full_run_had() -> None:
    gw = DirectMockGateway()
    req = all_jobs_request("standard").model_copy(update={"rerun_jobs": ["R4"]})
    await make_engine(gw).run(req, make_context()[0])
    plan = c6_input(gw)
    assert plan.jobs_available == ["R4"]
    assert plan.max_queries_per_job == 3  # as with five jobs, not 8 for the one job refreshed
    assert dict(planned_per_job(gw)) == {"R4": 3}


# --------------------------------------------------------------------------- (g) clean queries


@pytest.mark.parametrize("depth", ["light", "standard", "deep"])
@pytest.mark.parametrize("names", [True, False])
@pytest.mark.parametrize("archetype", ARCHETYPES)
async def test_every_generated_query_passes_the_engines_privacy_and_clean_checks(
    depth: str, names: bool, archetype: str
) -> None:
    cfg = ResearchConfig()
    req = make_extras_request(
        archetype=archetype,
        depth=depth,
        slots=full_slots(archetype),
        references=["Rival App"],
        brand_url="https://brand.example/",
        policy=make_policy(allow_company_name=names),
    )
    gw = DirectMockGateway()
    engine = make_engine(gw)
    report = await engine.run_with_report(req, make_context()[0])
    keys = [" ".join(q.casefold().split()) for q in report.queries]
    assert len(keys) == len(set(keys)), "a query was planned twice"
    privacy = engine._start(req, make_context()[0]).privacy  # pyright: ignore[reportPrivateUsage]
    for query in report.queries:
        assert len(query) <= cfg.query_max_chars
        assert clean_query(query, cfg.query_max_chars) == query  # nothing left to clean
        assert privacy.violations(query) == []
        if not names:
            assert "rival" not in query.casefold() and "brand" not in query.casefold().split()
    assert (
        report.dropped["query_rejected"] == 0
    )  # the engine rejected none of the planner's queries


# --------------------------------------------------------------------------- (e) gap-fill


class CappedGateway(DirectMockGateway):
    """A gateway that enforces a research budget the way the real one does: a call that would take
    the spend over the cap is refused (``BudgetExceeded``); spend is reported per scope."""

    def __init__(self, cap_micros: int, gaps: list[str] | None = None) -> None:
        super().__init__(
            cost_micros={
                "C6.plan_research": 1_500,
                "S1.web_search": 12_000,
                "C7.extract": 1_000,
                "C8.synthesize_research": 4_000,
            }
        )
        self.cap_micros = cap_micros
        self.gaps = gaps
        self.spent = 0
        self.refused: list[str] = []

    async def run(
        self, spec: CallSpec[Any, Any], input: BaseModel, ctx: CallContext
    ) -> CallResult[Any]:
        cost = self.cost_micros.get(spec.id, 0)
        if self.spent + cost > self.cap_micros:
            self.refused.append(spec.id)
            raise BudgetExceeded("research budget")
        result = await super().run(spec, input, ctx)
        self.spent += cost
        if (
            self.gaps
            and isinstance(input, C8Input)
            and not input.gap_round
            and isinstance(result.output, C8Output)
        ):
            result = result.model_copy(
                update={"output": result.output.model_copy(update={"gaps": self.gaps})}
            )
        return result

    async def budget_status(self) -> BudgetStatus:
        return make_status(by_scope={"research:run_TEST": self.spent}, spent=self.spent)


async def test_deep_makes_twenty_planned_searches_plus_at_most_four_gap_searches() -> None:
    gw = CappedGateway(cap_micros=10**9, gaps=GAPS)  # the policy's research cap is raised to match
    req = all_jobs_request("deep", research_budget_usd="0.40", session_budget_usd="0.60")
    note = await make_engine(gw).run(req, make_context()[0])
    purposes = Counter(c.ctx.purpose for c in gw.calls)
    assert purposes["research:search"] == 20
    assert purposes["research:gap_search"] == 4  # eight gaps reported, four searched
    assert gw.counts["C8.synthesize_research"] == 2
    assert note.queries_run == 24
    assert note.status == "ok"


async def test_a_mock_deep_run_makes_the_one_gap_search_the_mock_reports() -> None:
    gw = DirectMockGateway()
    await make_engine(gw).run(all_jobs_request("deep"), make_context()[0])
    purposes = Counter(c.ctx.purpose for c in gw.calls)
    assert (purposes["research:search"], purposes["research:gap_search"]) == (20, 1)


async def test_gap_searches_keep_room_for_the_resynthesis() -> None:
    """After the first synthesis 41,000 micro-dollars remain: two gap searches fit with the 4,000
    reserve (13,000 + 4,000 each); a third would leave 2,000 and starve the second C8 call."""
    gw = CappedGateway(cap_micros=265_500 + 41_000, gaps=GAPS)
    req = all_jobs_request("deep", research_budget_usd="0.3065")
    report = await make_engine(gw).run_with_report(req, make_context()[0])
    purposes = Counter(c.ctx.purpose for c in gw.calls)
    assert purposes["research:search"] == 20
    assert purposes["research:gap_search"] == 2  # the rest of the round was skipped
    assert gw.counts["C8.synthesize_research"] == 2  # the re-synthesis still happened ...
    assert gw.refused == []  # ... and nothing was refused by the budget
    assert "c8_deterministic_suggestions" not in report.fallbacks_used
    assert gw.spent <= gw.cap_micros
    assert report.note.status == "partial"  # gap queries were cut by the budget
    assert report.note.unavailable_reason == "budget"
    assert report.note.suggestions  # the second synthesis produced the suggestions
    assert not any(not s.verified for s in report.note.suggestions if s.default_included)


async def test_without_room_for_the_reserve_no_gap_search_runs() -> None:
    gw = CappedGateway(cap_micros=265_500 + 16_500, gaps=GAPS)  # 13,000 fits, 13,000 + 4,000 not
    req = all_jobs_request("deep", research_budget_usd="0.282")
    report = await make_engine(gw).run_with_report(req, make_context()[0])
    purposes = Counter(c.ctx.purpose for c in gw.calls)
    assert purposes["research:gap_search"] == 0
    assert gw.counts["C8.synthesize_research"] == 1  # nothing new to weigh: no second synthesis
    assert gw.refused == []
    assert report.note.status == "partial" and report.note.unavailable_reason == "budget"
    assert report.note.gaps  # the unanswered questions are listed, not silently dropped
    assert "c8_deterministic_suggestions" not in report.fallbacks_used


class QuietGapGateway(DirectMockGateway):
    """Every gap search comes back empty, as when the web has nothing on the open question."""

    async def run(
        self, spec: CallSpec[Any, Any], input: BaseModel, ctx: CallContext
    ) -> CallResult[Any]:
        if ctx.purpose != "research:gap_search":
            return await super().run(spec, input, ctx)
        self.calls.append(RecordedCall(spec.id, input, ctx))
        return make_result(spec, FreeTextResult(text="", citations=[], search_calls=1))


async def test_a_gap_round_that_finds_nothing_new_makes_no_second_synthesis() -> None:
    gw = QuietGapGateway()
    note = await make_engine(gw).run(all_jobs_request("deep"), make_context()[0])
    assert Counter(c.ctx.purpose for c in gw.calls)["research:gap_search"] == 1
    assert gw.counts["C8.synthesize_research"] == 1  # the first draft stands ...
    assert note.gaps  # ... and its open question stays listed
    assert note.status == "ok"
    assert note.queries_run == 21


# --------------------------------------------------------------------------- budget arithmetic


def test_the_deep_ceiling_fits_the_research_cap_with_room_for_a_few_gap_searches() -> None:
    cfg = ResearchConfig()
    cost = cfg.depth_cap("deep") * cfg.per_query_cost_micros + cfg.overhead_cost_micros
    assert cost == 265_500
    room = 300_000 - cost
    assert room == 34_500
    assert (room - cfg.synthesis_cost_micros) // cfg.per_query_cost_micros == 2
